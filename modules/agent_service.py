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

from agent_core import (extract_stream_content, edit_mode_on, edit_confirm_on,
                        claude_confirm_settings, EDIT_SYSTEM_ADD, READ_SYSTEM_ADD)  # noqa: E402
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
             "HTTP-Referer": "https://github.com/wibawasuyadnya/dotai"},
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
    edit = edit_mode_on()
    workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    cmd = [
        claude_bin, "-p",
        "--model", model or "sonnet",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        # No personal MCP connectors — they bloat every prompt with tool schemas
        "--strict-mcp-config",
    ]
    if edit:
        cmd += ["--permission-mode", "acceptEdits",
                "--allowedTools", "Read,Glob,Grep,Edit,Write,MultiEdit,NotebookEdit,Bash,TodoWrite"]
        if edit_confirm_on():
            # PreToolUse hook asks y/n on the terminal before Edit/Write/Bash
            cmd += ["--settings", claude_confirm_settings()]
        system_prompt = (system_prompt or "") + EDIT_SYSTEM_ADD.format(ws=workspace)
    else:
        # Reads allowed everywhere, shell behind the y/n hook, writes blocked
        cmd += ["--allowedTools", "Read,Glob,Grep,Bash",
                "--disallowed-tools", "Edit,Write,MultiEdit,NotebookEdit,Task,WebFetch,WebSearch,TodoWrite",
                "--settings", claude_confirm_settings()]
        system_prompt = (system_prompt or "") + READ_SYSTEM_ADD
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=workspace)
    try:
        proc.stdin.write(_cli_prompt(convo))
        proc.stdin.close()
        got, result_text, result_is_error = False, None, False
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
            elif data.get("type") == "assistant":
                # Tool calls (Read/Grep/Edit…) surface as ∗ activity lines
                for blk in (data.get("message") or {}).get("content") or []:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        inp = blk.get("input") or {}
                        brief = str(inp.get("file_path") or inp.get("command") or inp.get("pattern") or inp.get("path") or "")[:100]
                        yield f"\n∗ {blk.get('name')} {brief}\n"
            elif data.get("type") == "result":
                result_text = data.get("result")
                result_is_error = bool(data.get("is_error"))
        proc.wait(timeout=600 if edit else 300)
        if not got and result_text:
            # Offline/API failures come back as a result whose text is the
            # error — raise so the caller cascades to the next backend
            if result_is_error or str(result_text).startswith("API Error"):
                raise RuntimeError(str(result_text)[:120])
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
            # CLI down (offline, not logged in, …): cascade to the fallback
            # backends below — the last of which boots the local llama-server
            if not errs:
                errs.append(f"{backend} CLI returned nothing")

    # Tool-calling loop (OpenRouter backend): built-in file/shell tools are
    # always attached (reads free, run_command needs the user's y/n on a
    # terminal, write_file only in edit mode), plus any MCP servers on the
    # agent. Non-streaming rounds: the model calls tools, results go back,
    # repeat until it answers in plain text. Activity is surfaced as ∗ lines.
    mcp_servers = agent.get("mcp") or []
    okey = os.environ.get("OPENROUTER_API_KEY")
    if not acc and backend == "openrouter" and okey:
        from agent_core import _EDIT_TOOLS, _run_edit_tool
        allowed = {"read_file", "list_dir", "run_command"} | ({"write_file"} if edit_mode_on() else set())
        builtin = {t["function"]["name"] for t in _EDIT_TOOLS if t["function"]["name"] in allowed}
        tools = [t for t in _EDIT_TOOLS if t["function"]["name"] in builtin]
        if mcp_servers:
            try:
                import mcp_client
                tools += mcp_client.openai_tools(mcp_servers)
            except Exception as e:
                yield {"type": "error", "message": f"MCP: {e}"}
                return
        workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
        convo = list(messages)
        from agent_core import TOOLS_SYSTEM_ADD
        note = TOOLS_SYSTEM_ADD.format(names=", ".join(sorted(builtin)), ws=workspace)
        if convo and convo[0]["role"] == "system":
            convo[0] = {"role": "system", "content": convo[0]["content"] + note}
        else:
            convo.insert(0, {"role": "system", "content": note.strip()})
        model = session.get("model") or agent["model"]
        for _round in range(6):
            body = {"model": model, "messages": convo, "tools": tools,
                    "usage": {"include": True}}
            req = urlreq.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {okey}",
                         "HTTP-Referer": "https://github.com/wibawasuyadnya/dotai"},
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
                try:
                    args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                except Exception:
                    args = {}
                if fname in builtin:
                    brief = str(args.get("path") or args.get("command") or "")[:120]
                    yield {"type": "token", "text": f"\n∗ {fname} {brief}\n"}
                    try:
                        result = _run_edit_tool(fname, args, workspace)
                    except Exception as e:
                        result = f"[tool error] {e}"
                else:
                    srv, _, tool = fname.partition("__")
                    yield {"type": "token",
                           "text": f"\n∗ {srv}.{tool} {json.dumps(args, ensure_ascii=False)[:140]}\n"}
                    try:
                        result = mcp_client.call_tool(srv, tool, args)
                    except Exception as e:
                        result = f"[tool error] {e}"
                convo.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                              "content": result[:20000]})
        if not acc:
            if mcp_servers:
                yield {"type": "error", "message": "; ".join(errs) or "MCP agent returned nothing"}
                return
            # No MCP attached: let the plain streaming cascade below retry
            errs.append("tool loop returned nothing")

    for url, headers, model, timeout in ([] if acc else _backends(session.get("model") or agent["model"])):
        if url.startswith("http://localhost"):
            # Local fallback (cloud down / offline): boot llama-server on demand
            try:
                from agent_core import ensure_local_server
                if not ensure_local_server():
                    errs.append("local llama-server unavailable")
                    continue
            except Exception:
                pass
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
    # Global spend ledger (same file the main chat and /usage read)
    try:
        import agent_usage
        agent_usage.record(session.get("model") or agent.get("model") or backend,
                           usage.get("prompt_tokens", 0),
                           usage.get("completion_tokens", 0),
                           usage.get("cost", 0) or 0.0)
    except Exception:
        pass
    yield {"type": "done", "usage": session["usage"], "title": session["title"],
           "cost": usage.get("cost", 0)}


# Persisted /settings state (startup agent, edit mode) applies to team agents
# too — real shell env vars still win, exactly like in ai-agent.py
_REAL_ENV_BACKEND = "AI_BACKEND" in os.environ
_REAL_ENV_EDIT = "AI_EDIT_MODE" in os.environ
load_env()
try:
    import agent_settings
    agent_settings.apply_startup(_REAL_ENV_BACKEND, _REAL_ENV_EDIT)
except Exception:
    pass
