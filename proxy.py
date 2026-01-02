#!/usr/bin/env python3
"""
MeshCore Map Proxy (v2)
- Fetches nodes from: https://map.meshcore.dev/api/v1/nodes
- Adds first_seen tracking (persisted to meshcore_seen_state.json)
- Normalizes fields so the frontend can use consistent names:
    name        <- adv_name
    lat/lon     <- adv_lat/adv_lon
    created_at  <- inserted_date
    updated_at  <- updated_date
    last_seen_iso <- updated_date (fallback last_advert)
- CORS enabled: Access-Control-Allow-Origin: *
- Endpoints:
    GET /nodes
    GET /recent?days=7   (filters by first_seen_ms in the last N days)
    GET /health
"""

import json
import os
import ssl
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

UPSTREAM = "https://map.meshcore.dev/api/v1/nodes"
HOST, PORT = "127.0.0.1", 8787

# Store first_seen locally so we can detect "new" nodes over time
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meshcore_seen_state.json")


def make_ssl_context():
    """
    Prefer certifi CA bundle if available (helps on macOS with mixed Python installs).
    Falls back to system default trust store.
    """
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CTX = make_ssl_context()


def now_ms() -> int:
    return int(time.time() * 1000)


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("first_seen"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"first_seen": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


STATE = load_state()


def node_key(n: dict) -> str:
    """
    Stable ID for first_seen tracking.
    In your schema, 'public_key' is the best primary key.
    """
    v = n.get("public_key") or n.get("id") or n.get("node_id") or n.get("nodeId")
    if isinstance(v, str) and v.strip():
        return v.strip()
    # Very rare fallback: stable-ish hash of truncated JSON
    return "fallback:" + json.dumps(n, sort_keys=True)[:200]


def iso_from_any(v):
    """
    Best-effort ISO normalizer:
    - epoch seconds/ms -> ISO
    - ISO string -> returned as-is
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        ms = int(v)
        if ms < 2_000_000_000_000:  # likely seconds
            ms *= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms / 1000))
    if isinstance(v, str):
        return v
    return None


def fetch_upstream_nodes():
    req = Request(UPSTREAM, headers={"User-Agent": "meshcore-local-proxy"})
    with urlopen(req, timeout=25, context=SSL_CTX) as r:
        body = r.read()
        content_type = r.headers.get("Content-Type", "application/json")
        status = r.status

    if status < 200 or status >= 300:
        raise HTTPError(UPSTREAM, status, "Non-2xx from upstream", hdrs=None, fp=None)

    data = json.loads(body.decode("utf-8", errors="replace"))
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        data = data["nodes"]
    if not isinstance(data, list):
        raise ValueError("Unexpected upstream schema (expected list)")

    return data, content_type


def enrich_nodes(nodes: list) -> list:
    """
    Adds first_seen fields + normalizes schema.
    Persists state as needed.
    """
    changed = False
    first_seen_map = STATE["first_seen"]
    now = now_ms()

    enriched = []
    for n in nodes:
        if not isinstance(n, dict):
            continue

        k = node_key(n)
        fs = first_seen_map.get(k)
        if fs is None:
            first_seen_map[k] = now
            fs = now
            changed = True

        nn = dict(n)  # shallow copy
        nn["_key"] = k

        # First seen (local/proxy)
        nn["first_seen_ms"] = int(fs)
        nn["first_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(fs / 1000))

        # Normalize fields from the MeshCore map schema you showed:
        # adv_name, adv_lat, adv_lon, inserted_date, updated_date, last_advert
        if "adv_name" in nn and "name" not in nn:
            nn["name"] = nn.get("adv_name")

        if "adv_lat" in nn and "lat" not in nn:
            nn["lat"] = nn.get("adv_lat")
        if "adv_lon" in nn and "lon" not in nn:
            nn["lon"] = nn.get("adv_lon")

        if "inserted_date" in nn and "created_at" not in nn:
            nn["created_at"] = nn.get("inserted_date")
        if "updated_date" in nn and "updated_at" not in nn:
            nn["updated_at"] = nn.get("updated_date")

        # Provide a consistent last_seen_iso
        nn["last_seen_iso"] = (
            iso_from_any(nn.get("updated_date"))
            or iso_from_any(nn.get("last_advert"))
            or iso_from_any(nn.get("updated_at"))
        )

        enriched.append(nn)

    if changed:
        save_state(STATE)

    return enriched


def filter_recent_by_first_seen(enriched: list, days: int) -> list:
    if days <= 0:
        return enriched
    cutoff = now_ms() - days * 24 * 60 * 60 * 1000
    out = []
    for n in enriched:
        fs = n.get("first_seen_ms")
        if isinstance(fs, int) and fs >= cutoff:
            out.append(n)
    return out


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/health":
                self._send_json({"ok": True})
                return

            if path not in ("/nodes", "/recent"):
                self._send_json({"error": "Not Found. Use /nodes or /recent?days=7"}, status=404)
                return

            nodes, _ct = fetch_upstream_nodes()
            enriched = enrich_nodes(nodes)

            if path == "/recent":
                days = int(qs.get("days", ["7"])[0])
                enriched = filter_recent_by_first_seen(enriched, days)

            self._send_json(enriched)
            return

        except HTTPError as e:
            self._send_json({"error": f"Upstream HTTPError {getattr(e, 'code', 'unknown')}"}, status=502)
        except URLError as e:
            self._send_json({"error": f"Upstream URLError: {getattr(e, 'reason', str(e))}"}, status=502)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)


def main():
    print(f"MeshCore proxy v2 running at http://{HOST}:{PORT}/nodes")
    print(f"First-seen state file: {STATE_FILE}")
    try:
        import certifi  # type: ignore
        print(f"Using certifi CA bundle: {certifi.where()}")
    except Exception:
        print("Using default SSL trust store (no certifi found).")
    HTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
