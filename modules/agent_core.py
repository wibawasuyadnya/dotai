# File: ~/.config/local-ai/modules/agent_core.py
import os
import sys
import re
import json
import time
import urllib.request as urlreq
import urllib.error as urlerr
import requests
import agent_ui as ui

# --- OPTIONAL SPEED-TEST HOOK ---
try:
    import speed_test
except ImportError:
    speed_test = None

# --- OPTIONAL SPEND/USAGE LEDGER HOOK ---
try:
    import agent_usage as usage_log
except ImportError:
    usage_log = None


def _log_turn_usage(model: str, in_tok: int, out_tok: int, cost: float,
                    show_stats: bool, ctx_used: int = None) -> None:
    """Records a finished turn in the spend ledger and, when stats are on,
    prints the dim usage line (tokens, turn cost, today's spend, context left)."""
    if not usage_log:
        return
    try:
        usage_log.record(model, in_tok, out_tok, cost)
        usage_log.refresh_balance_async(min_age=10)
        if show_stats and sys.stdout.isatty():
            ctx_max = None
            if ctx_used is not None:
                try:
                    ctx_max = int(os.environ.get("AI_MAX_TOKENS", 8192))
                except Exception:
                    ctx_max = 8192
            print(usage_log.turn_line(in_tok, out_tok, cost, ctx_used, ctx_max))
    except Exception:
        pass

# --- FAST-PATH BYTE EXTRACTOR ---
def extract_stream_content(line_bytes: bytes) -> str:
    """Performs raw byte-level searching to extract streaming tokens.
    
    Bypasses full-line string decoding and dictionary creation entirely for a major CPU speedup.
    """
    idx = line_bytes.find(b'"content":"')
    if idx == -1:
        idx = line_bytes.find(b'"text":"')
        if idx == -1:
            return ""
        start = idx + 8
    else:
        start = idx + 11

    end = start
    length = len(line_bytes)
    while end < length:
        char = line_bytes[end]
        if char == 34:  # ASCII for double quote '"'
            break
        if char == 92:  # ASCII for backslash '\'
            end += 2    # Skip escaped character
        else:
            end += 1

    try:
        raw_str_bytes = line_bytes[start:end]
        json_str = b'"' + raw_str_bytes + b'"'
        return json.loads(json_str.decode("utf-8", errors="ignore"))
    except Exception:
        return line_bytes[start:end].decode("utf-8", errors="ignore")


