# File: ~/.config/local-ai/modules/mcp_client.py
"""Minimal MCP (Model Context Protocol) client — stdlib only.

Servers are declared in <repo>/mcp.json:

    {
      "servers": {
        "everything": {"command": ["npx", "-y", "@modelcontextprotocol/server-everything"]},
        "agora":      {"url": "https://example.com/mcp"}
      }
    }

Two transports: stdio (spawned subprocess, newline-delimited JSON-RPC) and
streamable HTTP (POST JSON-RPC; JSON or SSE responses, Mcp-Session-Id kept).
Connections are cached per process. Team agents opt in via `"mcp": ["name"]`
in agents.json — agent_service turns their tools into OpenAI-style functions.
"""
import json
import os
import subprocess
import threading
import urllib.request as urlreq

CFG_DIR = os.path.join(os.path.expanduser("~"), ".config", "local-ai")
MCP_FILE = os.path.join(CFG_DIR, "mcp.json")
PROTOCOL = "2025-03-26"

_clients = {}
_lock = threading.Lock()


def load_servers() -> dict:
    try:
        with open(MCP_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("servers", {})
    except Exception:
        return {}


def save_servers(servers: dict) -> None:
    tmp = MCP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"servers": servers}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, MCP_FILE)


class _StdioClient:
    def __init__(self, command: list):
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._id = 0
        self._io_lock = threading.Lock()
        self._handshake()

    def _rpc(self, method: str, params: dict = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        with self._io_lock:
            if not notify:
                self._id += 1
                msg["id"] = self._id
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            if notify:
                return None
            # Read lines until our response id shows up (skip notifications)
            for _ in range(200):
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP server closed the pipe")
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if data.get("id") == self._id:
                    if "error" in data:
                        raise RuntimeError(data["error"].get("message", "MCP error"))
                    return data.get("result", {})
            raise RuntimeError("no MCP response")

    def _handshake(self):
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dotai", "version": "0.9"},
        })
        self._rpc("notifications/initialized", {}, notify=True)

    def list_tools(self) -> list:
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class _HttpClient:
    def __init__(self, url: str, headers: dict = None):
        self.url = url
        self.extra = headers or {}
        self.session_id = ""
        self._id = 0
        self._handshake()

    def _rpc(self, method: str, params: dict = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
            **self.extra,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urlreq.Request(self.url, data=json.dumps(msg).encode("utf-8"),
                             headers=headers, method="POST")
        with urlreq.urlopen(req, timeout=60) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if notify:
                return None
            body = r.read().decode("utf-8", errors="replace")
            ctype = r.headers.get("Content-Type", "")
        data = None
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        cand = json.loads(line[5:].strip())
                        if cand.get("id") == self._id:
                            data = cand
                    except Exception:
                        continue
        else:
            try:
                data = json.loads(body)
            except Exception:
                pass
        if not data:
            raise RuntimeError("no MCP response")
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "MCP error"))
        return data.get("result", {})

    def _handshake(self):
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dotai", "version": "0.9"},
        })
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except Exception:
            pass  # some servers reject bodyless notifications — non-fatal

    def list_tools(self) -> list:
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def alive(self) -> bool:
        return True

    def close(self):
        pass


def connect(name: str):
    """Cached connection to a configured server (raises on failure)."""
    with _lock:
        cli = _clients.get(name)
        if cli and cli.alive():
            return cli
        cfg = load_servers().get(name)
        if not cfg:
            raise RuntimeError(f"no MCP server '{name}' in mcp.json")
        if cfg.get("url"):
            cli = _HttpClient(cfg["url"], cfg.get("headers"))
        elif cfg.get("command"):
            cli = _StdioClient(cfg["command"])
        else:
            raise RuntimeError(f"server '{name}' needs a \"command\" or \"url\"")
        _clients[name] = cli
        return cli


def list_tools(name: str) -> list:
    return connect(name).list_tools()


def call_tool(name: str, tool: str, arguments: dict) -> str:
    """Calls a tool and flattens the result content to text."""
    res = connect(name).call_tool(tool, arguments)
    parts = []
    for item in res.get("content", []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        else:
            parts.append(json.dumps(item))
    text = "\n".join(p for p in parts if p) or json.dumps(res)
    if res.get("isError"):
        text = f"[tool error] {text}"
    return text


def openai_tools(server_names: list) -> list:
    """MCP tools as OpenAI-style function specs, names prefixed `server__tool`."""
    out = []
    for srv in server_names:
        for t in list_tools(srv):
            out.append({"type": "function", "function": {
                "name": f"{srv}__{t['name']}",
                "description": (t.get("description") or "")[:1024],
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            }})
    return out
