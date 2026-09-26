# HTTP/2 and HTTP/3 Implementation Study Report

**Project:** LLM Council Plus  
**Scope:** Backend (FastAPI on port 8001), frontend (Vite/React on 5173), SSE streaming  
**Date:** February 2025

---

## 1. Quick Technical Analysis: Broad Implementation Steps

### 1.1 Current Stack Summary

| Layer        | Technology              | Protocol   | Notes                                      |
|-------------|-------------------------|------------|--------------------------------------------|
| Backend     | FastAPI + Uvicorn       | HTTP/1.1   | ASGI; `uvicorn.run(app, host="0.0.0.0", port=8001)` |
| Frontend    | Vite + React            | HTTP/1.1   | `fetch()` + `ReadableStream` for SSE       |
| Key traffic | REST + long-lived SSE   | —          | `/api/runs/{id}/stream`, many REST calls   |

Uvicorn does **not** support HTTP/2 or HTTP/3. To add HTTP/2 or HTTP/3 you must either switch the ASGI server or put a reverse proxy in front that terminates HTTP/2 or HTTP/3 and speaks HTTP/1.1 to the app.

---

### 1.2 Option A: HTTP/2 or HTTP/3 at the Application Server

**Broad steps:**

1. **Choose an ASGI server with HTTP/2 (and optionally HTTP/3) support**
   - **Hypercorn**: Supports HTTP/1.1, HTTP/2, and HTTP/3 (QUIC). Best fit for a single server handling all protocols.
   - **Daphne**: HTTP/1.1 + HTTP/2, no HTTP/3.
   - **Granian**: HTTP/2 supported; HTTP/3 in progress.

2. **TLS requirement**
   - HTTP/2 is effectively TLS-only in browsers (ALPN negotiation). HTTP/3 is over QUIC (UDP), also TLS.
   - Obtain or generate certificates (e.g. `cert.pem` / `key.pem`, or Let’s Encrypt).

3. **Configure the server**
   - Bind address and port (e.g. `0.0.0.0:8001`).
   - Enable HTTP/2: e.g. `h2_enabled = True`, `alpn_protocols = ["h2", "http/1.1"]`.
   - For HTTP/3: enable QUIC (e.g. `h3_enabled = True`) and same certs; often a separate UDP port.

4. **Keep the app unchanged**
   - FastAPI and ASGI are protocol-agnostic. No changes to routes, middleware, or SSE endpoints.

5. **Frontend and dev workflow**
   - Use `https://` (or `wss://` if you add WebSockets) in `VITE_API_URL` or `getApiBase()` so the browser connects with ALPN and gets HTTP/2 (or HTTP/3 where supported).
   - Dev: either serve backend over HTTPS (e.g. Hypercorn with certs) or keep HTTP/1.1 for local dev and use HTTP/2 only behind a proxy in staging/production.

6. **Start script and docs**
   - Replace or complement `uv run python -m backend.main` with a Hypercorn (or other) command and document env vars (e.g. cert paths, bind, HTTP/2 vs HTTP/3 flags).

---

### 1.3 Option B: HTTP/2 or HTTP/3 at a Reverse Proxy

**Broad steps:**

1. **Keep Uvicorn as-is**
   - Backend stays HTTP/1.1 on `localhost:8001` (or a unix socket).

2. **Put a reverse proxy in front**
   - **Nginx**: `listen 443 ssl http2` (and optionally `quic` / `reuseport` for HTTP/3).
   - **Caddy**: HTTP/2 and HTTP/3 by default with automatic TLS.
   - **Traefik**: HTTP/2/HTTP/3 via TLS and routing to the backend.

3. **Configure TLS and protocol**
   - Install certs on the proxy; enable `http2 on` (Nginx) or equivalent; for HTTP/3, open UDP (e.g. 443) and enable QUIC.

4. **Proxy to backend**
   - `proxy_pass http://127.0.0.1:8001` (or upstream); preserve `Host`, `X-Forwarded-*`, and ensure long timeouts and no buffering for SSE (`proxy_buffering off`, `X-Accel-Buffering: no`, or equivalent).

5. **Frontend**
   - Point to the proxy’s HTTPS URL so browser uses HTTP/2 or HTTP/3 to the proxy; proxy uses HTTP/1.1 to Uvicorn.

6. **Deployment**
   - Document proxy config, ports (TCP 443, UDP 443 for QUIC if used), and any firewall rules.

---

### 1.4 Summary of Broad Steps (Checklist)

| Step | HTTP/2 (app server) | HTTP/2 (proxy) | HTTP/3 (app server) | HTTP/3 (proxy) |
|------|---------------------|----------------|----------------------|----------------|
| 1. TLS certs | Required | Required | Required | Required |
| 2. Switch server or add proxy | Hypercorn/Daphne/Granian | Nginx/Caddy/Traefik | Hypercorn (QUIC) | Nginx/Caddy/Traefik |
| 3. Enable protocol | `h2`, ALPN | `http2 on` etc. | `h3`, QUIC, UDP port | QUIC on proxy |
| 4. Backend app code | No change | No change | No change | No change |
| 5. Frontend base URL | HTTPS to server | HTTPS to proxy | HTTPS to server | HTTPS to proxy |
| 6. SSE / long-lived | Verify no buffering | Disable buffering for stream | Same as HTTP/2 | Same as HTTP/2 |

---

## 2. Project-Specific Considerations

### 2.1 SSE Streaming