# --- CASCADING COMPLETION ENGINES ---
def stream(messages, prefix, gkey, spinner_class, show_stats: bool = True):
    workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    sf = os.path.join(workspace, ".agent", "session.json")
    
    saved_id = None
    if os.path.exists(sf):
        try:
            with open(sf, "r", encoding="utf-8") as f:
                saved_id = json.load(f).get("last_interaction_id")
        except Exception:
            pass

    model = os.environ.get("CLOUD_MODEL", "gemini-3.5-flash")
    body = {"model": model, "input": messages[-1]["content"] if messages else "", "stream": True}
    if messages and messages[0]["role"] == "system":
        body["system_instruction"] = messages[0]["content"]
    if saved_id:
        body["previous_interaction_id"] = saved_id

    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    headers = {"x-goog-api-key": gkey, "Content-Type": "application/json"}
    req = urlreq.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    spinner = spinner_class()

    try:
        spinner.start()
        with urlreq.urlopen(req, timeout=30) as response:
            try:
                cfg_dir = os.path.expanduser("~/.config/local-ai")
                with open(os.path.join(cfg_dir, ".request_log"), "a", encoding="utf-8") as f:
                    f.write(f"{int(time.time())}|gemini-interactions\n")
            except Exception:
                pass
            
            first, acc, resolved_id = True, [], None
            for line in response:
                dec = line.decode("utf-8").strip()
                if not dec:
                    continue
                if dec.startswith("data:"):
                    dec = dec[5:].strip()
                if dec == "[DONE]":
                    continue
                try:
                    data = json.loads(dec)
                    if data.get("event_type") == "interaction.completed":
                        resolved_id = data.get("interaction", {}).get("id")
                    
                    content = ""
                    if data.get("event_type") == "step.delta":
                        delta = data.get("delta", {})
                        content = delta.get("text", "") if delta.get("type") == "text" else delta.get("content", {}).get("text", "")
                    
                    if content:
                        if first:
                            spinner.stop()
                            if sys.stdout.isatty():
                                sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
                                sys.stdout.flush()
                            first = False
                            if speed_test and show_stats:
                                speed_test.start()
                        print(content, end="", flush=True)
                        acc.append(content)
                        if speed_test and show_stats:
                            speed_test.count_token(content)
                except Exception:
                    pass
            print("")
            if speed_test and show_stats:
                speed_test.end()

            # Interactions API sends no usage object — estimate at ~4 chars/token
            ans_text = "".join(acc)
            in_est = (len(body.get("input", "")) + len(body.get("system_instruction", ""))) // 4
            ctx_est = (sum(len(m.get("content", "")) for m in messages) + len(ans_text)) // 4
            _log_turn_usage(model, in_est, len(ans_text) // 4, 0.0, show_stats, ctx_est)

            if resolved_id:
                try:
                    os.makedirs(os.path.dirname(sf), exist_ok=True)
                    with open(sf, "w", encoding="utf-8") as f:
                        json.dump({"last_interaction_id": resolved_id}, f)
                except Exception:
                    pass
            return "".join(acc)
    except urlerr.HTTPError as e:
        spinner.stop()
        if saved_id and e.code in (400, 404):
            try:
                os.remove(sf)
            except Exception:
                pass
        return None
    except Exception:
        spinner.stop()
        return None


EDIT_SYSTEM_ADD = (
    "\n\n### EDIT MODE (overrides any read-only rules above):\n"
    "You are a coding agent with write access to the project at {ws}. "
    "Use your tools to inspect and modify files directly instead of describing changes. "
    "After editing, reply briefly: what you changed, where, and why."
)


def edit_mode_on() -> bool:
    return os.environ.get("AI_EDIT_MODE") == "1"


def stream_claude(messages, prefix, spinner, show_stats: bool = True):
    """Streams a chat turn through the local Claude Code CLI, which authenticates
    with your claude.ai account login (no API key needed). In edit mode the CLI
    runs inside the focused project with file tools enabled."""
    import shutil
    import subprocess
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    system_prompt = ""
    convo = messages
    if messages and messages[0]["role"] == "system":
        system_prompt = messages[0]["content"]
        convo = messages[1:]
    if not convo:
        return None

    # claude -p is stateless, so prior turns are replayed inline in the prompt
    history = "\n\n".join(
        ("User: " if m["role"] == "user" else "Assistant: ") + m["content"]
        for m in convo[:-1]
    )
    prompt = convo[-1]["content"]
    if history:
        prompt = f"### Prior conversation:\n{history}\n\n### Current message:\n{prompt}"

    edit = edit_mode_on()
    workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    cmd = [
        claude_bin, "-p",
        "--model", os.environ.get("CLAUDE_MODEL", "sonnet"),
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    ]
    if edit:
        # Full agent in the focused project: edits auto-approved, shell allowed
        cmd += ["--permission-mode", "acceptEdits",
                "--allowedTools", "Read,Glob,Grep,Edit,Write,MultiEdit,NotebookEdit,Bash,TodoWrite"]
        system_prompt = (system_prompt or "") + EDIT_SYSTEM_ADD.format(ws=workspace)
    else:
        # Chat only: block agentic tools so it can't read files or run commands
        cmd += ["--disallowed-tools", "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit"]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    proc = None
    acc = []
    first = True
    result_usage = {}
    result_cost = 0.0
    try:
        spinner.start()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, cwd=workspace if edit else None)
        proc.stdin.write(prompt)
        proc.stdin.close()
        result_text = None
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
                content = delta.get("text", "") if delta.get("type") == "text_delta" else ""
                if content:
                    if first:
                        spinner.stop()
                        if sys.stdout.isatty():
                            sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
                            sys.stdout.flush()
                        first = False
                        if speed_test and show_stats:
                            speed_test.start()
                    print(content, end="", flush=True)
                    acc.append(content)
                    if speed_test and show_stats:
                        speed_test.count_token(content)
            elif data.get("type") == "assistant" and edit:
                # Surface tool calls (Edit/Write/Bash…) as dim ∗ activity lines
                for blk in (data.get("message") or {}).get("content") or []:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        inp = blk.get("input") or {}
                        brief = str(inp.get("file_path") or inp.get("command") or inp.get("pattern") or inp.get("path") or "")[:100]
                        spinner.stop()
                        print(f"\033[2m∗ {blk.get('name')} {brief}\033[0m")
                        spinner.start()
                        first = True  # next text delta re-clears the spinner row
            elif data.get("type") == "result":
                result_text = data.get("result")
                result_usage = data.get("usage") or {}
                result_cost = data.get("total_cost_usd") or 0.0
        proc.wait(timeout=600 if edit else 30)
        spinner.stop()
        if not acc and result_text:
            if sys.stdout.isatty():
                sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
            print(result_text, end="")
            acc.append(result_text)
        if not acc:
            return None
        print("")
        if speed_test and show_stats:
            speed_test.end()
        ans_text = "".join(acc)
        in_tok = (
            (result_usage.get("input_tokens") or 0)
            + (result_usage.get("cache_read_input_tokens") or 0)
            + (result_usage.get("cache_creation_input_tokens") or 0)
        ) or sum(len(m.get("content", "")) for m in messages) // 4
        out_tok = result_usage.get("output_tokens") or len(ans_text) // 4
        ctx_est = (sum(len(m.get("content", "")) for m in messages) + len(ans_text)) // 4
        _log_turn_usage(f"claude:{os.environ.get('CLAUDE_MODEL', 'sonnet')}",
                        in_tok, out_tok, result_cost, show_stats, ctx_est)
        return ans_text
    except KeyboardInterrupt:
        spinner.stop()
        if proc:
            proc.kill()
        sys.stderr.write("\n\r\x1b[2K\r[sys] Interrupted.\n")
        # Non-None so the caller does not cascade to another backend mid-turn
        return "".join(acc)
    except Exception:
        spinner.stop()
        if proc:
            proc.kill()
        return None


