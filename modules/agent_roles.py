# File: ~/.config/local-ai/modules/agent_roles.py
"""Terminal dispatch for the role agents in agents.json (DotAI).

The user is the orchestrator: inside `ai` (or as a one-shot), messages like

    @debug why does this stack trace point to a None?
    @review check the diff I just pasted
    @research compare SSE vs websockets for this

are routed to that agent's own model + system prompt, continuing its most
recent session. Sessions live in .sessions/ — the same store the web GUI
uses, so terminal turns show up there too. `@<role> /new` starts a fresh
session; `/team` lists the roster and `/team add|edit|rm|show` manages it.
"""
import re

import agent_service as svc

_PREFIX_COLORS = {"debug": "1;31", "review": "1;35", "research": "1;36", "chat": "1;32"}
_PALETTE = ("1;31", "1;32", "1;33", "1;34", "1;35", "1;36")
_BACKENDS = ("openrouter", "claude", "codex")
_DEFAULT_MODELS = {"openrouter": "deepseek/deepseek-v4-flash", "claude": "sonnet", "codex": "gpt-5.2-codex"}
_FIELDS = ("name", "icon", "model", "backend", "prompt", "skills", "mcp")


def _color(agent_id: str) -> str:
    if agent_id in _PREFIX_COLORS:
        return _PREFIX_COLORS[agent_id]
    return _PALETTE[sum(ord(c) for c in agent_id) % len(_PALETTE)]


def _latest_session(agent_id: str) -> dict:
    metas = svc.list_sessions(agent_id)
    if metas:
        return svc.get_session(agent_id, metas[0]["id"]) or svc.create_session(agent_id)
    return svc.create_session(agent_id)


def team_summary() -> str:
    lines = ["\033[1mteam\033[0m — @<name> <msg> · @<name> /new · /team add|edit|rm|show"]
    for a in svc.list_agents():
        count = len(svc.list_sessions(a["id"]))
        backend = a.get("backend", "openrouter")
        tag = f" · {backend}" if backend != "openrouter" else ""
        lines.append(
            f"  \033[{_color(a['id'])}m@{a['id']:<9}\033[0m {a['icon']} {a['name']:<11} "
            f"\033[2m{a['model']}{tag} · {count} session(s)\033[0m"
        )
    return "\n".join(lines) + "\n"


# ── /team CRUD ───────────────────────────────────────────────────────────────