- **Endpoints:** `GET /api/runs/{run_id}/stream`, legacy `POST /api/conversations/{id}/message/stream`.
- **Behavior:** `StreamingResponse` with `media_type="text/event-stream"`, `Cache-Control: no-cache`, `Connection: keep-alive`.
- **Frontend:** `api.streamRun()` uses `fetch()` and `response.body.getReader()` to consume the stream.

**Implications:**

- Any proxy must **not** buffer the response (e.g. Nginx: `proxy_buffering off`; `X-Accel-Buffering: no` for SSE).
- HTTP/2 and HTTP/3 do not change the semantics of SSE; the same endpoint works once the connection is established over H2 or H3.

### 2.2 Many Concurrent Requests

- Stage 1/2: multiple models in parallel; frontend issues several REST calls (conversations, settings, models, runs).
- **HTTP/2 benefit:** Multiplexing over one connection can reduce connection setup and head-of-line blocking for many small requests.

### 2.3 Local and Network Access

- Backend binds `0.0.0.0:8001`; frontend uses `window.location.hostname` so it works on localhost and LAN.
- If you move to HTTPS for HTTP/2/3, you need a way to trust the cert (e.g. local CA, or self-signed with browser exception) for `https://localhost:8001` or `https://<host>:443` via proxy.

### 2.4 Start Script

- `start.sh` runs `uv run python -m backend.main` (which starts Uvicorn). To use Hypercorn (or another server), you’d run e.g. `uv run hypercorn backend.main:app ...` with config and cert paths, and optionally keep a separate “dev” path without TLS.

---

## 3. Recommended Paths for This Project

- **Minimal change, production-like:** Keep Uvicorn; introduce a reverse proxy (e.g. Caddy or Nginx) with TLS and HTTP/2 (and optionally HTTP/3). Proxy to `localhost:8001`; frontend talks to the proxy over HTTPS. No Python dependency change.
- **All-in-one server:** Add Hypercorn as a dependency and an alternative entrypoint (e.g. `backend.main:app` with a Hypercorn config) with TLS, HTTP/2, and optionally HTTP/3. Use this for deployments where you don’t want a separate proxy.
- **Local dev:** Keep current `start.sh` (Uvicorn, HTTP) for simplicity; use HTTP/2 or HTTP/3 only in a dedicated dev script or in staging/production behind a proxy or Hypercorn.

---

## 4. Pros and Cons

### 4.1 HTTP/2

**Pros**

- **Multiplexing:** Many requests over one connection; fewer TCP connections, less overhead for conversation list, settings, models, runs, and polling.
- **Header compression (HPACK):** Smaller headers on repeated requests (e.g. many `/api/...` calls with similar headers).
- **Single connection:** Can reduce latency when the frontend makes several API calls in parallel (e.g. after loading or during a run).
- **Binary framing:** Less parsing overhead and more efficient use of the wire compared to HTTP/1.1.
- **No change to FastAPI or SSE logic:** Same routes and streaming behavior; only server or proxy configuration changes.

**Cons**

- **TLS required** for browser use (ALPN); you must manage certificates and HTTPS in dev and production.
- **Server choice:** Uvicorn cannot do HTTP/2; you must switch to Hypercorn/Daphne/Granian or add a proxy.
- **Debugging:** Binary framing is harder to inspect with simple tools (e.g. raw `curl`); you may need HTTP/2-aware tools or browser devtools.
- **Limited gain for single long-lived stream:** The main SSE stream is one long response; multiplexing helps more for the many REST calls around it than for the stream itself.
- **Head-of-line blocking at TCP:** One lost TCP segment can still delay all streams on that connection (addressed by HTTP/3 over QUIC).

---

### 4.2 HTTP/3 (QUIC)

**Pros**

- **No TCP head-of-line blocking:** Streams are independent; packet loss on one stream doesn’t block others (beneficial for mixed traffic: REST + SSE).
- **Faster connection setup:** QUIC often reduces latency for new connections (0-RTT resume where supported).
- **Better on poor networks:** Can outperform TCP in lossy or variable conditions.
- **Modern default:** Aligns with browser and CDN trends (e.g. HTTP/3 on by default in many environments).

**Cons**

- **Less mature in Python:** Hypercorn’s HTTP/3/QUIC support is still evolving; fewer production reports than HTTP/2.
- **UDP and firewalls:** QUIC uses UDP; some strict or legacy networks block or throttle UDP, which can cause fallback or connectivity issues.
- **Operational complexity:** Two transports (TCP for HTTP/1.1/2, UDP for HTTP/3), and possibly separate ports or proxy config.
- **Debugging and tooling:** QUIC/UDP debugging is harder than TCP; support in proxies and load balancers varies.
- **Certificate and TLS:** Same as HTTP/2 (TLS required); plus you must expose UDP (e.g. 443) and ensure it’s allowed.

---

### 4.3 Summary Table

| Aspect              | HTTP/1.1 (current) | HTTP/2              | HTTP/3                    |
|---------------------|--------------------|---------------------|---------------------------|
| Multiplexing        | No                 | Yes                 | Yes                       |
| TLS required        | No                 | Yes (for browsers)  | Yes                       |
| Server support      | Uvicorn ✓          | Hypercorn, proxy    | Hypercorn, proxy          |
| Head-of-line block  | TCP                | TCP                 | Per-stream (QUIC)         |
| Operational effort  | Lowest             | Medium              | Higher                    |
| Benefit for this app| —                  | High for many REST  | Extra on lossy networks   |

---

*End of report.*