def stream_codex(messages, prefix, spinner, show_stats: bool = True):
    """Runs a chat turn through the OpenAI Codex CLI, which authenticates with
    your ChatGPT account login (no API key needed). Non-streaming: the answer
    is printed once complete."""
    import shutil
    import subprocess
    import tempfile
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return None

    system_prompt = ""
    convo = messages
    if messages and messages[0]["role"] == "system":
        system_prompt = messages[0]["content"]
        convo = messages[1:]
    if not convo:
        return None

    edit = edit_mode_on()
    workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    if edit:
        system_prompt = (system_prompt or "") + EDIT_SYSTEM_ADD.format(ws=workspace)

    parts = []
    if system_prompt:
        parts.append(f"### Instructions:\n{system_prompt}")
    history = "\n\n".join(
        ("User: " if m["role"] == "user" else "Assistant: ") + m["content"]
        for m in convo[:-1]
    )
    if history:
        parts.append(f"### Prior conversation:\n{history}")
    parts.append(f"### Current message:\n{convo[-1]['content']}")
    prompt = "\n\n".join(parts)

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.close()
    cmd = [
        codex_bin, "exec",
        "--sandbox", "workspace-write" if edit else "read-only", "--skip-git-repo-check",
        "--output-last-message", tmp.name,
    ]
    model = os.environ.get("CODEX_MODEL")
    if model:
        cmd += ["-m", model]
    effort = os.environ.get("CODEX_EFFORT")
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    cmd.append(prompt)

    try:
        spinner.start()
        subprocess.run(cmd, capture_output=True, text=True, timeout=600 if edit else 300,
                       cwd=workspace if edit else None)
        spinner.stop()
        with open(tmp.name, "r", encoding="utf-8") as f:
            ans = f.read().strip()
        if not ans:
            return None
        if sys.stdout.isatty():
            sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
        print(ans)
        # Codex exec reports no usage — estimate at ~4 chars/token, no cost
        _log_turn_usage(f"codex:{model or 'default'}", len(prompt) // 4, len(ans) // 4,
                        0.0, show_stats, (len(prompt) + len(ans)) // 4)
        return ans
    except KeyboardInterrupt:
        spinner.stop()
        sys.stderr.write("\n\r\x1b[2K\r[sys] Interrupted.\n")
        # Non-None so the caller does not cascade to another backend mid-turn
        return ""
    except Exception:
        spinner.stop()
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


# --- NATIVE EDIT-MODE TOOL LOOP (OpenRouter / local llama-server) ---------
_EDIT_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file from the project. Returns its content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a text file in the project with the given content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root"},
            "content": {"type": "string", "description": "Full new file content"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files and directories at a path inside the project.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root, '' for the root"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a shell command in the project root. The user must approve it first.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}},
            "required": ["command"]}}},
]


