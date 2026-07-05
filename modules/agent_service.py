# File: ~/.config/local-ai/modules/agent_service.py
"""Multi-agent service used by the HTTP server (server/) behind the web GUI (gui/).

Unlike agent_core.stream_response (which prints tokens to the terminal), this
module exposes streaming as a generator of event dicts so any frontend can
render them. Backends: OpenRouter (primary) with local llama.cpp fallback.
Sessions are persisted as JSON files under <repo>/.sessions/<agent-id>/.
"""
import os
import sys
import json
import time
import uuid
import threading
import urllib.request as urlreq
import urllib.error as urlerr

CFG_DIR = os.path.join(os.path.expanduser("~"), ".config", "local-ai")
sys.path.insert(0, os.path.join(CFG_DIR, "modules"))

from agent_core import extract_stream_content  # noqa: E402
from agent_skills import find_skill_file  # noqa: E402

SESSIONS_DIR = os.path.join(CFG_DIR, ".sessions")
AGENTS_FILE = os.path.join(CFG_DIR, "agents.json")
_write_lock = threading.Lock()


def load_env() -> None:
    """Same zero-dep .env loader contract as ai-agent.py: real env vars win."""
    path = os.path.join(CFG_DIR, ".env")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


# ── Agents ───────────────────────────────────────────────────────────────────

def list_agents() -> list:
    try:
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("agents", [])
    except Exception:
        return [{"id": "chat", "name": "General", "icon": "💬",
                 "model": os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash"),
                 "system": "You are a helpful assistant."}]


def get_agent(agent_id: str) -> dict:
    for a in list_agents():
        if a["id"] == agent_id:
            return a
    return list_agents()[0]


def save_agents(agents: list) -> None:
    """Atomically rewrite agents.json (used by the /team CRUD commands)."""
    with _write_lock:
        tmp = AGENTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"agents": agents}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, AGENTS_FILE)


# ── Sessions ─────────────────────────────────────────────────────────────────

def _session_path(agent_id: str, session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, agent_id, f"{session_id}.json")


def create_session(agent_id: str, title: str = "") -> dict:
    agent = get_agent(agent_id)
    sid = uuid.uuid4().hex[:12]
    session = {
        "id": sid,
        "agent": agent["id"],
        "model": agent["model"],
        "title": title or "New session",
        "created": int(time.time()),
        "updated": int(time.time()),
        "usage": {"in": 0, "out": 0},
        "messages": [],
    }
    _save_session(session)
    return session


