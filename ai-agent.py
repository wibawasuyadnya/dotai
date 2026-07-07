#!/usr/bin/env python3
# DotAI Agent [suyadnya] [v0.9.0]
# Path: ~/.config/local-ai/ai-agent.py

import os
import sys
import re
import json
import time
import subprocess
import threading
import urllib.request as urlreq

# Configuration constants
CFG_DIR = os.path.expanduser("~/.config/local-ai")
CONTEXT_FILE = os.path.join(CFG_DIR, "ai-context.md")
SKILLS_DIR = os.path.join(CFG_DIR, "skills")
SESSIONS_DIR = os.path.join(CFG_DIR, "projects", "database")
BASE_PROMPT = (
    "Local shell AI assistant (read-only access).\n"
    "Provide direct, natural plain-text answers using any provided system context.\n"
    "No markdown (no bolding, no headers, no bullet lists).\n"
    "Always write full, complete, and helpful sentences.\n\n"
)

def load_env_file(path: str) -> None:
    """Loads KEY=value pairs from a .env file into the environment.
    Real environment variables always win, so `AI_BACKEND=local ai` still overrides."""
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


# A backend or edit mode forced on the real shell env (`AI_BACKEND=local ai`)
# outranks both settings.json and .env — remember what was set before .env loads
_REAL_ENV_BACKEND = "AI_BACKEND" in os.environ
_REAL_ENV_EDIT = "AI_EDIT_MODE" in os.environ

load_env_file(os.path.join(CFG_DIR, ".env"))

# Bootstrap custom local modules path
sys.path.append(os.path.join(CFG_DIR, "modules"))

try:
    import readline
    readline.parse_and_bind(r'"\e[A": previous-history')
    readline.parse_and_bind(r'"\e[B": next-history')
except ImportError:
    pass

# Load consolidated core library functions under a single unified namespace
try:
    import agent_core as core
    import agent_ui as ui
    import agent_spell as spell
    import agent_skills as skills
    import agent_context as context
    import agent_settings as settings
except ImportError as e:
    sys.stderr.write(f"\033[1;31m[CRITICAL]: Failed to load modules: {e}\033[0m\n")
    sys.exit(1)

# Persisted startup agent + edit mode from settings.json (real shell env wins)
settings.apply_startup(_REAL_ENV_BACKEND, _REAL_ENV_EDIT)

STOP_WORDS = {"is", "what", "it", "do", "any", "i", "have", "the", "a", "an", "on", "to", "for", "me", "you", "my", "your", "we", "us", "are", "about", "in", "how"}


def workspace_safe_name(workspace_path: str, home_dir: str) -> str:
    """Derives the per-workspace database key from a workspace path."""
    safe = workspace_path[len(home_dir):].lstrip("/") if workspace_path.startswith(home_dir) else workspace_path
    return safe.replace("/", "-").strip("-") or "home"


def workspace_db_counts(safe_name: str) -> tuple:
    """Reads (turns, tpm facts) for a workspace from its SQLite database."""
    turns = facts = 0
    try:
        res = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "get-count", safe_name], capture_output=True, text=True)
        turns = int(res.stdout.strip())
    except Exception:
        pass
    try:
        res = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "get-tpm-count", safe_name], capture_output=True, text=True)
        facts = int(res.stdout.strip())
    except Exception:
        pass
    return turns, facts


def sync_md_to_sqlite(workspace: str, workspace_path: str) -> None:
    """Parses manual edits from .agent/tpm.md back into SQLite on startup."""
    md_path = os.path.join(workspace_path, ".agent", "tpm.md")
    if not os.path.exists(md_path):
        return
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        matches = re.findall(r"\*\s+\*\*([^*]+)\*\*:\s*(.*)", content)
        if not matches:
            return
        reconciled = {k.strip().lower(): v.strip() for k, v in matches}
        if reconciled:
            # Syncs manual human edits directly back into the SQLite DB
            subprocess.run([sys.executable, os.path.join(CFG_DIR, "modules", "ai-agent-memories"), "tpm-reconcile", workspace], input=json.dumps(reconciled), text=True, capture_output=True)
    except Exception:
        pass


