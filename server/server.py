#!/usr/bin/env python3
# File: ~/.config/orkesai/server/server.py
"""HTTP + SSE API for the OrkesAI multi-agent app (stdlib only).

    python3 server/server.py            # listens on 127.0.0.1:8765

Endpoints
    GET    /api/health                       {ok: true}
    GET    /api/agents                       list configured agents
    POST   /api/agents          {…}          create a team agent
    PUT    /api/agents/<id>     {…}          edit a team agent
    DELETE /api/agents/<id>                  delete a team agent
    GET    /api/backends                     backends + their models (picker)
    POST   /api/backends        {backend,model}  add a custom model
    GET    /api/settings                     persisted settings + valid values
    PUT    /api/settings        {key: val}   save settings
    GET    /api/env                          editable .env keys (secrets masked)
    PUT    /api/env             {key: val}   write .env keys
    GET    /api/projects                     list projects (with meta)
    POST   /api/projects        {name,desc}  create a project
    PUT    /api/projects/<name> {…}          edit description/instructions
    DELETE /api/projects/<name>              delete a project
    POST   /api/projects/<name>/files {name,url}   add a file
    DELETE /api/projects/<name>/files/<file>       remove a file
    GET    /api/usage[?range=week|month|year|all]  spend aggregation
    GET    /api/skills · POST · DELETE /api/skills/<name>
    GET    /api/mcp    · POST · DELETE /api/mcp/<name>
    GET    /api/databases                    SQLite files + row counts
    GET    /api/sessions[?agent=<id>]        list sessions (newest first)
    POST   /api/sessions        {agent, project?, backend?, model?}
    GET    /api/sessions/<id>                full session incl. messages
    PUT    /api/sessions/<id>   {…}          patch backend/model/project/title
    DELETE /api/sessions/<id>                delete a session
    GET    /api/notes/export?scope=&fmt=pdf|docx|xlsx|csv[&note=<id>]  download
    POST   /api/chat  {session_id, message, images?, attachments?}  SSE
    POST   /api/confirm  {id, approve}       answer a pending tool confirmation

Consumed by gui/ (Next.js + Electron).
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".config", "orkesai", "modules"))
import agent_service as svc  # noqa: E402
import agent_settings  # noqa: E402
import agent_automations as auto  # noqa: E402

HOST = os.environ.get("AI_SERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_SERVER_PORT", "8765"))


def _unquote(s: str) -> str:
    return urllib.parse.unquote(s)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── plumbing ────────────────────────────────────────────────────────────
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, res):
        """(obj, error) → 200 obj or 400 {error}. Bare bool → {ok}."""
        obj, err = res
        if err:
            return self._json({"error": err}, 400)
        return self._json(obj if obj not in (True, False) else {"ok": bool(obj)})

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
        seg = [s for s in path.split("/") if s]  # ['api', ...]
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/agents":
            return self._json({"agents": svc.list_agents()})
        if path == "/api/backends":
            return self._json({"backends": svc.list_backends()})
        if path == "/api/settings":
            return self._json({
                "settings": svc.gui_settings(),
                "valid_agents": list(agent_settings.VALID_AGENTS),
                "valid_edit": list(agent_settings.VALID_EDIT),
            })
        if path == "/api/env":
            return self._json({"env": svc.read_env_config()})
        if path == "/api/projects":
            return self._json({"projects": svc.list_projects()})
        if path == "/api/usage":
            return self._json(svc.usage_summary(params.get("range", "all")))
        if path == "/api/skills":
            return self._json({"skills": svc.list_skills()})
        if path == "/api/mcp":
            return self._json({"servers": svc.list_mcp()})
        if path == "/api/databases":
            return self._json({"databases": svc.list_databases()})
        if path == "/api/sessions":
            return self._json({"sessions": svc.list_sessions(params.get("agent", ""))})
        if len(seg) == 3 and seg[1] == "agent-files":
            return self._json({"files": svc.list_agent_files(_unquote(seg[2]))})
        if path == "/api/context":
            return self._json(svc.context_view(params.get("agent", "default"), params.get("session", "")))
        if path == "/api/models/openrouter":
            return self._json(svc.openrouter_catalog(params.get("refresh") == "1"))
        if path == "/api/integrations":
            return self._json({"integrations": svc.list_integrations()})
        if path == "/api/automations":
            return self._json({"automations": auto.list_automations()})
        if path == "/api/groups":
            return self._json({"groups": svc.list_groups()})
        if len(seg) == 4 and seg[1] == "automations" and seg[3] == "export":
            return self._ok(auto.export_automation(seg[2]))
        if len(seg) == 3 and seg[1] == "automations":
            a = auto.get_automation(seg[2])
            return self._json(a) if a else self._json({"error": "not found"}, 404)
        if path == "/api/notes/export":
            out, err = svc.export_notes(_unquote(params.get("scope", "")),
                                        _unquote(params.get("fmt", "pdf")),
                                        _unquote(params.get("note", "")))
            if err:
                return self._json({"error": err}, 400)
            data = out["data"]
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", out["mime"])
            self.send_header("Content-Disposition", f'attachment; filename="{out["filename"]}"')
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if len(seg) == 3 and seg[1] == "sessions":
            s = svc.find_session(seg[2])
            return self._json(s) if s else self._json({"error": "not found"}, 404)
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        path, _, query = self.path.partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        seg = [s for s in path.split("/") if s]
        if len(seg) == 3 and seg[1] == "sessions":
            s = svc.find_session(seg[2])
            if s and svc.delete_session(s["agent"], s["id"]):
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        if len(seg) == 3 and seg[1] == "agents":
            return self._ok(svc.delete_agent(seg[2]))
        if len(seg) == 3 and seg[1] == "projects":
            return self._ok(svc.delete_project(_unquote(seg[2])))
        if len(seg) == 5 and seg[1] == "projects" and seg[3] == "files":
            return self._ok(svc.delete_project_file(_unquote(seg[2]), _unquote(seg[4])))
        if len(seg) == 3 and seg[1] == "skills":
            return self._ok(svc.remove_skill(_unquote(seg[2])))
        if len(seg) == 3 and seg[1] == "mcp":
            return self._ok(svc.remove_mcp(_unquote(seg[2])))
        if len(seg) == 3 and seg[1] == "databases":
            return self._ok(svc.delete_database(_unquote(seg[2])))
        if len(seg) == 3 and seg[1] == "integrations":
            return self._ok(svc.delete_integration(seg[2]))
        if len(seg) == 3 and seg[1] == "automations":
            return self._ok(auto.delete_automation(seg[2]))
        if len(seg) == 3 and seg[1] == "groups":
            return self._ok(svc.delete_group(seg[2]))
        if len(seg) == 3 and seg[1] == "notes":
            return self._ok(svc.delete_note(_unquote(params.get("scope", "")), seg[2]))
        if len(seg) == 3 and seg[1] == "links":
            return self._ok(svc.delete_link(_unquote(params.get("scope", "")), seg[2]))
        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        seg = [s for s in self.path.split("/") if s]
        if self.path == "/api/settings":
            err = svc.save_gui_settings(self._body())
            return self._json({"error": err}, 400) if err else self._json({"settings": svc.gui_settings()})
        if self.path == "/api/env":
            err = svc.save_env_config(self._body())
            return self._json({"error": err}, 400) if err else self._json({"env": svc.read_env_config()})
        if len(seg) == 3 and seg[1] == "agents":
            return self._ok(svc.update_agent(seg[2], self._body()))
        if len(seg) == 3 and seg[1] == "projects":
            return self._ok(svc.update_project(_unquote(seg[2]), self._body()))
        if len(seg) == 3 and seg[1] == "sessions":
            return self._ok(svc.update_session(seg[2], self._body()))
        if len(seg) == 3 and seg[1] == "automations":
            return self._ok(auto.update_automation(seg[2], self._body()))
        if len(seg) == 3 and seg[1] == "groups":
            return self._ok(svc.update_group(seg[2], self._body()))
        if self.path == "/api/notes-auto":
            b = self._body()
            return self._ok(svc.set_notes_auto(b.get("scope", ""), bool(b.get("on"))))
        if len(seg) == 3 and seg[1] == "notes":
            b = self._body()
            return self._ok(svc.update_note(b.get("scope", ""), seg[2], b))
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        seg = [s for s in self.path.split("/") if s]
        body = None
        if self.path == "/api/sessions":
            body = self._body()
            return self._json(svc.create_session(body.get("agent", "default"),
                                                 body.get("title", ""),
                                                 body.get("project", ""),
                                                 body.get("backend", ""),
                                                 body.get("model", ""),
                                                 body.get("effort", ""),
                                                 body.get("temperature", ""),
                                                 body.get("max_tokens", 0)), 201)
        if self.path == "/api/agents":
            return self._ok(svc.create_agent(self._body()))
        if self.path == "/api/backends":
            body = self._body()
            err = svc.add_model(body.get("backend", ""), body.get("model", ""))
            return self._json({"error": err}, 400) if err else self._json({"backends": svc.list_backends()})
        if self.path == "/api/projects":
            body = self._body()
            return self._ok(svc.create_project(body.get("name", ""), body.get("description", "")))
        if len(seg) == 4 and seg[1] == "projects" and seg[3] == "files":
            body = self._body()
            return self._ok(svc.add_project_file(_unquote(seg[2]), body.get("name", ""), body.get("url", "")))
        if self.path == "/api/notes":
            body = self._body()
            return self._ok(svc.create_note(body.get("scope", ""), body.get("title", ""), body.get("body", "")))
        if self.path == "/api/notes/summarize":
            body = self._body()
            return self._ok(svc.summarize_to_note(body.get("agent", "default"), body.get("session", "")))
        if self.path == "/api/enhance-prompt":
            body = self._body()
            out, err = svc.enhance_role_prompt(body.get("text", ""), body.get("name", ""), body.get("model", ""))
            return self._json({"error": err}, 400) if err else self._json({"text": out})
        if self.path == "/api/links":
            body = self._body()
            return self._ok(svc.add_link(body.get("scope", ""), body.get("url", ""), body.get("title", "")))
        if self.path == "/api/skills":
            body = self._body()
            return self._ok(svc.add_skill(body.get("name", ""), body.get("source", "")))
        if self.path == "/api/mcp":
            body = self._body()
            return self._ok(svc.add_mcp(body.get("name", ""), body.get("command", "")))
        if self.path == "/api/integrations":
            return self._ok(svc.add_integration(self._body()))
        if self.path == "/api/automations":
            return self._ok(auto.create_automation(self._body()))
        if self.path == "/api/groups":
            return self._ok(svc.create_group(self._body()))
        if self.path == "/api/automations/import":
            return self._ok(auto.import_automation(self._body()))
        if len(seg) == 4 and seg[1] == "automations" and seg[3] == "run":
            b = self._body()
            auto.run_async(seg[2], json.dumps(b) if b else "")
            return self._json({"started": True})
        if len(seg) == 3 and seg[1] == "hooks":
            # external webhook trigger — fire and return fast so callers
            # (GitLab, Zapier, curl) never wait on the agent run
            a = auto.get_automation(seg[2])
            if not a or not a.get("enabled"):
                return self._json({"error": "unknown or disabled automation"}, 404)
            raw = ""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            except Exception:
                raw = ""
            auto.run_async(seg[2], raw)
            return self._json({"ok": True, "started": True})
        if self.path == "/api/confirm":
            body = self._body()
            ok = svc.resolve_confirm(body.get("id", ""), bool(body.get("approve")))
            return self._json({"ok": ok}, 200 if ok else 404)
        if self.path == "/api/chat":
            return self._chat(self._body())
        self._json({"error": "not found"}, 404)

    def _chat(self, body: dict):
        session = svc.find_session(body.get("session_id", ""))
        message = (body.get("message") or "").strip()
        images = [u for u in (body.get("images") or []) if isinstance(u, str)]
        attachments = [a for a in (body.get("attachments") or []) if isinstance(a, dict)]
        if not session or not (message or images or attachments):
            return self._json({"error": "session_id and message required"}, 400)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        stream = svc.stream_group_chat if session.get("group") else svc.stream_chat
        try:
            for ev in stream(session, message, images, attachments):
                payload = json.dumps(ev, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away mid-stream; session file already handles state


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    auto.start_scheduler()
    print(f"OrkesAI server on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
