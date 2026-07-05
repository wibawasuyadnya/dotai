#!/usr/bin/env python3
# File: ~/.config/local-ai/server/server.py
"""HTTP + SSE API for the DotAI multi-agent app (stdlib only).

    python3 server/server.py            # listens on 127.0.0.1:8765

Endpoints
    GET    /api/agents                       list configured agents
    GET    /api/sessions[?agent=<id>]        list sessions (newest first)
    POST   /api/sessions        {agent}      create a session
    GET    /api/sessions/<id>                full session incl. messages
    DELETE /api/sessions/<id>                delete a session
    POST   /api/chat  {session_id, message}  SSE stream: token/done/error events
    GET    /api/health                       {ok: true}

Consumed by gui/ (Next.js + Electron).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".config", "local-ai", "modules"))
import agent_service as svc  # noqa: E402

HOST = os.environ.get("AI_SERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_SERVER_PORT", "8765"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── plumbing ────────────────────────────────────────────────────────────
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        sys.stderr.write("[api] %s\n" % (fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/agents":
            return self._json({"agents": svc.list_agents()})
        if path == "/api/sessions":
            return self._json({"sessions": svc.list_sessions(params.get("agent", ""))})
        if path.startswith("/api/sessions/"):
            s = svc.find_session(path.rsplit("/", 1)[1])
            return self._json(s) if s else self._json({"error": "not found"}, 404)
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/sessions/"):
            s = svc.find_session(self.path.rsplit("/", 1)[1])
            if s and svc.delete_session(s["agent"], s["id"]):
                return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/sessions":
            body = self._body()
            return self._json(svc.create_session(body.get("agent", "chat"),
                                                 body.get("title", "")), 201)
        if self.path == "/api/chat":
            return self._chat(self._body())
        self._json({"error": "not found"}, 404)

    def _chat(self, body: dict):
        session = svc.find_session(body.get("session_id", ""))
        message = (body.get("message") or "").strip()
        if not session or not message:
            return self._json({"error": "session_id and message required"}, 400)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # SSE has no known length; close the connection to mark the end
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for ev in svc.stream_chat(session, message):
                payload = json.dumps(ev, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away mid-stream; session file already handles state


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"DotAI server on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