def background_tpm_update(user_msg: str, assistant_msg: str, workspace: str, workspace_path: str):
    """Asynchronously extracts, reconciles, and commits user facts to SQLite and tpm.md."""
    cleaned = user_msg.lower().strip()
    if len(cleaned) < 8 or cleaned in ("hello", "hi", "hey", "exit", "quit", "q", "/exit", "/quit", "/q", "/clear", "/reset", "/stats", "/tok", "/m", "/help"):
        return
    try:
        import sqlite3
        from contextlib import closing
        db_path = os.path.join(SESSIONS_DIR, f"{workspace}.db")
        existing_facts = ""
        if os.path.exists(db_path):
            with closing(sqlite3.connect(db_path, timeout=5)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT key, value FROM tpm_memories")
                rows = cur.fetchall()
                if rows:
                    existing_facts = "\n".join(f"* {k}: {v}" for k, v in rows)

        system_prompt = (
            "You are an asynchronous memory compiler. Analyze the latest user message and assistant response.\n"
            "Extract any new facts, occupations, locations, style preferences, or software tool configurations the user explicitly shared.\n"
            "Output ONLY a flat JSON object of the updated key-value pairs (e.g., {\"editor\": \"Zed\", \"role\": \"CEO\"}).\n"
            "If a fact contradicts or updates an existing fact in the memory profile, override it with the new value.\n"
            "Do not include markdown, explanations, or code blocks. Output '{}' if no new facts or updates are found."
        )
        
        user_prompt = (
            f"### Existing Memory Profile:\n{existing_facts or 'None'}\n\n"
            f"### Latest Turn:\nUser: {user_msg}\nAssistant: {assistant_msg}\n\n"
            f"Identify and reconcile any updates. Output JSON:"
        )

        # Call local llama-server directly with a fast, non-reasoning background payload
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.0,
            "thinking_budget_tokens": 0,  # Turn off thinking for maximum background speed
            "stream": False
        }

        req = urlreq.Request(
            "http://localhost:8080/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urlreq.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            llm_out = res_data["choices"][0]["message"].get("content", "").strip()

        llm_out = re.sub(r"^```json\s*|\s*```$", "", llm_out, flags=re.IGNORECASE).strip()
        parsed_json = json.loads(llm_out)
        
        if parsed_json and isinstance(parsed_json, dict):
            # Commit to SQLite
            subprocess.run(
                [sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "tpm-reconcile", workspace],
                input=json.dumps(parsed_json),
                text=True,
                capture_output=True
            )
            # Sync directly out to your local human-readable tpm.md flat file
            res_get = subprocess.run(
                [sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "tpm-get", workspace],
                capture_output=True,
                text=True
            )
            if res_get.stdout.strip():
                md_dir = os.path.join(workspace_path, ".agent")
                os.makedirs(md_dir, exist_ok=True)
                with open(os.path.join(md_dir, "tpm.md"), "w", encoding="utf-8") as mdf:
                    mdf.write(res_get.stdout.strip() + "\n")
    except Exception:
        pass


# Helper to stream and count tokens for the speed-test
def stream_llm_response(messages: list, prefix: str = "AI: ", show_stats: bool = True) -> str or None:
    try:
        import speed_test
        speed_test.set_label(ui.current_model_name())
    except Exception:
        pass
    return core.stream_response(messages, prefix, CFG_DIR, show_stats)


HELP_TEXT = """\033[1mcommands\033[0m
  \033[1;36m/help\033[0m /h /?          \033[2mthis list\033[0m
  \033[1;36m/exit\033[0m /quit /q       \033[2mleave\033[0m
  \033[1;36m/clear\033[0m /reset        \033[2mwipe history, cloud session, and TPM memory\033[0m
  \033[1;36m/save\033[0m <name>         \033[2msave this conversation\033[0m
  \033[1;36m/load\033[0m /timeline      \033[2mbrowse and restore a saved session\033[0m
  \033[1;36m/agent\033[0m <name>        \033[2mbackend: claude | codex | openrouter | local | auto\033[0m
  \033[1;36m/settings\033[0m [k v]      \033[2mpersistent config: startup agent, spellchecker (settings.json)\033[0m
  \033[1;36m/model\033[0m <name>        \033[2mset the current backend's model\033[0m
  \033[1;36m/effort\033[0m <lvl>        \033[2mcodex reasoning: minimal | low | medium | high\033[0m
  \033[1;36m/skill\033[0m <name>        \033[2mload a skill/role (alias /s)\033[0m
  \033[1;36m/skill list\033[0m          \033[2mall skills · /skill add <name> <owner/repo|url> · /skill rm <name>\033[0m
  \033[1;36m/mcp\033[0m                 \033[2mMCP servers · /mcp add <name> <url|command…> · /mcp tools <name> · /mcp rm\033[0m
  \033[1;36m/project\033[0m [name]      \033[2mlist projects · focus one (own memory/history) · new name = create\033[0m
  \033[1;36m/edit\033[0m [on|auto|off]  \033[2magents edit files & run commands anywhere (default on, y/n per action)\033[0m
  \033[1;36m/usage\033[0m               \033[2mspend per model · today's total · openrouter balance · /usage reset\033[0m
  \033[1;36m/tok\033[0m                 \033[2mtoken count of the conversation\033[0m
  \033[1;36m/stats\033[0m               \033[2mtoggle generation statistics\033[0m
  \033[1;36m/m\033[0m                   \033[2mtoggle long-term memory + TPM\033[0m
  \033[1;36m/e\033[0m /d                \033[2mspellchecker on / off\033[0m
\033[1mteam\033[0m \033[2m(agents.json)\033[0m
  \033[1;36m@<role>\033[0m <msg>        \033[2mmessage a team agent · @<role> /new = fresh session\033[0m
  \033[1;36m@<role> /last\033[0m [from] [note]  \033[2mhand a teammate's last answer to this agent\033[0m
  \033[1;36m/team\033[0m               \033[2mroster, models, session counts\033[0m
  \033[1;36m/team add\033[0m [id]      \033[2mcreate an agent (wizard)\033[0m
  \033[1;36m/team edit\033[0m <id> <name|icon|model|backend|prompt|skills|mcp> <value>
  \033[1;36m/team rm\033[0m <id>       \033[2mdelete an agent (asks about sessions)\033[0m
  \033[1;36m/team show\033[0m <id>     \033[2mone agent's full config\033[0m
"""


def run_interactive_chat(args: list):
    is_agent = (args[0] == "--talk-chat")
    skills_list = []
    active_skill = os.environ.get("AI_ACTIVE_SKILL")
    if active_skill:
        skills_list.extend([s.lstrip("-").lower() for s in active_skill.split()])
    for arg in args:
        if arg.startswith("-") and arg not in ("--talk", "--talk-chat"):
            skills_list.append(arg.lstrip("-").lower())
    skills_list = list(dict.fromkeys(skills_list))
    
    skill_content = skills.load_skill_content(" ".join(skills_list), SKILLS_DIR, CFG_DIR)
    active_system_prompt = skill_content if (is_agent and skill_content) else (BASE_PROMPT + (f"\n\n### Active Skill/Role Instructions:\n{skill_content}\n" if skill_content else ""))
    
    workspace_path = os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    home_dir = os.path.expanduser("~")
    safe_name = workspace_safe_name(workspace_path, home_dir)
    
    chat_history = [{"role": "system", "content": active_system_prompt}]
    pending_query = " ".join(args[1:]) if len(args) > 1 else None
    clean_name = " ".join(skills_list)
    
    # Spellchecker starts from the persisted setting (/d and /e save it too)
    spell_active = bool(settings.get("spellcheck", not is_agent))
    memory_active = True
    show_stats = True  # Keeps stats enabled by default inside chat sessions
    
    # Unify memory sync: Synchronize manual human edits from tpm.md back into SQLite
    if is_agent:
        sync_md_to_sqlite(safe_name, workspace_path)
    
    db_turns = 0
    tpm_count = 0
    if is_agent:
        db_turns, tpm_count = workspace_db_counts(safe_name)

    try:
        import agent_usage
        agent_usage.refresh_balance_async()
    except Exception:
        agent_usage = None

    ui.draw_session_box(workspace_path, home_dir, is_agent, db_turns, tpm_count, memory_active, active_system_prompt, clean_name)

    try:
        while True:
            typed = False
            if pending_query:
                query, pending_query = pending_query, None
            else:
                typed = True
                try:
                    # Opencode-style: composer pinned to the bottom row.
                    # Statusline carries live per-agent spend (claude/openrouter/local)
                    status = "/help commands · /team agents · /exit quit"
                    try:
                        spend = agent_usage.statusline_spend() if agent_usage else ""
                        if spend:
                            status = f"{spend}  ·  /help · /usage · /exit"
                    except Exception:
                        pass
                    if os.environ.get("AI_EDIT_MODE") == "1":
                        tag = "✎ edit·auto" if os.environ.get("AI_EDIT_CONFIRM") == "0" else "✎ edit"
                        status = f"{tag} · {status}"
                    ui.composer_prepare(status)
                    query = input("\x01\033[1;30m\x02❯\x01\033[0m\x02 ").strip()
                except EOFError:
                    break
                finally:
                    ui.composer_done()
                    try:
                        readline.set_startup_hook(None)
                    except Exception:
                        pass
                if not query:
                    continue
                # Commands echo dim into the content area (the composer row
                # is cleared, so the typed line would otherwise vanish)
                if query.startswith(("/", "-")):
                    print(f"\033[1;30m❯\033[0m \033[2m{query}\033[0m")
                if query.lower() in ("/exit", "/quit", "/q", "exit", "quit", "q"):
                    print("\r\033[1;33mExiting conversation.\033[0m")
                    sys.exit(0)
                if query.lower() in ("/help", "/h", "/?"):
                    print(HELP_TEXT)
                    continue
                if query in ("/d", "/e"):
                    spell_active = (query == "/e")
                    settings.set("spellcheck", spell_active)
                    print(f"\033[1;33m[sys] Spellchecker {'enabled' if spell_active else 'disabled'} — saved, next runs start this way.\033[0m\n")
                    continue
                
                # --- UNIFIED MEMORY LAYER TOGGLE (/m) ---
                if query == "/m":
                    memory_active = not memory_active
                    print(f"\033[1;33m[sys] Long-term memory and TPM reconciliation {'enabled' if memory_active else 'disabled'}.\033[0m\n")
                    continue
                
                # --- STATS ON-DEMAND TOGGLE ---
                if query == "/stats":
                    show_stats = not show_stats
                    print(f"\033[1;33m[sys] Generation statistics {'enabled' if show_stats else 'disabled'}.\033[0m\n")
                    continue

                # --- LIVE BACKEND / MODEL / EFFORT SWITCHING ---
                if query.split()[0] in ("/agent", "/agents", "/backend"):
                    arg = query.split(None, 1)[1].strip().lower() if " " in query else ""
                    valid = ("claude", "codex", "openrouter", "gemini", "local", "auto")
                    if arg in valid:
                        if arg == "auto":
                            os.environ.pop("AI_BACKEND", None)
                        else:
                            os.environ["AI_BACKEND"] = arg
                        print(f"\033[1;32m[sys] Agent backend switched to: {arg}\033[0m")
                        print(f"\033[2m[sys] this session only — /settings agent {arg} makes it the startup default\033[0m\n")
                    else:
                        cur = os.environ.get("AI_BACKEND") or "auto (cascade)"
                        print(f"\033[1;33m[sys] Current backend: {cur}\033[0m")
                        print(f"\033[2m[sys] Usage: /agent <{'|'.join(valid)}>  (local = your llama.cpp model, e.g. Hermes)\033[0m\n")
                    continue

                # --- PERSISTENT SETTINGS: /settings [key value] (settings.json) ---
                if query.split()[0] in ("/settings", "/setting", "/set"):
                    sp = query.split()
                    if len(sp) == 1:
                        cur_agent = settings.get("agent") or "auto"
                        cur_spell = settings.get("spellcheck", not is_agent)
                        cur_edit = settings.get("edit") or "on"
                        print("\033[1msettings\033[0m \033[2m(persist across runs — settings.json)\033[0m")
                        print(f"  \033[1m{'agent':<12}\033[0m {cur_agent:<14} \033[2mbackend on startup · /settings agent <claude|codex|openrouter|gemini|local|auto>\033[0m")
                        print(f"  \033[1m{'edit':<12}\033[0m {cur_edit:<14} \033[2magents run commands & edit anywhere · on = asks y/n each action · auto | off\033[0m")
                        print(f"  \033[1m{'spell':<12}\033[0m {'on' if cur_spell else 'off':<14} \033[2mspellchecker on startup · /settings spell <on|off> (/d and /e save too)\033[0m")
                        print(f"\033[2m  file: {settings.SETTINGS_FILE.replace(home_dir, '~', 1)}\033[0m\n")
                        continue
                    key = sp[1].lower()
                    val = sp[2].lower() if len(sp) > 2 else ""
                    if key in ("agent", "backend"):
                        if val in settings.VALID_AGENTS:
                            settings.set("agent", val)
                            if val == "auto":
                                os.environ.pop("AI_BACKEND", None)
                            else:
                                os.environ["AI_BACKEND"] = val
                            print(f"\033[1;32m[settings] startup agent saved: {val} — applied now and on every next run\033[0m\n")
                        else:
                            print(f"\033[1;33m[settings] usage: /settings agent <{'|'.join(settings.VALID_AGENTS)}>\033[0m\n")
                    elif key == "edit":
                        if val in settings.VALID_EDIT:
                            settings.set("edit", val)
                            if val == "off":
                                os.environ.pop("AI_EDIT_MODE", None)
                                os.environ.pop("AI_EDIT_CONFIRM", None)
                            else:
                                os.environ["AI_EDIT_MODE"] = "1"
                                os.environ["AI_EDIT_CONFIRM"] = "0" if val == "auto" else "1"
                            print(f"\033[1;32m[settings] edit mode saved: {val} — applied now and on every next run\033[0m\n")
                        else:
                            print("\033[1;33m[settings] usage: /settings edit <on|auto|off> — on asks y/n per action\033[0m\n")
                    elif key in ("spell", "spellcheck", "autocorrect"):
                        if val in ("on", "off"):
                            spell_active = (val == "on")
                            settings.set("spellcheck", spell_active)
                            print(f"\033[1;32m[settings] spellchecker saved: {val} — applied now and on every next run\033[0m\n")
                        else:
                            print("\033[1;33m[settings] usage: /settings spell <on|off>\033[0m\n")
                    else:
                        print(f"\033[1;33m[settings] unknown key '{key}' — /settings shows what can be set\033[0m\n")
                    continue

                if query.split()[0] == "/model":
                    model_vars = {"claude": "CLAUDE_MODEL", "codex": "CODEX_MODEL", "openrouter": "OPENROUTER_MODEL", "gemini": "CLOUD_MODEL"}
                    backend_now = os.environ.get("AI_BACKEND", "").strip().lower()
                    var = model_vars.get(backend_now)
                    arg = query.split(None, 1)[1].strip() if " " in query else ""
                    if not var:
                        print(f"\033[1;33m[sys] Backend '{backend_now or 'auto'}' has no switchable model (local is fixed by llama-server). Pick one first: /agent claude\033[0m\n")
                    elif arg:
                        os.environ[var] = arg
                        print(f"\033[1;32m[sys] {backend_now} model set to: {arg}\033[0m\n")
                    else:
                        print(f"\033[1;33m[sys] Current {backend_now} model: {os.environ.get(var) or '(default)'} — change with /model <name>\033[0m\n")
                    continue

                if query.split()[0] == "/effort":
                    arg = query.split(None, 1)[1].strip().lower() if " " in query else ""
                    if arg in ("minimal", "low", "medium", "high"):
                        os.environ["CODEX_EFFORT"] = arg
                        print(f"\033[1;32m[sys] Codex reasoning effort set to: {arg}\033[0m\n")
                    else:
                        print(f"\033[1;33m[sys] Current codex effort: {os.environ.get('CODEX_EFFORT') or '(default)'} — usage: /effort <minimal|low|medium|high>\033[0m\n")
                    continue

                # --- NATIVE FULL SESSION CLEAR ---
                if query.lower() in ("/clear", "/reset"):
                    # 1. Clear local Python history array and pre-fill the startup greeting to prevent loops
                    chat_history = [
                        {"role": "system", "content": active_system_prompt},
                        {"role": "assistant", "content": "Agent: Workspace loaded. Awaiting instructions."}
                    ]
                    
                    # 2. Clear Google/Cloud session by deleting session.json
                    try:
                        sf = os.path.join(workspace_path, ".agent", "session.json")
                        if os.path.exists(sf):
                            os.remove(sf)
                        md_file = os.path.join(workspace_path, ".agent", "tpm.md")
                        if os.path.exists(md_file):
                            os.remove(md_file)
                        # Delete the physical workspace history file as well
                        hist_file = os.path.join(workspace_path, "history.md")
                        if os.path.exists(hist_file):
                            os.remove(hist_file)
                    except Exception:
                        pass
                        
                    # 3. Clear both semantic turns and the compiled TPM facts table in SQLite
                    try:
                        subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "clear", safe_name], capture_output=True)
                        subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "tpm-clear", safe_name], capture_output=True)
                    except Exception:
                        pass
                        
                    # Fresh screen like opencode: wipe content and redraw header
                    if sys.stdout.isatty():
                        sys.stdout.write("\033[2J\033[H")
                        ui.draw_session_box(workspace_path, home_dir, is_agent, db_turns, tpm_count, memory_active, active_system_prompt, clean_name)
                    print("\033[1;32m[sys] Conversation history, cloud session, and local TPM memory cleared.\033[0m\n")
                    continue

                if query == "/tok":
                    subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "show-tok"], input=json.dumps(chat_history), text=True)
                    continue
                if spell_active and not query.startswith(("/", "-", "#", "@", "```")):
                    action, query = spell.check_query_spelling(query, ui.get_key)
                    if action == "EDIT":
                        try:
                            readline.set_startup_hook(lambda: readline.insert_text(query))
                        except Exception:
                            pass
                        continue
                    elif action == "DISABLE":
                        spell_active = False
                        settings.set("spellcheck", False)
                    
            # Opencode-style: re-render the typed line as a shaded user block
            # (chat + @team messages only; /commands keep the plain prompt)
            if typed and not query.startswith(("/", "-")):
                ui.echo_user_block(query)

            # --- TEAM ORCHESTRATION: @debug/@review/@research from agents.json ---
            if query.startswith("@") or query.split()[0] == "/team":
                import agent_roles as roles
                if query.split()[0] == "/team":
                    roles.team_command(query)
                else:
                    roles.dispatch(query)
                continue

            # --- PROJECT FOCUS: /project [name|path] — each project keeps its own
            # memory, history, and cloud session (workspace switch without restart)
            if query.split()[0] in ("/project", "/proj", "/projects"):
                arg = query.split(None, 1)[1].strip() if " " in query else ""
                projects_root = os.path.join(CFG_DIR, "projects")
                if not arg or arg == "list":
                    names = sorted(
                        d for d in (os.listdir(projects_root) if os.path.isdir(projects_root) else [])
                        if os.path.isdir(os.path.join(projects_root, d)) and d != "database"
                    )
                    for name in names:
                        p = os.path.join(projects_root, name)
                        turns, facts = workspace_db_counts(workspace_safe_name(p, home_dir))
                        mark = "\033[1;36m●\033[0m" if p == workspace_path else " "
                        print(f"  {mark} \033[1m{name:<24}\033[0m \033[2m{turns} turns · {facts} facts\033[0m")
                    if not names:
                        print("\033[2m  no projects yet — /project <name> creates one\033[0m")
                    print(f"\033[2m  focused: {workspace_path.replace(home_dir, '~', 1)} — switch with /project <name|path>\033[0m\n")
                    continue
                # Resolve: an existing project name wins, then a filesystem path,
                # otherwise a new project directory is created under projects/
                cand = os.path.expanduser(arg)
                proj_dir = os.path.join(projects_root, arg)
                if os.path.isdir(proj_dir):
                    target = proj_dir
                elif os.path.isabs(cand) or arg.startswith(("~", "./", "../")) or os.path.isdir(cand):
                    target = os.path.abspath(cand)
                else:
                    target = proj_dir
                created = not os.path.isdir(target)
                if created:
                    try:
                        os.makedirs(target, exist_ok=True)
                    except Exception as e:
                        print(f"\033[1;31m[project] cannot create {target}: {e}\033[0m\n")
                        continue
                workspace_path = target
                os.environ["AI_WORKSPACE_PATH"] = target
                safe_name = workspace_safe_name(workspace_path, home_dir)
                chat_history = [{"role": "system", "content": active_system_prompt}]
                db_turns = tpm_count = 0
                if is_agent:
                    sync_md_to_sqlite(safe_name, workspace_path)
                    db_turns, tpm_count = workspace_db_counts(safe_name)
                if sys.stdout.isatty():
                    sys.stdout.write("\033[2J\033[H")
                ui.draw_session_box(workspace_path, home_dir, is_agent, db_turns, tpm_count, memory_active, active_system_prompt, clean_name)
                print(f"\033[1;32m[project] {'created and ' if created else ''}focused: {workspace_path.replace(home_dir, '~', 1)}\033[0m\n")
                continue

            # --- EDIT MODE: /edit [on|off] — let the agent modify project files.
            # claude/codex run as real agents in the focused project; openrouter
            # and local get read/write/list/run file tools (shell asks first)
            if query.split()[0] in ("/edit", "/write"):
                arg = query.split(None, 1)[1].strip().lower() if " " in query else ""
                if arg not in ("", "on", "auto", "off"):
                    print("\033[1;33m[sys] usage: /edit — toggle · /edit on (asks y/n) · /edit auto (no prompts) · /edit off\033[0m\n")
                    continue
                cur = os.environ.get("AI_EDIT_MODE") == "1"
                if arg in ("on", "auto") or (arg == "" and not cur):
                    os.environ["AI_EDIT_MODE"] = "1"
                    os.environ["AI_EDIT_CONFIRM"] = "0" if arg == "auto" else "1"
                    settings.set("edit", "auto" if arg == "auto" else "on")
                    backend = os.environ.get("AI_BACKEND", "").strip().lower()
                    if backend == "claude":
                        engine = f"claude CLI ({os.environ.get('CLAUDE_MODEL', 'sonnet')})"
                    elif backend == "codex":
                        engine = "codex CLI, workspace-write sandbox"
                    elif backend == "local" or not os.environ.get("OPENROUTER_API_KEY"):
                        engine = "local llama-server + file tools"
                    else:
                        engine = f"{os.environ.get('OPENROUTER_MODEL', 'openrouter/free')} + file tools"
                    guard = "auto-approve, no prompts" if arg == "auto" else "every write/shell command asks you y/n first"
                    print(f"\033[1;32m[sys] Edit mode ON — {engine}\033[0m")
                    print(f"\033[2m[sys] {guard} · confined to {workspace_path.replace(home_dir, '~', 1)} · /edit off to leave\033[0m\n")
                else:
                    os.environ.pop("AI_EDIT_MODE", None)
                    os.environ.pop("AI_EDIT_CONFIRM", None)
                    settings.set("edit", "off")
                    print("\033[1;33m[sys] Edit mode OFF — read-only chat, saved for next runs too (/edit on restores it).\033[0m\n")
                continue

            # --- SPEND LEDGER: /usage — per-model tokens & cost, today's total,
            # and the live OpenRouter credit balance
            if query.split()[0] in ("/usage", "/cost", "/spend"):
                if not agent_usage:
                    print("\033[1;31m[usage] ledger module unavailable\033[0m\n")
                    continue
                arg = query.split(None, 1)[1].strip().lower() if " " in query else ""
                if arg == "reset":
                    agent_usage.reset()
                    print("\033[1;32m[usage] spend ledger reset.\033[0m\n")
                else:
                    agent_usage.print_summary()
                continue

            # --- MCP SERVERS: /mcp [add|rm|tools] (config in mcp.json) ---
            if query.split()[0] == "/mcp":
                import mcp_client as mcp
                p = query.split()
                servers = mcp.load_servers()
                if len(p) == 1:
                    if not servers:
                        print("\033[2m[mcp] none configured — /mcp add <name> <url | command…>\033[0m\n")
                    else:
                        for name, cfg in servers.items():
                            target = cfg.get("url") or " ".join(cfg.get("command", []))
                            try:
                                status = f"{len(mcp.list_tools(name))} tools"
                            except Exception as e:
                                status = f"unreachable: {str(e)[:48]}"
                            print(f"  \033[1m{name:<12}\033[0m \033[2m{target[:70]} · {status}\033[0m")
                        print("\033[2m  attach to an agent with /team edit <id> mcp <name>\033[0m\n")
                elif p[1] == "add" and len(p) >= 4:
                    servers[p[2]] = {"url": p[3]} if p[3].startswith("http") else {"command": p[3:]}
                    mcp.save_servers(servers)
                    print(f"\033[1;32m[mcp] added '{p[2]}' — check it with /mcp tools {p[2]}\033[0m\n")
                elif p[1] in ("rm", "remove") and len(p) >= 3:
                    if servers.pop(p[2], None) is not None:
                        mcp.save_servers(servers)
                        print(f"\033[1;32m[mcp] '{p[2]}' removed\033[0m\n")
                    else:
                        print(f"\033[1;33m[mcp] no server '{p[2]}'\033[0m\n")
                elif p[1] == "tools" and len(p) >= 3:
                    try:
                        for t in mcp.list_tools(p[2]):
                            desc = (t.get("description") or "").split("\n")[0][:80]
                            print(f"  \033[1m{t['name']:<28}\033[0m \033[2m{desc}\033[0m")
                        print()
                    except Exception as e:
                        print(f"\033[1;31m[mcp] {e}\033[0m\n")
                else:
                    print("\033[1;33m[mcp] usage: /mcp · /mcp add <name> <url | command…> · /mcp tools <name> · /mcp rm <name>\033[0m\n")
                continue

            # --- SKILL MANAGEMENT: /skill list|add|rm (files in skills/) ---
            qp = query.split()
            if qp[0] in ("/skill", "/s") and len(qp) > 1 and qp[1] in ("add", "rm", "remove", "list"):
                if qp[1] == "list":
                    for cat, names in sorted(skills.list_skills(SKILLS_DIR).items()):
                        print(f"  \033[1m{cat:<10}\033[0m \033[2m{' · '.join(names)}\033[0m")
                    print("\033[2m  load one with /skill <name>, install with /skill add <name> <owner/repo|url>\033[0m\n")
                elif qp[1] == "add" and len(qp) >= 4:
                    try:
                        dest = skills.install_skill(qp[2], qp[3], SKILLS_DIR)
                        print(f"\033[1;32m[skill] installed '{qp[2]}' → {dest.replace(home_dir, '~', 1)} — load with /skill {qp[2]}\033[0m\n")
                    except Exception as e:
                        print(f"\033[1;31m[skill] {e}\033[0m\n")
                elif qp[1] in ("rm", "remove") and len(qp) >= 3:
                    if skills.remove_skill(qp[2], SKILLS_DIR):
                        print(f"\033[1;32m[skill] '{qp[2]}' removed\033[0m\n")
                    else:
                        print(f"\033[1;33m[skill] no custom skill '{qp[2]}' (only skills/custom/ can be removed)\033[0m\n")
                else:
                    print("\033[1;33m[skill] usage: /skill list · /skill add <name> <owner/repo|url> · /skill rm <name>\033[0m\n")
                continue

            if query in ("/skill", "/s") or query.startswith(("/skill ", "/s ")):
                res = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-skills", safe_name, query], input=json.dumps(chat_history), stdout=subprocess.PIPE, text=True)
                if res.stdout.strip():
                    try:
                        chat_history = json.loads(res.stdout.strip())
                        print(f"\033[1;32m[skill] Skill loaded into the session ({len(chat_history)-1} messages).\033[0m\n")
                    except Exception as e:
                        print(f"Error loading skill: {e}")
                continue
            if query.startswith(("/save", "-save")):
                subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "save", safe_name, (query.split(None, 1)[1].strip() if " " in query else "")], input=json.dumps(chat_history), text=True)
                continue
            if query in ("/load", "/timeline", "-load", "-timeline"):
                res = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "load", safe_name], stdin=sys.stdin, stdout=subprocess.PIPE, text=True)
                if res.stdout.strip():
                    try:
                        chat_history = json.loads(res.stdout.strip())
                        print(f"\033[1;32m[session-mgr] Restored session ({len(chat_history)-1} turns loaded).\033[0m\n")
                    except Exception as e:
                        print(f"Error loading session: {e}")
                else:
                    print(f"\033[1;31m[session-mgr] Load aborted.\033[0m\n")
                    continue
                
            past_memory = ""
            tpm_context = ""
            is_init_map = query.startswith(("#", "[", "{")) or "\n" in query or "last_interaction_id" in query or "index-map" in query
            if is_agent and memory_active and not is_init_map:
                # 1. Fetch semantic semantic memory
                res = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "get-context", safe_name, query], stdout=subprocess.PIPE, text=True)
                if res.returncode == 2:
                    pending_query = None
                    continue
                if res.returncode == 3:
                    memory_active = False
                past_memory = res.stdout.strip()
                
                # 2. Fetch compiled, resolved Temporal Personality Memory (TPM)
                res_tpm = subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-memories", "tpm-get", safe_name], stdout=subprocess.PIPE, text=True)
                tpm_context = res_tpm.stdout.strip()
                
            if re.match(r'^/?([ftba])(?:\s+(\d+))?$', query.lower()):
                think_bin = f"{CFG_DIR}/modules/chat"
                if os.path.exists(think_bin):
                    try:
                        subprocess.run([sys.executable, think_bin, query], input=json.dumps(chat_history), text=True)
                        continue
                    except Exception as e:
                        sys.stderr.write(f"\033[1;31m[Warning] chat failed: {e}\033[0m\n")
                        continue
                else:
                    sys.stderr.write("\033[1;31mError: chat tool not found\033[0m\n")
                    continue
                
            if is_init_map:
                prompt = f"### SYSTEM INSTRUCTIONS (CRITICAL OVERRIDE):\n{active_system_prompt}\n\n### CODESPACE MAP:\n{query}"
            else:
                sys_ctx = skills.get_system_context(query, CONTEXT_FILE, STOP_WORDS, SKILLS_DIR, CFG_DIR)
                comb_ctx = (f"{tpm_context}\n\n" if tpm_context else "") + (f"{past_memory}\n\n" if past_memory else "") + sys_ctx
                prompt = (f"### Real-time System Context:\n{comb_ctx}\n\n" if comb_ctx else "") + f"User Question: {query}"
                
            chat_history.append({"role": "user", "content": prompt})
            if not is_init_map:
                try:
                    readline.add_history(query)
                except Exception:
                    pass
            # Explicitly passes your dynamic show_stats state parameter
            ans = stream_llm_response(core.prune_history(chat_history), prefix="", show_stats=show_stats)
            if ans:
                chat_history.append({"role": "assistant", "content": ans})
                if is_agent:
                    subprocess.run([sys.executable, f"{CFG_DIR}/modules/ai-agent-sessions", "log-turn", safe_name, query, ans])
                    
                    # --- SPIN BACKGROUND RECONCILIATION THREAD ---
                    # Asynchronously updates your local Temporal Personality Memory (TPM)
                    if memory_active and not is_init_map:
                        threading.Thread(
                            target=background_tpm_update,
                            args=(query, ans, safe_name, workspace_path),
                            daemon=True
                        ).start()
                        
                    if not is_init_map:
                        hist_file = os.path.join(workspace_path, "history.md")
                        try:
                            mode = "a" if os.path.exists(hist_file) else "w"
                            with open(hist_file, mode, encoding="utf-8") as hf:
                                if mode == "w":
                                    hf.write(f"# Workspace History: {os.path.basename(os.path.dirname(workspace_path))}\n\n")
                                hf.write(f"## [{time.strftime('%Y-%m-%d %H:%M')}] User:\n{query}\n\n### Agent:\n{ans}\n\n---\n\n")
                        except Exception:
                            pass
    except KeyboardInterrupt:
        ui.bottom_input_off()
        print("\n\r\033[1;33mExiting conversation.\033[0m")
        sys.exit(0)
    finally:
        ui.bottom_input_off()
        # True zero-daemon exit: stop the auto-started llama-server too
        # (runs on /exit, Ctrl+C, and Ctrl+D alike; AI_KEEP_LOCAL=1 skips it)
        core.shutdown_local_server()