def _ask(label: str, default: str = "") -> str or None:
    """Prompt with a default; returns None if the user aborts (ctrl-c/ctrl-d)."""
    hint = f" \033[2m[{default}]\033[0m" if default else ""
    try:
        val = input(f"  \033[1m{label}\033[0m{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return val or default


def _team_add(arg: str) -> None:
    agents = svc.list_agents()
    ids = {a["id"] for a in agents}
    aid = (arg.split()[0] if arg else _ask("@handle (id)") or "").lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", aid or ""):
        print("\033[1;33m[team] id must be 1-16 chars: lowercase letters, digits, - or _\033[0m\n")
        return
    if aid in ids:
        print(f"\033[1;33m[team] @{aid} already exists — edit it with /team edit {aid} …\033[0m\n")
        return
    name = _ask("Name", aid.capitalize())
    if name is None:
        return
    icon = _ask("Icon", "🤖")
    if icon is None:
        return
    backend = (_ask("Backend (openrouter/claude/codex)", "openrouter") or "").lower()
    if backend is None or backend not in _BACKENDS:
        if backend is not None:
            print(f"\033[1;33m[team] backend must be one of: {', '.join(_BACKENDS)}\033[0m\n")
        return
    model = _ask("Model", _DEFAULT_MODELS[backend])
    if model is None:
        return
    want = _backend_for_model(model)
    if want and want != backend:
        print(f"\033[1;33m  [team] heads up: '{model}' looks like a {want} model, not {backend}\033[0m")
    system = _ask("System prompt", "You are a helpful assistant.")
    if system is None:
        return
    agent = {"id": aid, "name": name, "icon": icon, "model": model, "system": system}
    if backend != "openrouter":
        agent["backend"] = backend
    svc.save_agents(agents + [agent])
    print(f"\n\033[1;32m[team] added {icon} {name} — try: @{aid} hello\033[0m\n")


def _team_rm(arg: str) -> None:
    aid = arg.split()[0].lstrip("@").lower() if arg else ""
    agents = svc.list_agents()
    agent = next((a for a in agents if a["id"] == aid), None)
    if not agent:
        print(f"\033[1;33m[team] usage: /team rm <id> — ids: {' '.join(a['id'] for a in agents)}\033[0m\n")
        return
    if len(agents) == 1:
        print("\033[1;33m[team] can't delete the last agent — add another first.\033[0m\n")
        return
    if (_ask(f"Delete {agent['icon']} {agent['name']} (@{aid})? (y/N)", "n") or "n").lower() != "y":
        print("\033[2m[team] kept.\033[0m\n")
        return
    svc.save_agents([a for a in agents if a["id"] != aid])
    sessions = svc.list_sessions(aid)
    if sessions and (_ask(f"Also delete its {len(sessions)} session(s)? (y/N)", "n") or "n").lower() == "y":
        import shutil
        shutil.rmtree(f"{svc.SESSIONS_DIR}/{aid}", ignore_errors=True)
        print(f"\033[1;32m[team] @{aid} and its sessions deleted.\033[0m\n")
    else:
        print(f"\033[1;32m[team] @{aid} deleted (sessions kept in .sessions/{aid}).\033[0m\n")


def _backend_for_model(model: str) -> str:
    """Guess which backend a model name belongs to ('' = no idea)."""
    m = model.lower()
    if "/" in m:
        return "openrouter"
    if any(k in m for k in ("opus", "sonnet", "haiku", "claude")):
        return "claude"
    if m.startswith(("gpt", "o1", "o3", "o4", "codex")):
        return "codex"
    return ""


def _team_edit(arg: str) -> None:
    parts = arg.split(None, 2)
    agents = svc.list_agents()
    if len(parts) < 3 or parts[1].lower() not in _FIELDS:
        print(f"\033[1;33m[team] usage: /team edit <id> <{'|'.join(_FIELDS)}> <value>\033[0m")
        print("\033[2m[team] e.g. /team edit debug model deepseek/deepseek-v4-flash\033[0m\n")
        return
    aid, field, value = parts[0].lstrip("@").lower(), parts[1].lower(), parts[2].strip()
    agent = next((a for a in agents if a["id"] == aid), None)
    if not agent:
        print(f"\033[1;33m[team] no agent '@{aid}' — ids: {' '.join(a['id'] for a in agents)}\033[0m\n")
        return
    if field == "backend":
        if value.lower() not in _BACKENDS:
            print(f"\033[1;33m[team] backend must be one of: {', '.join(_BACKENDS)}\033[0m\n")
            return
        value = value.lower()
    key = "system" if field == "prompt" else field
    if field in ("skills", "mcp"):
        names = [s for s in value.replace(",", " ").split() if s]
        if names == ["none"]:
            agent.pop(field, None)
        else:
            if field == "mcp":
                import mcp_client
                unknown = [n for n in names if n not in mcp_client.load_servers()]
                if unknown:
                    print(f"\033[1;33m[team] not in mcp.json: {' '.join(unknown)} — add with /mcp add\033[0m\n")
                    return
            agent[field] = names
        svc.save_agents(agents)
        print(f"\033[1;32m[team] @{aid} {field} → {', '.join(names) if names != ['none'] else 'cleared'}\033[0m\n")
        return
    agent[key] = value
    if field == "model":
        want = _backend_for_model(value)
        have = agent.get("backend", "openrouter")
        if want and want != have:
            ans = _ask(f"'{value}' looks like a {want} model but @{aid} runs on {have} — switch backend to {want}? (Y/n)", "y")
            if (ans or "n").lower() != "n":
                if want == "openrouter":
                    agent.pop("backend", None)
                else:
                    agent["backend"] = want
                print(f"\033[1;32m[team] @{aid} backend → {want}\033[0m")
            else:
                print(f"\033[1;33m[team] kept backend {have} — this model will likely fail on it\033[0m")
    elif field == "backend":
        want = _backend_for_model(agent.get("model", ""))
        if want and want != value:
            print(f"\033[1;33m[team] heads up: model '{agent['model']}' looks like a {want} model — "
                  f"set one for {value} with /team edit {aid} model …\033[0m")
    svc.save_agents(agents)
    print(f"\033[1;32m[team] @{aid} {field} → {value}\033[0m")
    if field == "model":
        print(f"\033[2m[team] existing sessions keep their old model — @{aid} /new to use it\033[0m")
    print()


def _team_show(arg: str) -> None:
    aid = arg.split()[0].lstrip("@").lower() if arg else ""
    agent = next((a for a in svc.list_agents() if a["id"] == aid), None)
    if not agent:
        print(f"\033[1;33m[team] usage: /team show <id> — ids: {' '.join(a['id'] for a in svc.list_agents())}\033[0m\n")
        return
    print(f"\033[{_color(aid)}m@{aid}\033[0m {agent['icon']} \033[1m{agent['name']}\033[0m")
    print(f"  \033[2mbackend\033[0m {agent.get('backend', 'openrouter')}")
    print(f"  \033[2mmodel\033[0m   {agent['model']}")
    if agent.get("skills"):
        print(f"  \033[2mskills\033[0m  {', '.join(agent['skills'])}")
    if agent.get("mcp"):
        print(f"  \033[2mmcp\033[0m     {', '.join(agent['mcp'])}")
    print(f"  \033[2mprompt\033[0m  {agent['system']}")
    print(f"  \033[2msessions\033[0m {len(svc.list_sessions(aid))}\n")


def team_command(query: str) -> None:
    """Handles '/team [add|edit|rm|show] …' (no subcommand = roster)."""
    parts = query.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    if not sub:
        print(team_summary())
    elif sub == "add":
        _team_add(rest)
    elif sub in ("rm", "remove", "del", "delete"):
        _team_rm(rest)
    elif sub == "edit":
        _team_edit(rest)
    elif sub == "show":
        _team_show(rest)
    else:
        print("\033[1;33m[team] usage: /team · /team add [id] · /team edit <id> <field> <value> · /team rm <id> · /team show <id>\033[0m\n")


def dispatch(query: str) -> bool:
    """Handles '@<role> <message>'. Returns True when the query was consumed."""
    if not query.startswith("@"):
        return False
    head, _, message = query.partition(" ")
    role = head[1:].lower()
    agents = {a["id"]: a for a in svc.list_agents()}
    if role not in agents:
        known = " ".join("@" + i for i in agents)
        print(f"\033[1;33m[team] no agent '@{role}' — available: {known}\033[0m\n")
        return True

    agent = agents[role]
    color = _color(role)
    message = message.strip()
    if not message:
        print(f"\033[2m[team] usage: @{role} <message>   (model: {agent['model']})\033[0m\n")
        return True
    if message == "/new":
        svc.create_session(role)
        print(f"\033[1;32m[team] fresh session for {agent['name']}.\033[0m\n")
        return True

    session = _latest_session(role)
    print(f"\033[{color}m{agent['name']}:\033[0m ", end="", flush=True)
    for ev in svc.stream_chat(session, message):
        if ev["type"] == "token":
            print(ev["text"], end="", flush=True)
        elif ev["type"] == "error":
            print(f"\033[1;31m[error] {ev['message']}\033[0m", end="")
        elif ev["type"] == "done":
            u = ev["usage"]
            cost = f" · ${ev['cost']:.5f}" if ev.get("cost") else ""
            print(f"\n\033[2m▪ {agent['model']} · ↓{u['in']:,} ↑{u['out']:,}{cost}\033[0m")
    print()
    return True