def _safe_path(workspace: str, p: str) -> str:
    """Resolves a tool path and refuses anything outside the project root."""
    root = os.path.realpath(workspace)
    full = os.path.realpath(os.path.join(root, p or ""))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError(f"path escapes the project: {p}")
    return full


def _run_edit_tool(name: str, args: dict, workspace: str) -> str:
    import subprocess
    if name == "read_file":
        full = _safe_path(workspace, args.get("path", ""))
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read(60000)
    if name == "write_file":
        full = _safe_path(workspace, args.get("path", ""))
        content = args.get("content", "")
        os.makedirs(os.path.dirname(full) or workspace, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {args.get('path')}"
    if name == "list_dir":
        full = _safe_path(workspace, args.get("path", ""))
        entries = sorted(os.listdir(full))
        return "\n".join((e + "/" if os.path.isdir(os.path.join(full, e)) else e) for e in entries) or "(empty)"
    if name == "run_command":
        cmd = args.get("command", "")
        if not sys.stdout.isatty() or not ui.confirm_tool(f"$ {cmd}"):
            return "[denied] the user did not approve this command"
        res = subprocess.run(cmd, shell=True, cwd=workspace, capture_output=True, text=True, timeout=120)
        out = (res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")
        return out[:10000] or f"(exit {res.returncode}, no output)"
    return f"[error] unknown tool {name}"


def edit_turn_tools(messages, prefix, spinner, show_stats: bool = True) -> str or None:
    """Edit-mode turn for OpenAI-compatible backends (OpenRouter or the local
    llama-server): non-streaming rounds where the model may call file tools,
    results go back, repeat until it answers in plain text."""
    workspace = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    okey = os.environ.get("OPENROUTER_API_KEY")
    backend = os.environ.get("AI_BACKEND", "").strip().lower()
    if okey and backend != "local":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {okey}", "HTTP-Referer": "https://github.com/suyadnya/local-ai"}
        model = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    else:
        if not ensure_local_server():
            return None
        url, headers, model = "http://localhost:8080/v1/chat/completions", {}, "local-model"

    convo = [dict(m) for m in messages]
    if convo and convo[0]["role"] == "system":
        convo[0]["content"] += EDIT_SYSTEM_ADD.format(ws=workspace)

    total = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    resolved_model = None
    try:
        for _round in range(10):
            body = {"model": model, "messages": convo, "tools": _EDIT_TOOLS}
            if okey and backend != "local":
                body["usage"] = {"include": True}
            req = urlreq.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
            spinner.start()
            try:
                with urlreq.urlopen(req, timeout=180) as r:
                    resp = json.loads(r.read().decode("utf-8"))
            finally:
                spinner.stop()
            u = resp.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens"):
                total[k] += u.get(k) or 0
            total["cost"] += u.get("cost") or 0.0
            resolved_model = resp.get("model") or resolved_model
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls")
            if not calls:
                ans = msg.get("content") or ""
                if not ans:
                    return None
                if sys.stdout.isatty():
                    sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
                print(ans)
                ctx_est = (sum(len(m.get("content") or "") for m in convo) + len(ans)) // 4
                _log_turn_usage(resolved_model or model, total["prompt_tokens"], total["completion_tokens"],
                                total["cost"], show_stats, ctx_est)
                return ans
            convo.append(msg)
            for tc in calls:
                fname = tc.get("function", {}).get("name", "")
                try:
                    args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                except Exception:
                    args = {}
                brief = str(args.get("path") or args.get("command") or "")[:100]
                print(f"\033[2m∗ {fname} {brief}\033[0m")
                try:
                    result = _run_edit_tool(fname, args, workspace)
                except Exception as e:
                    result = f"[tool error] {e}"
                convo.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
        return None
    except urlerr.HTTPError as e:
        spinner.stop()
        try:
            detail = e.read(300).decode("utf-8", errors="ignore")
        except Exception:
            detail = ""
        sys.stderr.write(f"\033[90m[sys] edit tools failed: HTTP {e.code} {detail}\033[0m\n")
        return None
    except KeyboardInterrupt:
        spinner.stop()
        sys.stderr.write("\n\r\x1b[2K\r[sys] Interrupted.\n")
        return ""
    except Exception as e:
        spinner.stop()
        sys.stderr.write(f"\033[90m[sys] edit tools failed: {e}\033[0m\n")
        return None


def ensure_local_server() -> bool:
    """Auto-start llama-server when the local backend is chosen but :8080 is down.

    Launches start-hermes.sh detached (it keeps running after the agent exits,
    so follow-up questions are instant) and waits for /health to report ready.
    llama-server answers 503 while the model is still loading, 200 once ready.
    """
    def up():
        try:
            with urlreq.urlopen("http://localhost:8080/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    if up():
        return True

    cfg_dir = os.path.expanduser("~/.config/local-ai")
    script = os.path.join(cfg_dir, "start-hermes.sh")
    if not os.path.exists(script):
        sys.stderr.write("\033[1;33m[sys] llama-server is not running and start-hermes.sh was not found — start it manually.\033[0m\n")
        return False

    import subprocess
    log_path = os.path.join(cfg_dir, ".llama-server.log")
    sys.stderr.write(f"\033[90m[sys] Starting local llama-server in the background (log: {log_path}). Very first run downloads ~9 GB.\033[0m\n")
    with open(log_path, "a", encoding="utf-8") as log:
        subprocess.Popen(["/bin/bash", script], stdout=log, stderr=log, start_new_session=True)

    deadline = time.time() + 900  # model load takes ~a minute; first-time download much longer
    waited = 0
    while time.time() < deadline:
        if up():
            sys.stderr.write("\033[90m[sys] llama-server ready on :8080.\033[0m\n")
            return True
        time.sleep(2)
        waited += 2
        if waited % 30 == 0:
            sys.stderr.write(f"\033[90m[sys] still loading model... ({waited}s)\033[0m\n")
    sys.stderr.write(f"\033[1;33m[sys] llama-server did not become ready — check {log_path}\033[0m\n")
    return False


def stream_response(messages: list, prefix: str = "AI: ", cfg_dir: str = "", show_stats: bool = False) -> str or None:
    acc = []
    spinner = ui.InlineSpinner()
    try:
        backend = os.environ.get("AI_BACKEND", "").strip().lower()
        if backend == "local":
            ensure_local_server()
        if backend in ("claude", "codex"):
            engine = stream_claude if backend == "claude" else stream_codex
            ans = engine(messages, prefix, spinner, show_stats)
            if ans is not None:
                return ans
            sys.stderr.write(f"\033[90m[sys] {backend} backend failed, falling back.\033[0m\n")

        # Edit mode on a non-CLI backend: run the native tool loop
        # (OpenRouter if a key is set, else the local llama-server)
        if edit_mode_on():
            ans = edit_turn_tools(messages, prefix, spinner, show_stats)
            if ans is not None:
                return ans
            sys.stderr.write("\033[90m[sys] edit tools unavailable — answering read-only.\033[0m\n")

        gkey = os.environ.get("GEMINI_API_KEY")
        okey = os.environ.get("OPENROUTER_API_KEY")

        # Gemini's native interactions API (server-side session memory) only
        # applies when Gemini is the effective primary backend
        if gkey and backend in ("", "claude", "gemini"):
            try:
                ans = stream(messages, prefix, gkey, ui.InlineSpinner, show_stats)
                if ans is not None:
                    return ans
            except Exception:
                pass

        named = {}
        if gkey:
            named["gemini"] = (
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                {"Authorization": f"Bearer {gkey}"},
                os.environ.get("CLOUD_MODEL", "gemini-3.1-flash-lite"),
                {},
                30
            )
        if okey:
            named["openrouter"] = (
                "https://openrouter.ai/api/v1/chat/completions",
                {
                    "Authorization": f"Bearer {okey}",
                    "HTTP-Referer": "https://github.com/suyadnya/local-ai"
                },
                os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
                {"usage": {"include": True}},
                180
            )
        named["local"] = ("http://localhost:8080/v1/chat/completions", {}, "local-model", {}, 180)

        # AI_BACKEND promotes one engine to the front; the rest stay as fallbacks
        order = list(named.keys())
        if backend in order:
            order.insert(0, order.pop(order.index(backend)))
        configs = [named[k] for k in order]

        for url, headers, model, extra, timeout in configs:
            body = {"messages": messages, "stream": True, **extra}
            if model:
                body["model"] = model
                
            req = urlreq.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", **headers},
                method="POST"
            )
            retries = 2
            backoff = 1.5
            while retries >= 0:
                try:
                    spinner.start()
                    with urlreq.urlopen(req, timeout=timeout) as response:
                        try:
                            p = "gemini" if "generativelanguage" in url else "openrouter" if "openrouter" in url else None
                            if p and cfg_dir:
                                with open(os.path.join(cfg_dir, ".request_log"), "a", encoding="utf-8") as lf:
                                    lf.write(f"{int(time.time())}|{p}\n")
                        except Exception:
                            pass
                        first, resolved_model, usage_obj = True, None, None
                        for line in response:
                            if not line.startswith(b"data:"):
                                continue
                            content = extract_stream_content(line)
                            if content:
                                if first:
                                    spinner.stop()
                                    if sys.stdout.isatty():
                                        sys.stdout.write("\r\x1b[2K\r" + (f"\033[1;32m{prefix}\033[0m " if prefix else ""))
                                        sys.stdout.flush()
                                    first = False
                                    if speed_test and show_stats:
                                        speed_test.start()
                                print(content, end="", flush=True)
                                acc.append(content)
                                if speed_test and show_stats:
                                    speed_test.count_token(content)
                            else:
                                try:
                                    dec = line.decode("utf-8").strip()
                                    if dec.startswith("data:"):
                                        dec = dec[5:].strip()
                                    if dec == "[DONE]" or not dec:
                                        continue
                                    data = json.loads(dec)
                                    if "model" in data and not resolved_model:
                                        resolved_model = data["model"]
                                    if isinstance(data.get("usage"), dict):
                                        usage_obj = data["usage"]
                                except Exception:
                                    pass
                        print("")
                        if speed_test and show_stats:
                            speed_test.end()
                        if resolved_model and resolved_model != model and sys.stdout.isatty():
                            home_dir = os.path.expanduser("~")
                            target_path = os.path.join(home_dir, "ollama_backup") + "/"
                            display_model = resolved_model
                            if display_model.startswith(target_path):
                                display_model = display_model.replace(target_path, ".../")
                            sys.stdout.write(f"\033[90m[via {display_model}]\033[0m\n")
                            sys.stdout.flush()
                        ans_text = "".join(acc)
                        u = usage_obj or {}
                        prompt_chars = sum(len(m.get("content", "")) for m in messages)
                        in_tok = u.get("prompt_tokens") or prompt_chars // 4
                        out_tok = u.get("completion_tokens") or len(ans_text) // 4
                        _log_turn_usage(resolved_model or model or url.split('/')[2],
                                        in_tok, out_tok, u.get("cost") or 0.0,
                                        show_stats, (prompt_chars + len(ans_text)) // 4)
                        return ans_text
                except urlerr.HTTPError as e:
                    spinner.stop()
                    if e.code == 429 and retries > 0:
                        time.sleep(backoff)
                        retries -= 1
                        backoff *= 2
                    elif e.code == 400:
                        sys.stderr.write(f"\n\033[1;31m[API 400 Error]: {e.read().decode('utf-8')}\033[0m\n")
                        break
                    else:
                        host = url.split('/')[2]
                        sys.stderr.write(f"\033[90m[sys] {host} failed: HTTP {e.code}\033[0m\n")
                        break
                except Exception as e:
                    spinner.stop()
                    host = url.split('/')[2]
                    sys.stderr.write(f"\033[90m[sys] {host} failed: {e}\033[0m\n")
                    break
    except KeyboardInterrupt:
        try: spinner.stop()
        except Exception: pass
        sys.stderr.write("\n\r\x1b[2K\r[sys] Interrupted.\n")
        sys.stderr.flush()
        return "".join(acc) if 'acc' in locals() else None
    return None


def get_accurate_token_count(text: str, server_url: str = "http://localhost:8080") -> int:
    try:
        res = requests.post(f"{server_url}/tokenize", json={"content": text}, timeout=3)
        return len(res.json().get("tokens", []))
    except Exception:
        return len(text) // 4


def show_memory_status(messages: list, max_context: int = 8192, server_url: str = "http://localhost:8080") -> None:
    total_toks = sum(get_accurate_token_count(m.get("content", ""), server_url) for m in messages)
    pct = (total_toks / max_context) * 100
    bar_len = 20
    filled = int((total_toks / max_context) * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    sys.stderr.write(f"\n\033[2m[sys] Context Window: {total_toks}/{max_context} tokens\033[0m\n")
    sys.stderr.write(f"\033[2m[sys] Usage: [{bar}] {pct:.1f}%\033[0m\n")
    sys.stderr.write(f"\033[2m[sys] Remaining: {max_context - total_toks} tokens\033[0m\n\n")
    sys.stderr.flush()
    
def prune_history(history: list, max_tokens: int = None) -> list:
    """Prunes old messages from conversation history to stay within context windows."""
    if len(history) <= 1:
        return history
    try:
        target_limit = int(os.environ.get("AI_MAX_TOKENS", 8192)) if max_tokens is None else max_tokens
    except Exception:
        target_limit = 8192

    sys_prompt = history[0]
    current_tokens = len(sys_prompt["content"]) // 4
    selected_messages = []

    for msg in reversed(history[1:]):
        approx_tokens = len(msg["content"]) // 4
        if not selected_messages or (current_tokens + approx_tokens <= target_limit):
            selected_messages.append(msg)
            current_tokens += approx_tokens
        else:
            break

    return [sys_prompt] + list(reversed(selected_messages))