def run_direct_query(args: list):
    """Executes a direct shell query command, explicitly disabling the speed test output."""
    query_parts = args[1:]
    # One-shot team dispatch: `ai @debug why is this failing …` / `ai /team rm x`
    if query_parts and query_parts[0] == "/team":
        import agent_roles as roles
        roles.team_command(" ".join(query_parts))
        sys.exit(0)
    if query_parts and query_parts[0].startswith("@"):
        import agent_roles as roles
        if roles.dispatch(" ".join(query_parts)):
            sys.exit(0)
    active_system_prompt = BASE_PROMPT
    if query_parts and query_parts[-1].startswith("-"):
        skill_content = skills.load_skill_content(query_parts[-1].lstrip("-").lower(), SKILLS_DIR, CFG_DIR)
        if skill_content:
            active_system_prompt += f"\n\n### Active Skill/Role Instructions:\n{skill_content}\n"
        query_parts = query_parts[:-1]
    query = " ".join(query_parts)
    sys_ctx = skills.get_system_context(query, CONTEXT_FILE, STOP_WORDS, SKILLS_DIR, CFG_DIR)
    messages = [
        {"role": "system", "content": active_system_prompt},
        {"role": "user", "content": (f"### Real-time System Context:\n{sys_ctx}\n\n" if sys_ctx else "") + f"User Question: {query}"}
    ]
    # Passes show_stats=False so direct queries remain silent
    stream_llm_response(messages, prefix="AI:", show_stats=False)
    sys.exit(0)


