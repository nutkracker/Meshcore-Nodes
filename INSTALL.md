# Installation & Local Setup

This document explains how to run the MeshCore Nodes Dashboard locally.

---

## Prerequisites

- Python **3.8+**
- Access to a MeshCore backend database
- The provided `proxy.py` file

---

## Step 1: Start a local web server

In a terminal window, navigate to the directory containing the HTML files and run:

```bash
python3 -m http.server 8000
```

This will host the dashboard locally at:

```
http://localhost:8000
```

---

## Step 2: Start the MeshCore proxy

In a **separate terminal window**, run:

```bash
python3 proxy.py
```

This starts a local proxy (default endpoint):

```
http://127.0.0.1:8787/nodes
```

The dashboard uses this proxy to retrieve MeshCore node data.

---

## Step 3: Open the dashboard

Open your browser and navigate to:

```
http://localhost:8000/meshcore-nodes.html
```

(or whichever HTML filename you are using)

---

## Notes

- The dashboard caches **all MeshCore nodes in-browser** for **5 minutes**
- Searches, filters, map interaction and analytics do **not** hit the backend unless:
  - The cache expires, or
  - You click **Refresh**
- This significantly reduces load on the MeshCore backend database

---

## Troubleshooting

- **Blank page**: Ensure both the web server and proxy are running
- **CORS errors**: Make sure you are accessing via `http://localhost:8000`, not opening the file directly
- **No data**: Verify `proxy.py` is running and `/nodes` returns JSON

---

## Disclaimer

This project is **not affiliated with or endorsed by MeshCore**.  
MeshCore is an independent project — see https://meshcore.co.uk