def _save_session(session: dict) -> None:
    path = _session_path(session["agent"], session["id"])
    with _write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def get_session(agent_id: str, session_id: str) -> dict or None:
    try:
        with open(_session_path(agent_id, session_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def find_session(session_id: str) -> dict or None:
    for a in list_agents():
        s = get_session(a["id"], session_id)
        if s:
            return s
    return None


def delete_session(agent_id: str, session_id: str) -> bool:
    try:
        os.remove(_session_path(agent_id, session_id))
        return True
    except Exception:
        return False


def list_sessions(agent_id: str = "") -> list:
    out = []
    agents = [get_agent(agent_id)] if agent_id else list_agents()
    for a in agents:
        d = os.path.join(SESSIONS_DIR, a["id"])
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                    s = json.load(f)
                out.append({k: s[k] for k in
                            ("id", "agent", "model", "title", "created", "updated", "usage")})
            except Exception:
                continue
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


# ── Streaming chat ───────────────────────────────────────────────────────────

def _backends(model: str) -> list:
    """(url, headers, model, timeout) candidates, primary first."""
    out = []
    okey = os.environ.get("OPENROUTER_API_KEY")
    if okey:
        out.append((
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {okey}",
             "HTTP-Referer": "https://github.com/suyadnya/local-ai"},
            model,
            180,
        ))
    out.append(("http://localhost:8080/v1/chat/completions", {}, "local-model", 180))
    return out


def _split_system(messages):
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"], messages[1:]
    return "", messages


def _cli_prompt(convo: list) -> str:
    """The CLIs are stateless, so prior turns are replayed inline."""
    history = "\n\n".join(
        ("User: " if m["role"] == "user" else "Assistant: ") + m["content"]
        for m in convo[:-1]
    )
    prompt = convo[-1]["content"]
    if history:
        prompt = f"### Prior conversation:\n{history}\n\n### Current message:\n{prompt}"
    return prompt


def _stream_claude_cli(messages: list, model: str):
    """Token generator over the Claude Code CLI (claude.ai subscription login)."""
    import shutil
    import subprocess
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not installed")
    system_prompt, convo = _split_system(messages)
    cmd = [
        claude_bin, "-p",
        "--model", model or "sonnet",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        # Chat only: block agentic tools so it can't read files or run commands
        "--disallowed-tools", "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit",
    ]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    try:
        proc.stdin.write(_cli_prompt(convo))
        proc.stdin.close()
        got, result_text = False, None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("type") == "stream_event":
                delta = data.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    got = True
                    yield delta["text"]
            elif data.get("type") == "result":
                result_text = data.get("result")
        proc.wait(timeout=30)
        if not got and result_text:
            yield result_text
    finally:
        if proc.poll() is None:
            proc.kill()


def _stream_codex_cli(messages: list, model: str):
    """Answer generator over the OpenAI Codex CLI (ChatGPT login). The CLI has
    no token streaming in exec mode, so the reply arrives as one chunk."""
    import shutil
    import subprocess
    import tempfile
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("codex CLI not installed")
    system_prompt, convo = _split_system(messages)
    prompt = (f"### Instructions:\n{system_prompt}\n\n" if system_prompt else "") + _cli_prompt(convo)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.close()
    cmd = [codex_bin, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
           "--output-last-message", tmp.name]
    if model:
        cmd += ["-m", model]
    effort = os.environ.get("CODEX_EFFORT")
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    cmd.append(prompt)
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        with open(tmp.name, "r", encoding="utf-8") as f:
            ans = f.read().strip()
        if ans:
            yield ans
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def stream_chat(session: dict, user_text: str):
    """Yields event dicts: {"type":"token","text":..}, then {"type":"done",...}
    (or {"type":"error","message":..} if every backend failed).
    The session file is updated with both messages on success."""
    agent = get_agent(session["agent"])
    system_prompt = agent["system"]
    # Optional "skills": ["caveman", ...] — skill file bodies join the prompt
    for sk in agent.get("skills") or []:
        path = find_skill_file(os.path.join(CFG_DIR, "skills"), sk)
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    system_prompt += "\n\n" + f.read().strip()
            except Exception:
                pass
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in session["messages"]]
    messages.append({"role": "user", "content": user_text})

    acc = []
    usage = {}
    errs = []

    backend = agent.get("backend", "openrouter")
    if backend in ("claude", "codex"):
        cli = _stream_claude_cli if backend == "claude" else _stream_codex_cli
        try:
            for text in cli(messages, session.get("model") or agent.get("model", "")):
                acc.append(text)
                yield {"type": "token", "text": text}
        except Exception as e:
            errs.append(f"{backend} CLI: {e}")
        if acc:
            # Subscription CLIs don't report token counts — estimate at ~4 chars/token
            usage = {"prompt_tokens": sum(len(m["content"]) for m in messages) // 4,
                     "completion_tokens": len("".join(acc)) // 4}
        else:
            yield {"type": "error", "message": "; ".join(errs) or f"{backend} CLI returned nothing"}
            return

    # Optional "mcp": ["server", ...] — tool-calling loop (OpenRouter backend).
    # Non-streaming rounds: model may call MCP tools, results go back, repeat
    # until it answers in plain text. Tool activity is surfaced as ∗ lines.
    mcp_servers = agent.get("mcp") or []
    if not acc and mcp_servers and backend == "openrouter":
        okey = os.environ.get("OPENROUTER_API_KEY")
        try:
            import mcp_client
            tools = mcp_client.openai_tools(mcp_servers)
        except Exception as e:
            yield {"type": "error", "message": f"MCP: {e}"}
            return
        if not okey:
            yield {"type": "error", "message": "MCP agents need OPENROUTER_API_KEY"}
            return
        convo = list(messages)
        model = session.get("model") or agent["model"]
        for _round in range(6):
            body = {"model": model, "messages": convo, "tools": tools,
                    "usage": {"include": True}}
            req = urlreq.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {okey}",
                         "HTTP-Referer": "https://github.com/suyadnya/local-ai"},
                method="POST")
            try:
                with urlreq.urlopen(req, timeout=180) as r:
                    resp = json.loads(r.read().decode("utf-8"))
            except urlerr.HTTPError as e:
                try:
                    detail = e.read(300).decode("utf-8", errors="ignore")
                except Exception:
                    detail = ""
                errs.append(f"HTTP {e.code} from openrouter.ai: {detail}")
                break
            except Exception as e:
                errs.append(f"openrouter.ai: {e}")
                break
            u = resp.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens"):
                usage[k] = usage.get(k, 0) + (u.get(k) or 0)
            usage["cost"] = usage.get("cost", 0) + (u.get("cost") or 0)
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls")
            if not calls:
                text = msg.get("content") or ""
                if text:
                    acc.append(text)
                    yield {"type": "token", "text": text}
                break
            convo.append(msg)
            for tc in calls:
                fname = tc.get("function", {}).get("name", "")
                srv, _, tool = fname.partition("__")
                try:
                    args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                except Exception:
                    args = {}
                yield {"type": "token",
                       "text": f"\n∗ {srv}.{tool} {json.dumps(args, ensure_ascii=False)[:140]}\n"}
                try:
                    result = mcp_client.call_tool(srv, tool, args)
                except Exception as e:
                    result = f"[tool error] {e}"
                convo.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                              "content": result[:20000]})
        if not acc:
            yield {"type": "error", "message": "; ".join(errs) or "MCP agent returned nothing"}
            return

    for url, headers, model, timeout in ([] if acc else _backends(session.get("model") or agent["model"])):
        body = {"model": model, "messages": messages, "stream": True,
                "usage": {"include": True}}
        req = urlreq.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urlreq.urlopen(req, timeout=timeout) as response:
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    content = extract_stream_content(line)
                    if content:
                        acc.append(content)
                        yield {"type": "token", "text": content}
                    elif b'"usage"' in line:
                        try:
                            u = json.loads(line[5:].decode("utf-8")).get("usage") or {}
                            if u:
                                usage = u
                        except Exception:
                            pass
            if acc:
                break
            errs.append(f"empty response from {url}")
        except urlerr.HTTPError as e:
            try:
                detail = e.read(300).decode("utf-8", errors="ignore")
            except Exception:
                detail = ""
            errs.append(f"HTTP {e.code} from {url.split('/')[2]}: {detail}")
        except Exception as e:
            errs.append(f"{url.split('/')[2]}: {e}")

    if not acc:
        # All backends failed — show every error, primary first, so a dead
        # localhost fallback can't mask the real (e.g. OpenRouter 400) cause
        yield {"type": "error", "message": "; ".join(errs) or "all backends failed"}
        return

    answer = "".join(acc)
    now = int(time.time())
    session["messages"].append({"role": "user", "content": user_text, "ts": now})
    session["messages"].append({"role": "assistant", "content": answer, "ts": now})
    session["updated"] = now
    session["usage"]["in"] += usage.get("prompt_tokens", 0)
    session["usage"]["out"] += usage.get("completion_tokens", 0)
    if session["title"] == "New session" and user_text:
        session["title"] = user_text.strip().replace("\n", " ")[:48]
    _save_session(session)
    yield {"type": "done", "usage": session["usage"], "title": session["title"],
           "cost": usage.get("cost", 0)}


load_env()