def run_matching_search(args: list):
    """Handles single terminal queries, attempting local tool execution first."""
    user_input = re.sub(r"[`$]", "", " ".join(args)).strip()
    if not user_input or args[0].startswith("--"):
        sys.exit(0)
    if re.search(r"[\[\]{}()='\",;|#<>]", user_input):
        shell_name = os.path.basename(os.environ.get("SHELL", "/bin/bash"))
        sys.stderr.write(f"zsh: command not found: {user_input}\n" if "zsh" in shell_name else f"bash: {user_input}: command not found\n")
        sys.exit(127)
    matched = context.jaccard_search(user_input, CONTEXT_FILE, STOP_WORDS)
    if matched:
        print("\n".join(f"{line.split('|||', 1)[0]}|||{context.clean_tool_prefix(line.split('|||', 1)[1])}" for line in matched.split("\n")))
        sys.exit(0)
    shell_name = os.path.basename(os.environ.get("SHELL", "/bin/bash"))
    sys.stderr.write(f"zsh: command not found: {user_input}\n" if "zsh" in shell_name else f"bash: {user_input}: command not found\n")
    sys.exit(127)


def main():
    """Main program entry point orchestrating CLI args and flows."""
    try:
        args = sys.argv[1:]
        if args:
            if args[0] == "--interactive" and len(args) >= 2:
                ui.run_interactive_selection(
                    " ".join(args[1:]),
                    lambda q: context.jaccard_search(q, CONTEXT_FILE, STOP_WORDS),
                    context.clean_tool_prefix,
                    lambda n: sys.stderr.write(f"zsh: command not found: {n}\n" if "zsh" in os.path.basename(os.environ.get("SHELL", "")) else f"bash: {n}: command not found\n"),
                    lambda: skills.ensure_mysys_exists(SKILLS_DIR, CFG_DIR)
                )
                sys.exit(0)
            if args[0] in ("--talk", "--talk-chat"):
                if args[0] == "--talk-chat" or len(args) == 1:
                    run_interactive_chat(args)
                else:
                    run_direct_query(args)
                sys.exit(0)
            run_matching_search(args)
        else:
            # Fallback for empty arguments
            run_direct_query(["--talk"])
    except KeyboardInterrupt:
        sys.stderr.write("\nCancelled.\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
