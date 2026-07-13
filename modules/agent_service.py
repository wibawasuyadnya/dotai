# File: ~/.config/orkesai/modules/agent_service.py
"""Multi-agent service used by the HTTP server (server/) behind the web GUI (gui/).

Unlike agent_core.stream_response (which prints tokens to the terminal), this
module exposes streaming as a generator of event dicts so any frontend can
render them. Backends: OpenRouter (primary) with local llama.cpp fallback.
Sessions are persisted as JSON files under <repo>/.sessions/<agent-id>/.
"""
import os
import re
import sys
import json
import time
import uuid
import threading
import urllib.request as urlreq
import urllib.error as urlerr

CFG_DIR = os.path.join(os.path.expanduser("~"), ".config", "orkesai")
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
                val = val.strip()
                # Unquoted values may carry an inline comment (KEY=x  # note)
                if not val.startswith(('"', "'")):
                    val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
                val = val.strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


# ── Agents ───────────────────────────────────────────────────────────────────
# The built-in default agent backs the plain (non-role) chat — like typing `ai`
# in the terminal. It is NOT a team role: it never appears in the Team list and
# can't be deleted. Team @roles live in agents.json separately.

DEFAULT_AGENT_ID = "default"

# Shared house style + behaviour, appended to every agent's system prompt so
# answers render cleanly in the GUI and the model doesn't "give up" on actions.
STYLE_SYSTEM_ADD = (
    "\n\n### How to respond\n"
    "- Never dump wide Markdown tables for file/directory listings or metadata — "
    "in this chat they collapse into one unreadable line. Instead use a fenced "
    "code block with one item per line, e.g.\n"
    "```\n📁 folder-name\n📄 file.txt   (1,034 bytes)\n```\n"
    "Reserve tables only for genuinely tabular data with 2–3 short columns, and "
    "always put each row on its own line.\n"
    "- Write like a person: a short sentence of context, then the block, then an "
    "optional follow-up question. Don't pad with headings for tiny answers.\n"
    "- When an action needs permission or touches something outside the current "
    "folder, actually attempt it by calling the tool — the user gets an Allow/Deny "
    "prompt. Do NOT give up and paste a command for the user to run themselves; "
    "run it and let them approve or deny."
)


def default_agent() -> dict:
    return {
        "id": DEFAULT_AGENT_ID,
        "name": "OrkesAI",
        "icon": "✨",
        "model": os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash"),
        "system": "You are OrkesAI, a helpful, concise assistant. Give direct, "
                  "complete answers. Use your tools when a task needs files, "
                  "commands, or the web, and report what you actually did.",
        "backend": "openrouter",
    }


def list_agents() -> list:
    """Team @roles from agents.json (the built-in default agent is excluded)."""
    try:
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("agents", [])
    except Exception:
        return []


def get_agent(agent_id: str) -> dict:
    """Resolve any agent id, including the built-in default. Unknown ids fall
    back to the default agent so a deleted role never breaks a chat."""
    if agent_id == DEFAULT_AGENT_ID or not agent_id:
        return default_agent()
    for a in list_agents():
        if a["id"] == agent_id:
            return a
    return default_agent()


def _agent_dirs() -> list:
    """Session-folder owners: the default agent plus every team role."""
    return [DEFAULT_AGENT_ID] + [a["id"] for a in list_agents()]


def save_agents(agents: list) -> None:
    """Atomically rewrite agents.json (used by the /team CRUD commands)."""
    with _write_lock:
        tmp = AGENTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"agents": agents}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, AGENTS_FILE)


_AGENT_FIELDS = ("name", "icon", "model", "backend", "system", "effort", "temperature", "max_tokens")
_VALID_BACKENDS = ("openrouter", "claude", "codex", "local")


def _model_matches_backend(backend: str, model: str) -> str:
    """Reject an incompatible backend/model pair (e.g. claude + z-ai/glm-5.2).
    Returns an error string, or '' if the pair is sensible."""
    m = str(model or "").strip().lower()
    if backend == "claude":
        if m in ("sonnet", "opus", "haiku") or m.startswith("claude"):
            return ""
        return f"Claude models are sonnet, opus, haiku, or a claude-* id — not '{model}'"
    if backend == "codex":
        if "codex" in m or m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
            return ""
        return f"Codex models look like gpt-5.2-codex — not '{model}'"
    if backend == "openrouter":
        if "/" in m:
            return ""
        return f"OpenRouter models are namespaced (org/model), e.g. z-ai/glm-5.2 — not '{model}'"
    return ""  # local: whatever start-local.sh serves


def _agent_error(data: dict) -> str:
    if not str(data.get("name", "")).strip():
        return "name is required"
    if not str(data.get("model", "")).strip():
        return "model is required"
    backend = str(data.get("backend", "openrouter")).strip().lower()
    if backend not in _VALID_BACKENDS and not (
            backend.startswith("api:") and _integration(backend[4:])):
        return f"backend must be one of {', '.join(_VALID_BACKENDS)} or a connected api:<integration>"
    mismatch = _model_matches_backend(backend, data.get("model", ""))
    if mismatch:
        return mismatch
    eff = str(data.get("effort", "") or "").strip().lower()
    if eff and eff not in VALID_EFFORT:
        return f"effort must be one of low, medium, high (or blank for default)"
    return ""


def create_agent(data: dict):
    """Add a team agent from the GUI. Returns (agent, error)."""
    err = _agent_error(data)
    if err:
        return None, err
    agents = list_agents()
    base = re.sub(r"[^a-z0-9]+", "-", str(data["name"]).strip().lower()).strip("-") or "agent"
    aid, n = base, 2
    while any(a["id"] == aid for a in agents):
        aid, n = f"{base}{n}", n + 1
    agent = {"id": aid,
             "name": str(data["name"]).strip(),
             "icon": str(data.get("icon") or "🤖").strip() or "🤖",
             "model": str(data["model"]).strip(),
             "system": str(data.get("system") or "You are a helpful assistant.").strip(),
             "backend": str(data.get("backend", "openrouter")).strip().lower(),
             "effort": str(data.get("effort", "") or "").strip().lower(),
             "temperature": str(data.get("temperature", "") or "").strip().lower(),
             "max_tokens": int(data.get("max_tokens") or 0)}
    agents.append(agent)
    save_agents(agents)
    return agent, ""


def update_agent(agent_id: str, data: dict):
    """Edit a team agent's fields. Returns (agent, error). A backend/model
    change drops an 'engine' divider into every conversation the role speaks
    in (its own chats + groups it participates in), so the switch is visible
    exactly where it takes effect."""
    agents = list_agents()
    for a in agents:
        if a["id"] == agent_id:
            old_backend, old_model = a.get("backend", "openrouter"), a.get("model", "")
            merged = {**a, **{k: data[k] for k in _AGENT_FIELDS if k in data}}
            err = _agent_error(merged)
            if err:
                return None, err
            merged["backend"] = str(merged.get("backend", "openrouter")).strip().lower()
            a.clear()
            a.update(merged)
            save_agents(agents)
            if (a.get("backend"), a.get("model")) != (old_backend, old_model):
                _mark_engine_change(agent_id, a.get("backend", "openrouter"), a.get("model", ""))
            return a, ""
    return None, "agent not found"


def _mark_engine_change(role_id: str, backend: str, model: str) -> None:
    """Append a '{role} changed to · backend · model' divider to every
    non-empty conversation this @role answers in."""
    divider = {"role": "divider", "kind": "engine", "who": role_id,
               "backend": backend, "model": model, "ts": int(time.time())}
    try:
        sessions = [(role_id, m["id"]) for m in list_sessions(role_id)]
        sessions += [(DEFAULT_AGENT_ID, g.get("session", "")) for g in list_groups()
                     if role_id in (g.get("participants") or [])]
        for aid, sid in sessions:
            s = get_session(aid, sid)
            if not s or not s.get("messages"):
                continue
            last = s["messages"][-1]
            # collapse repeated engine flips into one divider
            if (last.get("role") == "divider" and last.get("kind") == "engine"
                    and last.get("who") == role_id):
                last.update(divider)
            else:
                s["messages"].append(dict(divider))
            _save_session(s)
    except Exception:
        pass  # a marker must never break the actual edit


def delete_agent(agent_id: str):
    """Remove a team agent (sessions on disk are kept). Returns (ok, error).
    The team can be emptied — the built-in default agent always remains."""
    if agent_id == DEFAULT_AGENT_ID:
        return False, "the default agent cannot be deleted"
    agents = list_agents()
    keep = [a for a in agents if a["id"] != agent_id]
    if len(keep) == len(agents):
        return False, "agent not found"
    save_agents(keep)
    return True, ""


# ── Settings (same settings.json the terminal /settings command uses) ────────

def gui_settings() -> dict:
    import agent_settings
    data = agent_settings.load()
    return {
        "agent": str(data.get("agent", "auto") or "auto"),
        "edit": str(data.get("edit", "on") or "on"),
        "spellcheck": bool(data.get("spellcheck", True)),
        "default_agent": str(data.get("default_agent", DEFAULT_AGENT_ID) or DEFAULT_AGENT_ID),
        # The backend + model a brand-new default chat starts with ("" model =
        # fall back to the backend's first model)
        "default_backend": str(data.get("default_backend", "openrouter") or "openrouter"),
        "default_model": str(data.get("default_model", "") or ""),
        "default_system": str(data.get("default_system", "") or ""),
        "appearance": str(data.get("appearance", "dark") or "dark"),
        "full_disk": bool(data.get("full_disk", False)),
    }


def save_gui_settings(data: dict) -> str:
    """Persist settings keys and apply them to the running server. Returns
    an error string, or "" on success."""
    import agent_settings
    if "agent" in data:
        v = str(data["agent"]).strip().lower()
        if v not in agent_settings.VALID_AGENTS:
            return f"agent must be one of {', '.join(agent_settings.VALID_AGENTS)}"
        agent_settings.set("agent", v)
    if "edit" in data:
        v = str(data["edit"]).strip().lower()
        if v not in agent_settings.VALID_EDIT:
            return f"edit must be one of {', '.join(agent_settings.VALID_EDIT)}"
        agent_settings.set("edit", v)
    if "spellcheck" in data:
        agent_settings.set("spellcheck", bool(data["spellcheck"]))
    if "default_agent" in data:
        v = str(data["default_agent"]).strip()
        if v != DEFAULT_AGENT_ID and not any(a["id"] == v for a in list_agents()):
            return "default_agent must be an existing agent id"
        agent_settings.set("default_agent", v)
    if "default_backend" in data:
        v = str(data["default_backend"]).strip().lower()
        if v not in _DEFAULT_MODELS and not (v.startswith("api:") and _integration(v[4:])):
            return f"backend must be one of {', '.join(_DEFAULT_MODELS)}"
        agent_settings.set("default_backend", v)
    if "default_model" in data:
        agent_settings.set("default_model", str(data["default_model"]).strip())
    if "default_system" in data:
        agent_settings.set("default_system", str(data["default_system"] or "")[:20000])
    if "appearance" in data:
        v = str(data["appearance"]).strip().lower()
        if v not in ("dark", "light", "system"):
            return "appearance must be dark, light or system"
        agent_settings.set("appearance", v)
    if "full_disk" in data:
        agent_settings.set("full_disk", bool(data["full_disk"]))
    # Take effect now (edit mode / startup backend), same precedence as startup
    agent_settings.apply_startup(_REAL_ENV_BACKEND, _REAL_ENV_EDIT)
    return ""


# ── Backends & models (the composer's agent → backend → model picker) ────────
# A curated default per backend; the user can add more models in settings, and
# whatever is configured in .env is always included.

# Only the models the user actually uses are offered by default — anything else
# has to be added deliberately (it costs money), so the picker never suggests a
# model they never picked.
_DEFAULT_MODELS = {
    "openrouter": ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "z-ai/glm-5.2"],
    "claude": ["opus", "sonnet", "haiku"],
    "codex": ["gpt-5.2-codex"],
    "local": ["local-model"],
}
# Version hints shown next to the CLI aliases (they map to the latest release)
MODEL_VERSIONS = {"opus": "4.8", "sonnet": "5", "haiku": "4.5"}
VALID_EFFORT = ("", "low", "medium", "high")
# temperature presets shown in the UI → the numeric value sent to the model
TEMP_PRESETS = {"precise": 0.2, "balanced": 0.7, "creative": 1.0}

def _local_model_from_script() -> str:
    """Derive the local model's friendly name from the `-hf …` line in
    start-local.sh — the launcher IS the source of truth for what the local
    backend runs, so the picker never shows a stale/old model."""
    for p in (os.path.join(CFG_DIR, "start-local.sh"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start-local.sh")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue
        m = re.search(r"-hf\s+([^\s:]+)", txt)
        if m:
            repo = m.group(1).split("/")[-1]                    # Qwen_Qwen3-4B-GGUF
            repo = re.sub(r"[-_]?GGUF$", "", repo, flags=re.I)  # Qwen_Qwen3-4B
            if "_" in repo:
                repo = repo.split("_", 1)[1]                    # drop org → Qwen3-4B
            return repo
    return ""


def _detect_local_model() -> str:
    """The local model name for the picker. Priority: LOCAL_MODEL env override →
    the configured launcher (start-local.sh) → whatever a running llama-server
    reports → 'local-model'. Never a hardcoded or stale value."""
    env = os.environ.get("LOCAL_MODEL", "").strip()
    if env:
        return env
    scripted = _local_model_from_script()
    if scripted:
        return scripted
    # no launcher found — fall back to whatever a running llama-server reports
    try:
        with urlreq.urlopen("http://localhost:8080/v1/models", timeout=1.0) as r:
            items = (json.loads(r.read().decode("utf-8")) or {}).get("data") or []
        if items:
            raw = str(items[0].get("id") or "")
            name = os.path.basename(raw) or raw
            return name[:-5] if name.lower().endswith(".gguf") else name
    except Exception:
        pass
    return "local-model"
_BACKEND_LABELS = {
    "openrouter": "OpenRouter", "claude": "Claude Code",
    "codex": "Codex", "local": "Local model",
}


def list_backends() -> list:
    """[{id, label, available, models:[...]}] for the composer picker. A backend
    is 'available' when its key/binary is present, but all are listed so the
    user can still pick one and see why it needs setup."""
    import shutil
    import agent_settings
    extra = agent_settings.load().get("models", {}) or {}
    env_or = os.environ.get("OPENROUTER_MODEL", "").strip()
    avail = {
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "claude": bool(shutil.which("claude")),
        "codex": bool(shutil.which("codex")),
        "local": True,
    }
    out = []
    for bid in ("openrouter", "claude", "codex", "local"):
        models = list(_DEFAULT_MODELS[bid])
        if bid == "local":
            models = [_detect_local_model()]
        if bid == "openrouter" and env_or and env_or not in models:
            models.insert(0, env_or)
        for m in extra.get(bid, []) or []:
            if m not in models:
                models.append(m)
        out.append({"id": bid, "label": _BACKEND_LABELS[bid],
                    "available": avail[bid], "models": models})
    # user-connected API integrations join the picker as their own backends
    for it in list_integrations(mask=False):
        bid = f"api:{it['id']}"
        models = integration_models(it)
        for m in extra.get(bid, []) or []:
            if m not in models:
                models.append(m)
        out.append({"id": bid, "label": it["name"], "available": True,
                    "models": models})
    return out


# The FULL OpenRouter catalog ("found 337 models" style) for autocomplete in
# the model pickers — fetched once, cached in memory + on disk so it also
# works offline. The curated _DEFAULT_MODELS stays the picker's short list.
_OR_CATALOG_FILE = os.path.join(CFG_DIR, ".cache-openrouter-models.json")
_OR_CATALOG = {"ts": 0.0, "models": []}
_OR_CATALOG_TTL = 6 * 3600


def openrouter_catalog(refresh: bool = False) -> dict:
    """{count, models:[{id, name}]} — every model OpenRouter serves."""
    now = time.time()
    if not refresh and _OR_CATALOG["models"] and now - _OR_CATALOG["ts"] < _OR_CATALOG_TTL:
        return {"count": len(_OR_CATALOG["models"]), "models": _OR_CATALOG["models"]}
    if not refresh and not _OR_CATALOG["models"]:
        try:
            with open(_OR_CATALOG_FILE, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if disk.get("models") and now - float(disk.get("ts", 0)) < _OR_CATALOG_TTL:
                _OR_CATALOG.update(disk)
                return {"count": len(disk["models"]), "models": disk["models"]}
        except Exception:
            pass
    try:
        req = urlreq.Request("https://openrouter.ai/api/v1/models",
                             headers={"HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"})
        with urlreq.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = sorted(
            ({"id": str(m.get("id") or ""), "name": str(m.get("name") or "")}
             for m in (data.get("data") or []) if m.get("id")),
            key=lambda m: m["id"])
        if models:
            _OR_CATALOG.update({"ts": now, "models": models})
            try:
                with open(_OR_CATALOG_FILE, "w", encoding="utf-8") as f:
                    json.dump(_OR_CATALOG, f)
            except Exception:
                pass
    except Exception:
        pass  # offline / API down → whatever we had (possibly stale or empty)
    return {"count": len(_OR_CATALOG["models"]), "models": _OR_CATALOG["models"]}


# ── Custom API integrations (Settings → Integrations) ────────────────────────
# OpenAI-compatible endpoints the user connects themselves: name, base URL,
# auth style, header name, API key. Each appears in the engine picker as
# backend id "api:<id>"; its /models list is probed and cached.

_INTEG_MODELS_CACHE = {}  # iid → (ts, [model ids])


def list_integrations(mask: bool = True) -> list:
    import agent_settings
    out = []
    for it in agent_settings.load().get("integrations", []) or []:
        d = dict(it)
        if mask and d.get("api_key"):
            d["api_key"] = "•••" + str(d["api_key"])[-4:]
        out.append(d)
    return out


def _integration(iid: str):
    import agent_settings
    for it in agent_settings.load().get("integrations", []) or []:
        if it.get("id") == iid:
            return it
    return None


def add_integration(data: dict):
    import agent_settings
    name = str(data.get("name") or "").strip()
    base = str(data.get("base_url") or "").strip().rstrip("/")
    if not name:
        return None, "name is required"
    if not base.startswith("http"):
        return None, "base URL must start with http(s) — e.g. https://api.groq.com/openai/v1"
    it = {
        "id": uuid.uuid4().hex[:8],
        "name": name[:60],
        "base_url": base,
        "auth": "bearer" if str(data.get("auth") or "bearer").lower() != "plain" else "plain",
        "header": str(data.get("header") or "Authorization").strip() or "Authorization",
        "api_key": str(data.get("api_key") or "").strip(),
    }
    s = agent_settings.load()
    lst = s.get("integrations", []) or []
    lst.append(it)
    agent_settings.set("integrations", lst)
    return {**it, "api_key": ("•••" + it["api_key"][-4:]) if it["api_key"] else ""}, ""


def delete_integration(iid: str):
    import agent_settings
    s = agent_settings.load()
    lst = [it for it in (s.get("integrations", []) or []) if it.get("id") != iid]
    agent_settings.set("integrations", lst)
    _INTEG_MODELS_CACHE.pop(iid, None)
    return True, ""


def _integration_headers(it: dict) -> dict:
    key = it.get("api_key") or ""
    if not key:
        return {}
    name = it.get("header") or "Authorization"
    bearer = it.get("auth", "bearer") == "bearer" and name.lower() == "authorization"
    return {name: f"Bearer {key}" if bearer else key}


def integration_models(it: dict) -> list:
    """Model ids the endpoint reports on GET <base>/models (OpenAI-compatible),
    cached 5 min; [] when unreachable."""
    iid = it.get("id", "")
    ts, models = _INTEG_MODELS_CACHE.get(iid, (0, []))
    if time.time() - ts < 300:
        return models
    models = []
    try:
        req = urlreq.Request(it["base_url"].rstrip("/") + "/models",
                             headers=_integration_headers(it))
        with urlreq.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = sorted(str(m.get("id")) for m in (data.get("data") or []) if m.get("id"))
    except Exception:
        models = []
    _INTEG_MODELS_CACHE[iid] = (time.time(), models)
    return models


def add_model(backend: str, model: str) -> str:
    import agent_settings
    backend = str(backend).strip().lower()
    model = str(model).strip()
    if backend not in _DEFAULT_MODELS and not (
            backend.startswith("api:") and _integration(backend[4:])):
        return "unknown backend"
    if not model:
        return "model is required"
    data = agent_settings.load()
    models = data.get("models", {}) or {}
    lst = models.setdefault(backend, [])
    if model not in lst and model not in _DEFAULT_MODELS.get(backend, []):
        lst.append(model)
        agent_settings.set("models", models)
    return ""


# ── .env config (edit keys from the GUI instead of a text editor) ────────────

# Curated, editable keys. `secret` values are masked on read so the API key is
# never shipped back to the browser in the clear.
_ENV_KEYS = [
    {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API key",
     "secret": True, "help": "Powers the OpenRouter backend. Get one at openrouter.ai/keys."},
    {"key": "OPENROUTER_MODEL", "label": "Default OpenRouter model",
     "secret": False, "help": "Model the default agent uses, e.g. deepseek/deepseek-v4-flash."},
]
_ENV_PATH = os.path.join(CFG_DIR, ".env")


def _parse_env_file() -> dict:
    """Read the raw key→value pairs from ~/.config/orkesai/.env (no env merge)."""
    out = {}
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                key, _, val = s.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip()
                if not val.startswith(('"', "'")):
                    val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
                out[key] = val.strip('"').strip("'")
    except Exception:
        pass
    return out


def read_env_config() -> list:
    """[{key,label,secret,help,set,value}] — secret values masked, never leaked."""
    raw = _parse_env_file()
    out = []
    for spec in _ENV_KEYS:
        v = raw.get(spec["key"], "")
        item = dict(spec)
        item["set"] = bool(v)
        # never return the real secret; a masked hint is enough for the UI
        item["value"] = ("•" * 8 + v[-4:]) if (spec["secret"] and v) else ("" if spec["secret"] else v)
        out.append(item)
    return out


def save_env_config(patch: dict) -> str:
    """Update editable keys in .env, preserving comments and other lines.
    An empty string clears a key; a masked value (starts with •) is ignored so
    re-saving the form doesn't wipe a secret the browser never saw."""
    allowed = {s["key"]: s for s in _ENV_KEYS}
    updates = {}
    for k, v in (patch or {}).items():
        if k not in allowed:
            continue
        v = str(v if v is not None else "")
        if v.startswith("•"):
            continue  # untouched masked secret — leave the file as-is
        updates[k] = v.strip()
    if not updates:
        return ""
    lines = []
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        pass
    seen = set()
    out_lines = []
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = m.group(1) if m else None
        if key in updates:
            seen.add(key)
            v = updates[key]
            if v == "":
                continue  # drop the line to clear the key
            out_lines.append(f"{key}={v}")
        else:
            out_lines.append(line)
    for key, v in updates.items():
        if key not in seen and v != "":
            out_lines.append(f"{key}={v}")
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = _ENV_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines).rstrip("\n") + "\n")
        os.replace(tmp, _ENV_PATH)
    except Exception as e:
        return f"cannot write .env: {e}"
    # apply live so the running server picks up the new key/model immediately
    for key, v in updates.items():
        if v:
            os.environ[key] = v
        else:
            os.environ.pop(key, None)
    return ""


# ── Projects (same directories the terminal /project command uses) ──────────

PROJECTS_ROOT = os.path.join(CFG_DIR, "projects")


def list_projects() -> list:
    try:
        names = sorted(d for d in os.listdir(PROJECTS_ROOT)
                       if os.path.isdir(os.path.join(PROJECTS_ROOT, d)) and d != "database")
    except Exception:
        names = []
    return [{"name": n, "path": os.path.join(PROJECTS_ROOT, n)} for n in names]


def _project_meta_path(path: str) -> str:
    return os.path.join(path, ".orkesai", "project.json")


def _load_project_meta(path: str) -> dict:
    try:
        with open(_project_meta_path(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _project_view(name: str, path: str) -> dict:
    meta = _load_project_meta(path)
    files = []
    fdir = os.path.join(path, ".orkesai", "files")
    try:
        files = sorted(os.listdir(fdir))
    except Exception:
        pass
    # count sessions attached to this project
    chats = sum(1 for s in list_sessions() if s.get("project") == path)
    return {
        "name": name, "path": path,
        "description": meta.get("description", ""),
        "instructions": meta.get("instructions", ""),
        "files": files, "chats": chats,
    }


def list_projects() -> list:
    try:
        names = sorted(d for d in os.listdir(PROJECTS_ROOT)
                       if os.path.isdir(os.path.join(PROJECTS_ROOT, d)) and d != "database")
    except Exception:
        names = []
    return [_project_view(n, os.path.join(PROJECTS_ROOT, n)) for n in names]


def create_project(name: str, description: str = ""):
    """Create a project folder. Returns (project, error)."""
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(name or "").strip()).strip(". ")
    if not name or name == "database":
        return None, "invalid project name"
    path = os.path.join(PROJECTS_ROOT, name)
    try:
        os.makedirs(os.path.join(path, ".orkesai", "files"), exist_ok=True)
    except Exception as e:
        return None, f"cannot create project: {e}"
    if description:
        _save_project_meta(path, {"description": description})
    return _project_view(name, path), ""


def _save_project_meta(path: str, patch: dict) -> None:
    meta = _load_project_meta(path)
    meta.update(patch)
    os.makedirs(os.path.dirname(_project_meta_path(path)), exist_ok=True)
    tmp = _project_meta_path(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _project_meta_path(path))


def _project_by_name(name: str) -> str:
    path = os.path.join(PROJECTS_ROOT, name)
    return path if os.path.isdir(path) else ""


def update_project(name: str, data: dict):
    """Patch description/instructions, and/or rename the project. When "name" is
    given and differs, the folder is renamed and attached chats repointed.
    Returns (project, error)."""
    path = _project_by_name(name)
    if not path:
        return None, "project not found"
    # Rename first, so the meta patch below lands on the new folder
    if "name" in data:
        new_name = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(data["name"] or "").strip()).strip(". ")
        if not new_name or new_name == "database":
            return None, "invalid project name"
        if new_name != name:
            new_path = os.path.join(PROJECTS_ROOT, new_name)
            if os.path.exists(new_path):
                return None, "a project with that name already exists"
            try:
                os.rename(path, new_path)
            except Exception as e:
                return None, f"cannot rename: {e}"
            # repoint every chat that pointed at the old folder
            for s in list_sessions():
                if s.get("project") == path:
                    full = get_session(s["agent"], s["id"])
                    if full:
                        full["project"] = new_path
                        _save_session(full)
            name, path = new_name, new_path
    patch = {}
    if "description" in data:
        patch["description"] = str(data["description"] or "")[:2000]
    if "instructions" in data:
        patch["instructions"] = str(data["instructions"] or "")[:20000]
    if patch:
        _save_project_meta(path, patch)
    return _project_view(name, path), ""


def delete_project(name: str):
    """Remove a project folder and detach its chats. Returns (ok, error)."""
    import shutil
    path = _project_by_name(name)
    if not path:
        return False, "project not found"
    for s in list_sessions():
        if s.get("project") == path:
            full = get_session(s["agent"], s["id"])
            if full:
                full["project"] = ""
                _save_session(full)
    try:
        shutil.rmtree(path)
    except Exception as e:
        return False, f"cannot delete: {e}"
    return True, ""


def add_project_file(name: str, filename: str, data_url: str):
    """Save an attached file into the project's .orkesai/files. Returns (ok, error)."""
    import base64
    path = _project_by_name(name)
    if not path:
        return None, "project not found"
    safe = os.path.basename(str(filename or "")).strip() or "file"
    _, _, b64 = str(data_url or "").partition(",")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None, "invalid file data"
    fdir = os.path.join(path, ".orkesai", "files")
    os.makedirs(fdir, exist_ok=True)
    with open(os.path.join(fdir, safe), "wb") as f:
        f.write(raw)
    return _project_view(name, path), ""


def delete_project_file(name: str, filename: str):
    path = _project_by_name(name)
    if not path:
        return None, "project not found"
    target = os.path.join(path, ".orkesai", "files", os.path.basename(filename))
    try:
        os.remove(target)
    except Exception:
        pass
    return _project_view(name, path), ""


# ── Sessions ─────────────────────────────────────────────────────────────────

def _session_path(agent_id: str, session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, agent_id, f"{session_id}.json")


def create_session(agent_id: str, title: str = "", project: str = "",
                   backend: str = "", model: str = "", effort: str = "",
                   temperature: str = "", max_tokens: int = 0) -> dict:
    agent = get_agent(agent_id)
    sid = uuid.uuid4().hex[:12]
    session = {
        "id": sid,
        "agent": agent["id"],
        # A per-session backend/model override lets the default chat switch
        # /agent/model from the composer without touching the agent config
        "backend": (backend or "").strip().lower(),
        "model": (model or "").strip() or agent["model"],
        "effort": (effort or "").strip().lower(),
        "temperature": (temperature or "").strip().lower(),
        "max_tokens": int(max_tokens or 0),
        "title": title or "New chat",
        "project": project or "",
        "created": int(time.time()),
        "updated": int(time.time()),
        "usage": {"in": 0, "out": 0},
        "messages": [],
    }
    _save_session(session)
    return session


def update_session(session_id: str, data: dict):
    """Patch a session's backend/model/project/title, or delegate it to another
    persona in place. Returns (session, error)."""
    s = find_session(session_id)
    if not s:
        return None, "session not found"
    old_agent = s["agent"]
    # Delegate this thread to another @role in place: keep the history + project,
    # drop a handoff divider, and adopt the new persona's engine (still
    # overridable via an explicit backend/model in the same request)
    if "agent" in data:
        new_id = str(data["agent"]).strip() or DEFAULT_AGENT_ID
        if new_id != s["agent"]:
            if new_id != DEFAULT_AGENT_ID and not any(a["id"] == new_id for a in list_agents()):
                return None, "agent not found"
            na = get_agent(new_id)
            s["messages"].append({
                "role": "divider", "from": old_agent, "to": new_id,
                "backend": na.get("backend", "openrouter"),
                "model": na.get("model", ""),
                "ts": int(time.time()),
            })
            s["agent"] = new_id
            # engine follows the new persona by default
            s["backend"] = na.get("backend", "openrouter")
            s["model"] = na.get("model", "")
    if "backend" in data:
        s["backend"] = str(data["backend"] or "").strip().lower()
    if "model" in data:
        s["model"] = str(data["model"] or "").strip() or s.get("model", "")
    if "effort" in data:
        v = str(data["effort"] or "").strip().lower()
        s["effort"] = v if v in VALID_EFFORT else ""
    if "temperature" in data:
        v = str(data["temperature"] or "").strip().lower()
        s["temperature"] = v if v in TEMP_PRESETS else ""
    if "max_tokens" in data:
        try:
            s["max_tokens"] = max(0, int(data["max_tokens"] or 0))
        except (TypeError, ValueError):
            s["max_tokens"] = 0
    if "project" in data:
        s["project"] = str(data["project"] or "")
    if "title" in data and str(data["title"]).strip():
        s["title"] = str(data["title"]).strip()[:80]
    if "truncate" in data:
        # Edit & resend: drop this user turn and everything after it so the
        # edited message becomes the newest turn again
        try:
            n = max(0, int(data["truncate"]))
            s["messages"] = s["messages"][:n]
        except Exception:
            pass
    _save_session(s)
    # a delegated session now lives in the new persona's folder — remove the
    # stale copy from the old one so it isn't listed twice
    if old_agent != s["agent"]:
        try:
            os.remove(_session_path(old_agent, s["id"]))
        except Exception:
            pass
    return s, ""


# ── Groups (one chat, several @role participants) ────────────────────────────
# A group owns ONE session (stored under the default agent, flagged with
# session["group"]). Every participant is a team @role; replies are
# orchestrated by stream_group_chat below.

GROUPS_FILE = os.path.join(CFG_DIR, "groups.json")


def list_groups() -> list:
    try:
        with open(GROUPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("groups", [])
    except Exception:
        return []


def get_group(gid: str):
    for g in list_groups():
        if g.get("id") == gid:
            return g
    return None


def _save_groups(groups: list) -> None:
    with _write_lock:
        tmp = GROUPS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"groups": groups}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, GROUPS_FILE)


def _clean_participants(parts) -> list:
    valid = {a["id"] for a in list_agents()}
    return [p for p in dict.fromkeys(str(x).strip() for x in (parts or [])) if p in valid]


def create_group(data: dict):
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "name is required"
    participants = _clean_participants(data.get("participants"))
    if not participants:
        return None, "pick at least one @role participant"
    gid = uuid.uuid4().hex[:10]
    # the group's single conversation — a default-agent session flagged as group
    sess = create_session(DEFAULT_AGENT_ID, title=name)
    sess["group"] = gid
    _save_session(sess)
    g = {"id": gid, "name": name[:80], "icon": str(data.get("icon") or "👥")[:8],
         "participants": participants, "session": sess["id"], "created": int(time.time())}
    groups = list_groups()
    groups.insert(0, g)
    _save_groups(groups)
    return g, ""


def update_group(gid: str, data: dict):
    groups = list_groups()
    for g in groups:
        if g.get("id") == gid:
            if "name" in data and str(data["name"]).strip():
                g["name"] = str(data["name"]).strip()[:80]
            if "icon" in data:
                g["icon"] = str(data.get("icon") or "👥")[:8]
            if "participants" in data:
                parts = _clean_participants(data.get("participants"))
                if not parts:
                    return None, "pick at least one @role participant"
                g["participants"] = parts
            _save_groups(groups)
            return g, ""
    return None, "group not found"


def delete_group(gid: str):
    g = get_group(gid)
    if g:
        delete_session(DEFAULT_AGENT_ID, g.get("session", ""))
    _save_groups([x for x in list_groups() if x.get("id") != gid])
    return True, ""


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
    for aid in _agent_dirs():
        s = get_session(aid, session_id)
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
    ids = [agent_id] if agent_id else _agent_dirs()
    for aid in ids:
        d = os.path.join(SESSIONS_DIR, aid)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                    s = json.load(f)
                meta = {k: s[k] for k in
                        ("id", "agent", "model", "title", "created", "updated", "usage")}
                meta["project"] = s.get("project", "")
                meta["group"] = s.get("group", "")
                meta["backend"] = s.get("backend", "")
                meta["effort"] = s.get("effort", "")
                meta["temperature"] = s.get("temperature", "")
                meta["max_tokens"] = s.get("max_tokens", 0)
                out.append(meta)
            except Exception:
                continue
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


# ── Usage / skills / MCP / databases (settings panels) ──────────────────────

def usage_summary(rng: str = "all") -> dict:
    """Aggregated spend for the Usage panel. rng: week|month|year|all filters
    the per-day ledger; totals are per model plus an OpenRouter balance."""
    import agent_usage
    data = agent_usage._load()
    daily = data.get("daily", {})
    days = sorted(daily.keys())
    now = time.time()
    span = {"week": 7, "month": 31, "year": 365}.get(rng)
    if span:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(now - span * 86400))
        days = [d for d in days if d >= cutoff]
    models = {}
    series = []
    for d in days:
        day_in = day_out = day_req = 0
        day_cost = 0.0
        for name, m in daily[d].items():
            agg = models.setdefault(name, {"req": 0, "in": 0, "out": 0, "cost": 0.0})
            for k in ("req", "in", "out"):
                agg[k] += m.get(k, 0)
                if k == "req":
                    day_req += m.get("req", 0)
            agg["cost"] += m.get("cost", 0.0)
            day_in += m.get("in", 0)
            day_out += m.get("out", 0)
            day_cost += m.get("cost", 0.0)
        series.append({"day": d, "in": day_in, "out": day_out,
                       "req": day_req, "cost": round(day_cost, 6)})
    if rng == "all":  # all-time totals live in the "models" bucket
        models = {k: dict(v) for k, v in data.get("models", {}).items()}
    model_list = [{"model": k, **v} for k, v in
                  sorted(models.items(), key=lambda kv: -kv[1].get("cost", 0.0))]
    total = {
        "in": sum(m["in"] for m in model_list),
        "out": sum(m["out"] for m in model_list),
        "req": sum(m["req"] for m in model_list),
        "cost": round(sum(m["cost"] for m in model_list), 6),
    }
    credits = agent_usage.fetch_openrouter_credits() or {}
    bal = None
    if credits:
        tot = credits.get("total_credits") or 0.0
        used = credits.get("total_usage") or 0.0
        bal = {"total": round(tot, 4), "used": round(used, 4),
               "left": round(max(0.0, tot - used), 4)}
    return {"range": rng, "models": model_list, "total": total,
            "series": series, "balance": bal}


_SKILLS_DIR = os.path.join(CFG_DIR, "skills")


def list_skills() -> list:
    import agent_skills
    cats = agent_skills.list_skills(_SKILLS_DIR)
    out = []
    for cat, names in cats.items():
        for n in names:
            path = find_skill_file(_SKILLS_DIR, n)
            ok, detail = False, "file missing"
            if path and os.path.isfile(path):
                try:
                    ok = os.path.getsize(path) > 0
                    detail = "installed" if ok else "empty file"
                except Exception:
                    detail = "unreadable"
            out.append({"name": n, "category": cat, "ok": ok, "detail": detail})
    return out


def add_skill(name: str, source: str):
    import agent_skills
    try:
        msg = agent_skills.install_skill(name, source, _SKILLS_DIR)
        return {"message": msg}, ""
    except Exception as e:
        return None, str(e)


def remove_skill(name: str):
    import agent_skills
    return (True, "") if agent_skills.remove_skill(name, _SKILLS_DIR) else (False, "skill not found")


def list_mcp() -> list:
    try:
        import mcp_client
        servers = mcp_client.load_servers()  # {name: {command|url}}
    except Exception:
        servers = {}
    import shutil
    out = []
    for k, v in servers.items():
        cmd = (" ".join(v["command"]) if isinstance(v.get("command"), list)
               else (v.get("command") or v.get("url") or ""))
        if v.get("url"):
            ok = str(v["url"]).startswith("http")
            detail = "http endpoint" if ok else "bad url"
        else:
            bin0 = (v["command"][0] if isinstance(v.get("command"), list) and v["command"]
                    else str(cmd).split()[0] if cmd else "")
            ok = bool(bin0) and shutil.which(bin0) is not None
            detail = "ready" if ok else (f"'{bin0}' not on PATH" if bin0 else "no command")
        out.append({"name": k, "command": cmd, "ok": ok, "detail": detail})
    return out


def add_mcp(name: str, command: str):
    try:
        import mcp_client
        servers = mcp_client.load_servers()
        name = str(name).strip()
        if not name or not str(command).strip():
            return None, "name and command are required"
        entry = ({"url": command.strip()} if command.strip().startswith("http")
                 else {"command": command.split()})
        servers[name] = entry
        mcp_client.save_servers(servers)
        return {"name": name}, ""
    except Exception as e:
        return None, str(e)


def remove_mcp(name: str):
    try:
        import mcp_client
        servers = mcp_client.load_servers()
        if name in servers:
            del servers[name]
            mcp_client.save_servers(servers)
            return True, ""
        return False, "server not found"
    except Exception as e:
        return False, str(e)


def list_databases() -> list:
    """The SQLite files under projects/database, with size and row counts."""
    import sqlite3
    ddir = os.path.join(CFG_DIR, "projects", "database")
    out = []
    try:
        names = sorted(os.listdir(ddir))
    except Exception:
        names = []
    for n in names:
        full = os.path.join(ddir, n)
        try:
            size = os.path.getsize(full)
        except Exception:
            continue
        info = {"name": n, "size": size, "tables": []}
        if n.endswith(".db"):
            try:
                con = sqlite3.connect(f"file:{full}?mode=ro", uri=True)
                for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    try:
                        c = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    except Exception:
                        c = 0
                    info["tables"].append({"table": t, "rows": c})
                con.close()
            except Exception:
                pass
        elif n.endswith(".json"):
            info["kind"] = "ledger"
        out.append(info)
    return out


def list_agent_files(agent_id: str, only_session: str = "") -> list:
    """File/image references. For an @role: across ALL its sessions (its shared
    workspace). For the default agent: pass only_session to scope to that one
    conversation (a default chat has no role to share with)."""
    out = []
    for meta in list_sessions(agent_id or DEFAULT_AGENT_ID):
        if only_session and meta["id"] != only_session:
            continue
        s = get_session(meta["agent"], meta["id"])
        if not s:
            continue
        title = s.get("title", "") or "Untitled"
        for m in s.get("messages", []):
            if m.get("role") not in ("user", "assistant"):
                continue
            ts = m.get("ts") or s.get("updated", 0)
            for url in (m.get("images") or []):
                out.append({"kind": "image", "name": "", "type": "image", "url": url,
                            "session": s["id"], "title": title, "ts": ts,
                            "generated": m.get("role") == "assistant"})
            for a in (m.get("attachments") or []):
                out.append({"kind": "file", "name": a.get("name", "file"),
                            "type": a.get("type", ""), "session": s["id"],
                            "title": title, "ts": ts, "generated": False})
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:80]


# ── Conversation context: Notes + Links (right panel) ───────────────────────
# Scope: a team @role shares one context across ALL its chats (its memory); the
# default agent scopes context to the single conversation ("session:<id>").

NOTES_DIR = os.path.join(CFG_DIR, ".notes")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _scope_of(agent_id: str, session_id: str):
    is_role = agent_id != DEFAULT_AGENT_ID and any(a["id"] == agent_id for a in list_agents())
    return (agent_id if is_role else f"session:{session_id}"), is_role


def _notes_path(scope: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_:.-]+", "_", scope)
    return os.path.join(NOTES_DIR, safe + ".json")


def _load_ctx(scope: str) -> dict:
    try:
        with open(_notes_path(scope), "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("notes", [])
    d.setdefault("links", [])
    d.setdefault("ai_auto", False)
    return d


def _save_ctx(scope: str, d: dict) -> None:
    os.makedirs(NOTES_DIR, exist_ok=True)
    tmp = _notes_path(scope) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _notes_path(scope))


def create_note(scope: str, title: str, body: str, source: str = "manual"):
    d = _load_ctx(scope)
    note = {"id": uuid.uuid4().hex[:10], "title": str(title or "Note")[:200],
            "body": str(body or "")[:20000], "source": source,
            "created": int(time.time()), "updated": int(time.time())}
    d["notes"].insert(0, note)
    _save_ctx(scope, d)
    return note, ""


def update_note(scope: str, note_id: str, data: dict):
    d = _load_ctx(scope)
    for n in d["notes"]:
        if n["id"] == note_id:
            if "title" in data:
                n["title"] = str(data["title"] or "")[:200]
            if "body" in data:
                n["body"] = str(data["body"] or "")[:20000]
            n["updated"] = int(time.time())
            _save_ctx(scope, d)
            return n, ""
    return None, "note not found"


def delete_note(scope: str, note_id: str):
    d = _load_ctx(scope)
    d["notes"] = [n for n in d["notes"] if n["id"] != note_id]
    _save_ctx(scope, d)
    return True, ""


def set_notes_auto(scope: str, on: bool):
    d = _load_ctx(scope)
    d["ai_auto"] = bool(on)
    _save_ctx(scope, d)
    return True, ""


def export_notes(scope: str, fmt: str, note_id: str = ""):
    """Render notes as a downloadable document — one note (note_id set, file
    named after its title) or the whole scope. Returns ({data, mime, filename},
    error) — data is raw bytes for the HTTP layer."""
    d = _load_ctx(scope)
    notes, name = d["notes"], ""
    if note_id:
        notes = [n for n in notes if n["id"] == note_id]
        if not notes:
            return None, "note not found"
        name = notes[0].get("title", "")
    if not notes:
        return None, "no notes to export yet"
    import notes_export
    try:
        data, mime, filename = notes_export.render(notes, fmt, scope, name)
    except ValueError as e:
        return None, str(e)
    except Exception as e:
        return None, f"export failed: {e}"
    return {"data": data, "mime": mime, "filename": filename}, ""


# write_file targets that are really the model trying to save "notes" as a
# loose file (…-notes.md, note.txt, conversation_notes.txt) — the app's Notes
# panel is the only sanctioned place for AI notes, so these get intercepted.
_NOTE_FILE_RE = re.compile(r"(^|[-_ .])notes?([-_ .]|$)", re.I)


def _is_note_file(path: str) -> bool:
    return bool(_NOTE_FILE_RE.search(os.path.basename(str(path or ""))))


def add_link(scope: str, url: str, title: str = ""):
    url = str(url or "").strip()
    if not url.startswith("http"):
        return None, "link must be an http(s) URL"
    d = _load_ctx(scope)
    link = {"id": uuid.uuid4().hex[:10], "url": url[:2000],
            "title": (str(title).strip() or _fetch_meta_title(url))[:200],
            "ts": int(time.time()), "source": "manual"}
    d["links"].insert(0, link)
    _save_ctx(scope, d)
    return link, ""


def delete_link(scope: str, link_id: str):
    d = _load_ctx(scope)
    d["links"] = [l for l in d["links"] if l["id"] != link_id]
    _save_ctx(scope, d)
    return True, ""


_LINK_TITLE_CACHE = {}


def _fetch_meta_title(url: str) -> str:
    """The page's own title (og:title or <title>), so a link shows what it IS —
    not the conversation title. Cached; empty string on any failure (offline,
    timeout) so the UI falls back to showing the URL."""
    if url in _LINK_TITLE_CACHE:
        return _LINK_TITLE_CACHE[url]
    title = ""
    try:
        req = urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0 (OrkesAI link preview)"})
        with urlreq.urlopen(req, timeout=3) as r:
            raw = r.read(80000).decode("utf-8", errors="ignore")
        m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title', raw, re.I)
             or re.search(r"<title[^>]*>([^<]+)</title>", raw, re.I))
        if m:
            import html as _html
            title = _html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())[:120]
    except Exception:
        title = ""
    _LINK_TITLE_CACHE[url] = title
    return title


def _auto_links(agent_id: str, only_session: str = "") -> list:
    """URLs the user or AI mentioned in the conversation(s), de-duped, each
    labelled with the linked page's OWN title."""
    seen, out = set(), []
    for meta in list_sessions(agent_id or DEFAULT_AGENT_ID):
        if only_session and meta["id"] != only_session:
            continue
        s = get_session(meta["agent"], meta["id"]) or {}
        for m in s.get("messages", []):
            if m.get("role") not in ("user", "assistant"):
                continue
            for u in _URL_RE.findall(m.get("text") or m.get("content") or ""):
                u = u.rstrip(".,);]")
                if u not in seen:
                    seen.add(u)
                    out.append({"url": u, "title": "", "session": s["id"], "source": "chat"})
    # fetch page titles for the first several (cached; short timeout)
    for l in out[:10]:
        l["title"] = _fetch_meta_title(l["url"])
    return out[:60]


def context_view(agent_id: str, session_id: str) -> dict:
    """Everything the right panel needs for a conversation: files, notes, links
    and the ai-notes toggle, scoped by @role vs individual default chat."""
    scope, is_role = _scope_of(agent_id, session_id)
    d = _load_ctx(scope)
    files = list_agent_files(agent_id, "" if is_role else session_id)
    links = list(d["links"]) + _auto_links(agent_id, "" if is_role else session_id)
    return {"scope": scope, "is_role": is_role, "files": files,
            "notes": d["notes"], "links": links, "ai_auto": d["ai_auto"]}


def summarize_to_note(agent_id: str, session_id: str, source: str = "ai", mode: str = "summary"):
    """Save a note from a conversation (cheap flash call). mode="summary" (the
    'Summarize this chat' button) writes a TL;DR of the whole chat; mode="content"
    (auto-notes) writes the actual useful CONTENT the assistant produced (the
    recipe/list/facts itself), not a description of the conversation. Content mode
    looks at only the LATEST exchange and keeps ONE NOTE PER TOPIC — a new topic
    becomes a new note, a follow-up on an existing topic updates that note."""
    scope, _ = _scope_of(agent_id, session_id)
    s = get_session(agent_id, session_id) or {}
    msgs = [m for m in s.get("messages", []) if m.get("role") in ("user", "assistant")]
    if mode == "content":
        # Only the last user→assistant exchange, so unrelated earlier topics
        # (e.g. a recipe from 10 turns ago) never bleed into this note.
        pair = []
        for m in reversed(msgs):
            pair.append(m)
            if m["role"] == "user" and any(x["role"] == "assistant" for x in pair):
                break
        pair.reverse()
        convo = "\n".join(
            f"{m['role']}: {(m.get('text') or m.get('content') or '')[:3000]}"
            for m in pair)[:7000]
    else:
        convo = "\n".join(
            f"{m['role']}: {(m.get('text') or m.get('content') or '')[:800]}"
            for m in msgs)[:7000]
    if not convo.strip():
        return None, "nothing to summarize yet"
    okey = os.environ.get("OPENROUTER_API_KEY")
    if not okey:
        return None, "an OpenRouter key is needed to summarize"
    if mode == "content":
        existing_titles = "; ".join(
            n["title"] for n in _load_ctx(scope)["notes"]
            if n.get("source") == "ai-auto")[:600]
        prompt = ("From this exchange, extract the useful CONTENT the assistant produced "
                  "(the recipe, list, steps, facts, or code — the actual thing itself) and write "
                  "it as a clean, standalone note the user can re-read and reuse later. Write the "
                  "content ITSELF in a clear format — do NOT describe the conversation or say who "
                  "asked what. The FIRST line must be 'TITLE: <short topic name>' naming what the "
                  "note is about, then the note body. No other preamble."
                  + (f"\nExisting note topics: {existing_titles}. If this exchange is about the "
                     "SAME topic as one of them, reuse that exact title; otherwise pick a new one."
                     if existing_titles else "")
                  + "\n\n" + convo + "\n\nNote:")
    else:
        prompt = ("Summarize this conversation as concise notes: first a one-line "
                  "TL;DR, then 3–6 key bullet points. No preamble.\n\n" + convo + "\n\nNotes:")
    body = {"model": "deepseek/deepseek-v4-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400, "temperature": 0.3}
    try:
        req = urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {okey}",
                     "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
            method="POST")
        with urlreq.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception as e:
        return None, f"summarize failed: {e}"
    text = text.strip()
    if not text:
        return None, "summarize returned nothing"
    if mode == "content":
        title = (s.get("title") or "Notes")[:60]
        first, _, rest = text.partition("\n")
        if first.strip().lower().startswith("title:"):
            title = first.split(":", 1)[1].strip()[:60] or title
            text = rest.strip()
        if not text:
            return None, "summarize returned nothing"
        # Upsert per topic: a follow-up on an existing ai-auto topic updates that
        # note instead of spawning duplicates; a new topic gets its own note.
        if source == "ai-auto":
            d = _load_ctx(scope)
            for n in d["notes"]:
                if (n.get("source") == "ai-auto"
                        and n["title"].strip().lower() == title.strip().lower()):
                    if text in n["body"]:
                        return n, ""
                    body = (n["body"].rstrip() + "\n\n" + text)[:20000]
                    return update_note(scope, n["id"], {"body": body})
    else:
        title = "Summary — " + (s.get("title") or "chat")[:40]
    return create_note(scope, title, text, source=source)


def enhance_role_prompt(text: str, name: str = "", model: str = ""):
    """Expand a rough @role description ("you are artist you create image")
    into a well-structured system prompt (cheap OpenRouter call, user picks the
    model in the GUI). Returns (prompt_text, error)."""
    text = str(text or "").strip()
    if not text:
        return None, "write a rough description first"
    okey = os.environ.get("OPENROUTER_API_KEY")
    if not okey:
        return None, "an OpenRouter key is needed to enhance"
    model = str(model or "").strip() or "deepseek/deepseek-v4-flash"
    prompt = (
        "You write system prompts for AI role agents. Expand the rough description "
        "below into a clear, effective system prompt for that role.\n"
        "Structure it as short markdown sections: who the agent is and its core "
        "duties; how it works (ownership — come back with solutions, not excuses; "
        "anticipate what's needed; be honest about uncertainty and mistakes); how "
        "it talks (direct, concise, no filler or sycophancy); and 2-4 things to "
        "avoid. Keep everything specific to THIS role — no generic assistant fluff.\n"
        "Write in second person ('You are …'), under 200 words, plain markdown. "
        "Output ONLY the system prompt itself — no preamble, no title, no fences.\n\n"
        + (f"Agent name: {name}\n" if str(name or "").strip() else "")
        + f"Rough description: {text}\n\nSystem prompt:")
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500, "temperature": 0.5}
    try:
        req = urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {okey}",
                     "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
            method="POST")
        with urlreq.urlopen(req, timeout=45) as r:
            resp = json.loads(r.read().decode("utf-8"))
        out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception as e:
        return None, f"enhance failed: {e}"
    out = out.strip()
    if not out:
        return None, "the model returned nothing"
    return out, ""


def delete_database(name: str):
    """Delete a store file under projects/database to reclaim disk. Returns
    (ok, error). Only plain filenames in that folder are allowed."""
    safe = os.path.basename(str(name or "")).strip()
    if not safe:
        return False, "name is required"
    ddir = os.path.join(CFG_DIR, "projects", "database")
    target = os.path.join(ddir, safe)
    if os.path.dirname(os.path.abspath(target)) != os.path.abspath(ddir) or not os.path.isfile(target):
        return False, "database not found"
    try:
        os.remove(target)
        # SQLite side files, if any
        for ext in ("-wal", "-shm", "-journal"):
            side = target + ext
            if os.path.isfile(side):
                os.remove(side)
    except Exception as e:
        return False, f"cannot delete: {e}"
    return True, ""


# ── GUI tool confirmations ───────────────────────────────────────────────────
# The terminal asks y/n on the tty; the GUI can't. stream_chat yields a
# {"type":"confirm"} event instead and blocks until the frontend answers via
# POST /api/confirm (resolve_confirm below), or the wait times out → denied.

_PENDING_CONFIRMS = {}
_CONFIRM_TIMEOUT = 300  # seconds


def resolve_confirm(confirm_id: str, approve: bool) -> bool:
    p = _PENDING_CONFIRMS.get(confirm_id)
    if not p:
        return False
    p["approve"] = bool(approve)
    p["evt"].set()
    return True


def _new_confirm() -> tuple:
    cid = uuid.uuid4().hex[:10]
    _PENDING_CONFIRMS[cid] = {"evt": threading.Event(), "approve": False}
    return cid


def _wait_confirm(cid: str) -> bool:
    p = _PENDING_CONFIRMS[cid]
    p["evt"].wait(_CONFIRM_TIMEOUT)
    return _PENDING_CONFIRMS.pop(cid)["approve"]


def _diff_text(old: str, new: str, path: str) -> str:
    import difflib
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                      fromfile=path, tofile=path, lineterm=""))
    if len(lines) > 120:
        lines = lines[:120] + [f"… +{len(lines) - 120} more lines"]
    return "\n".join(lines)


_DESTRUCTIVE_CMD = (
    "rm ", "rm -", "rmdir", "unlink", " mv ", "mv ", " dd ", "dd ", "mkfs", "shred",
    "truncate", "chmod", "chown", "chgrp", " ln ", "sed -i", "perl -i", "awk -i",
    " kill ", "kill ", "pkill", "killall", "shutdown", "reboot", "halt", "sudo ",
    "git reset", "git checkout", "git clean", "git rm", "git push", "git commit",
    "git stash", "npm install", "npm i ", "npm ci", "npm publish", "npm uninstall",
    "pip install", "pip3 install", "pip uninstall", "yarn add", "yarn install",
    "brew install", "brew uninstall", "apt ", "apt-get", "pacman", "dnf ", "yum ",
    "systemctl", "launchctl", "crontab", "diskutil", "chflags", "xattr -w",
)


def _is_destructive_cmd(cmd: str) -> bool:
    """True when a shell command DELETES/MOVES/OVERWRITES files or touches the
    system — those still ask. Read-only analysis (ls, cat, grep, find, curl,
    wget, python read, git status/log/diff) runs free."""
    c = " " + str(cmd or "").lower() + " "
    if any(t in c for t in _DESTRUCTIVE_CMD):
        return True
    # an overwrite redirect ( > file ) — but check OUTSIDE quotes, so a '>' inside
    # a grep/sed pattern or a curl -w "%{…}" string isn't mistaken for one
    unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", str(cmd or ""))
    if re.search(r"(?<![0-9&|>])>>?(?!&)", unquoted):
        return True
    return False


def _tool_plan(name: str, args: dict, workspace: str) -> tuple:
    """(needs_confirm, action label, detail) for a builtin tool call — the GUI
    shows the label + detail on an Allow/Deny card. POLICY: reading, listing and
    read-only commands run WITHOUT asking (even outside the project) so the model
    can analyze freely; only actions that ALTER files (write_file, or a
    destructive shell command) require an Allow/Deny."""
    from agent_core import _safe_path, _outside_project, edit_confirm_on
    if name in ("read_file", "list_dir"):
        return False, "", ""  # reads/lists never ask — analysis is free
    if name == "run_command":
        cmd = args.get("command", "")
        return (_is_destructive_cmd(cmd), f"$ {cmd}", "")  # only destructive shell asks
    if name == "write_file":
        full = _safe_path(workspace, args.get("path", ""))
        content = args.get("content", "")
        outside = _outside_project(workspace, full)
        exists = os.path.exists(full)
        old = ""
        if exists:
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    old = f.read()
            except Exception:
                old = ""
        detail = (_diff_text(old, content, args.get("path", "")) if exists
                  else f"new file · {len(content.splitlines())} lines")
        verb = "Overwrite" if exists else "Create"
        where = f"{full} — outside the project" if outside else args.get("path", "")
        return (edit_confirm_on() or outside), f"{verb} {where}", detail
    return False, "", ""


def _exec_tool(name: str, args: dict, workspace: str) -> str:
    """Runs an (already approved) builtin tool. Same behavior as the terminal's
    _run_edit_tool, minus the tty prompts."""
    from agent_core import _safe_path
    if name == "read_file":
        full = _safe_path(workspace, args.get("path", ""))
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read(60000)
    if name == "list_dir":
        full = _safe_path(workspace, args.get("path", ""))
        entries = sorted(os.listdir(full))
        return "\n".join((e + "/" if os.path.isdir(os.path.join(full, e)) else e)
                         for e in entries) or "(empty)"
    if name == "write_file":
        full = _safe_path(workspace, args.get("path", ""))
        content = args.get("content", "")
        os.makedirs(os.path.dirname(full) or workspace, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {args.get('path')}"
    if name == "run_command":
        import subprocess
        shell = os.environ.get("SHELL") or "/bin/sh"
        try:
            res = subprocess.run([shell, "-lc", args.get("command", "")], cwd=workspace,
                                 capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return "[error] command timed out after 300 seconds"
        out = ((res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")).strip()[:10000]
        if res.returncode != 0:
            return f"(exit {res.returncode})\n{out}" if out else f"(exit {res.returncode}, no output)"
        return out or "(exit 0, no output)"
    return f"[error] unknown tool {name}"


DENIED = ("[denied] the user did not approve this action — continue without "
          "it or ask what to do instead")

NOTES_DISABLED = ("[blocked] note generation is DISABLED — the 'Let the AI keep notes' "
                  "toggle is OFF (false) for this conversation, so the app refused this "
                  "write. Do NOT retry it or write the file anywhere else. Instead, give "
                  "the note content directly in your reply, then tell the user: the AI "
                  "notes ability is currently disabled — make sure 'Let the AI keep "
                  "notes' is enabled (true) in the Notes panel to save notes, which can "
                  "then be exported as PDF, DOCX, XLSX or CSV.")


# ── Streaming chat ───────────────────────────────────────────────────────────

def _api_messages(msgs: list) -> list:
    """OpenAI-format messages; turns with attached images become multimodal
    content arrays (data: URLs pass straight through to OpenRouter)."""
    out = []
    for m in msgs:
        imgs = m.get("images")
        if imgs:
            content = ([{"type": "text", "text": m["content"]}] if m["content"] else [])
            content += [{"type": "image_url", "image_url": {"url": u}} for u in imgs]
            out.append({"role": m["role"], "content": content})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


# Extensions we decode and paste inline; everything else that isn't an image is
# noted by name only (binary office docs, archives, etc.)
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".sh",
    ".bash", ".zsh", ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".php", ".sql", ".html", ".css", ".scss", ".svg",
    ".xml", ".csv", ".tsv", ".dart", ".vue", ".lua", ".r", ".m", ".pl",
}


def _expand_attachments(text: str, attachments: list) -> tuple:
    """Turn attachments into (content, images). Images stay as data URLs for
    multimodal models; text/code files are decoded and appended to the message
    as fenced blocks; unknown binaries are noted by filename."""
    import base64
    images = []
    blocks = []
    for att in attachments or []:
        name = str(att.get("name") or "file")
        url = str(att.get("url") or "")
        mime = str(att.get("type") or "")
        ext = os.path.splitext(name)[1].lower()
        if mime.startswith("image/") or ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            images.append(url)
            continue
        if ext in _TEXT_EXT or mime.startswith("text/"):
            _, _, b64 = url.partition(",")
            try:
                body = base64.b64decode(b64).decode("utf-8", errors="replace")[:40000]
                lang = ext.lstrip(".")
                blocks.append(f"### Attached file: {name}\n```{lang}\n{body}\n```")
                continue
            except Exception:
                pass
        size = 0
        _, _, b64 = url.partition(",")
        try:
            size = len(base64.b64decode(b64))
        except Exception:
            pass
        blocks.append(f"### Attached file: {name} ({size} bytes) — binary, not shown inline.")
    content = text
    if blocks:
        content = (text + "\n\n" if text else "") + "\n\n".join(blocks)
    return content, images


_MENTION_RE = re.compile(r"(?:^|\s)@([a-z0-9_-]+)", re.I)


def _handoff_context(user_text: str, current_agent_id: str) -> str:
    """When the message mentions another @role, fetch that teammate's most
    recent exchange and return it as a context preamble — so handoffs like
    'check @debug's warnings' work inline, no clicking. Mentions of the
    current agent or unknown names are ignored."""
    ids = {a["id"] for a in list_agents()}
    seen, blocks = set(), []
    for m in _MENTION_RE.findall(user_text or ""):
        rid = m.lower()
        if rid in seen or rid == current_agent_id or rid not in ids:
            continue
        seen.add(rid)
        exch = _last_exchange(rid)
        if not exch:
            continue
        src, q, a = exch
        blocks.append(
            f"### Context from teammate {src['name']} (@{rid}):\n"
            f"They were asked:\n{(q or '')[:1200]}\n\n"
            f"Their answer:\n{(a or '')[:6000]}"
        )
    if not blocks:
        return ""
    return ("The user mentioned a teammate with @name. That teammate's most "
            "recent work is included below as context — treat it as already "
            "known information and answer from it directly. Do NOT use file or "
            "shell tools to search the disk for the teammate's name; @name "
            "refers to a teammate agent, not a file or folder.\n\n"
            + "\n\n".join(blocks) + "\n\n---\n\n")


def _last_exchange(agent_id: str):
    """(agent, question, answer) from the agent's most recent completed turn.
    Mirrors agent_roles._last_exchange without the terminal dependency."""
    for meta in list_sessions(agent_id):
        s = get_session(agent_id, meta["id"])
        if not s:
            continue
        msgs = s["messages"]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "assistant":
                q = msgs[i - 1]["content"] if i > 0 and msgs[i - 1]["role"] == "user" else ""
                return get_agent(agent_id), q, msgs[i]["content"]
    return None


def _make_title(user_text: str, answer: str) -> str:
    """A short topic summary for the sidebar — a cheap, no-API heuristic:
    the first sentence/clause of the user's ask, trimmed and title-cleaned."""
    text = " ".join((user_text or "").split())
    text = _MENTION_RE.sub(" ", text).strip()  # drop @mentions from the title
    for stop in (". ", "? ", "! ", "\n"):
        if stop in text:
            text = text.split(stop)[0]
            break
    return (text[:60].rstrip(" ,;:-") or "New chat")


def _smart_title(user_text: str, answer: str) -> str:
    """A 3–5 word TOPIC summary of the exchange (not the raw request). Uses one
    tiny, cheap deepseek-flash call (~a hundredth of a cent); falls back to the
    heuristic when OpenRouter isn't configured or the call fails."""
    okey = os.environ.get("OPENROUTER_API_KEY")
    if not okey or not (user_text or answer):
        return _make_title(user_text, answer)
    prompt = ("Give a 3-5 word topic title for this conversation. "
              "Summarize what it is ABOUT — do not echo the user's wording. "
              "No quotes, no trailing punctuation.\n\n"
              f"User: {(user_text or '')[:600]}\n"
              f"Assistant: {(answer or '')[:600]}\n\nTitle:")
    body = {"model": "deepseek/deepseek-v4-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 20, "temperature": 0.2}
    try:
        req = urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {okey}",
                     "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
            method="POST")
        with urlreq.urlopen(req, timeout=12) as r:
            resp = json.loads(r.read().decode("utf-8"))
        t = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        t = " ".join(t.strip().strip('"').strip("'").rstrip(".").split())[:60]
        return t or _make_title(user_text, answer)
    except Exception:
        return _make_title(user_text, answer)


def _is_image_model(model: str) -> bool:
    """True for OpenRouter models that OUTPUT images (so we ask for the image
    modality and display the result, instead of running the tool-calling loop
    that makes a text model write code to fake it)."""
    m = (model or "").lower()
    return any(k in m for k in (
        "image", "dall-e", "dalle", "flux", "imagen", "stable-diffusion", "sdxl", "grok-2-image"))


def _is_offline_error(e) -> bool:
    """True when an exception means 'no internet connection' rather than a real
    server response (an HTTPError reached the server, so it is NOT offline)."""
    if isinstance(e, urlerr.HTTPError):
        return False
    if isinstance(e, urlerr.URLError):
        return True
    import socket
    return isinstance(e, (socket.error, ConnectionError, TimeoutError, OSError))


def _backends(model: str, prefer: str = "") -> list:
    """(url, headers, model, timeout) candidates, primary first. prefer='local'
    forces the on-device model only (no cloud); prefer='api:<id>' routes to
    that user-connected OpenAI-compatible integration first."""
    out = []
    if prefer.startswith("api:"):
        it = _integration(prefer[4:])
        if it:
            out.append((it["base_url"].rstrip("/") + "/chat/completions",
                        _integration_headers(it), model, 180))
    okey = os.environ.get("OPENROUTER_API_KEY")
    if okey and prefer != "local" and not prefer.startswith("api:"):
        out.append((
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {okey}",
             "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
            model,
            180,
        ))
    local_model = model if prefer == "local" and model else _detect_local_model()
    out.append(("http://localhost:8080/v1/chat/completions", {}, local_model, 180))
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


def _tty() -> bool:
    """True when the server was launched from a terminal that can answer the
    y/n confirm hook. Under Electron there is no tty, so hook-gated tools are
    trimmed instead of silently blocking."""
    try:
        return os.isatty(0)
    except Exception:
        return False


def _stream_claude_cli(messages: list, model: str, workspace: str = ""):
    """Token generator over the Claude Code CLI (claude.ai subscription login)."""
    import shutil
    import subprocess
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not installed")
    system_prompt, convo = _split_system(messages)
    edit = edit_mode_on()
    workspace = workspace or os.environ.get("AI_WORKSPACE_PATH", os.getcwd())
    cmd = [
        claude_bin, "-p",
        "--model", model or "sonnet",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        # No personal MCP connectors — they bloat every prompt with tool schemas
        "--strict-mcp-config",
    ]
    if edit:
        tools = "Read,Glob,Grep,Edit,Write,MultiEdit,NotebookEdit,Bash,TodoWrite"
        if edit_confirm_on():
            if _tty():
                # PreToolUse hook asks y/n on the terminal before Edit/Write/Bash
                cmd += ["--settings", claude_confirm_settings()]
            else:
                # No terminal to confirm on (GUI): edits stay sandboxed to the
                # project via acceptEdits; shell is dropped rather than let it
                # run unreviewed
                tools = "Read,Glob,Grep,Edit,Write,MultiEdit,NotebookEdit,TodoWrite"
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools", tools]
        system_prompt = (system_prompt or "") + EDIT_SYSTEM_ADD.format(ws=workspace)
    else:
        # Reads allowed everywhere, shell behind the y/n hook, writes blocked
        tools = "Read,Glob,Grep,Bash" if _tty() else "Read,Glob,Grep"
        cmd += ["--allowedTools", tools,
                "--disallowed-tools", "Edit,Write,MultiEdit,NotebookEdit,Task,WebFetch,WebSearch,TodoWrite"]
        if _tty():
            cmd += ["--settings", claude_confirm_settings()]
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


def _stream_codex_cli(messages: list, model: str, workspace: str = ""):
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


def stream_chat(session: dict, user_text: str, images: list = None,
               attachments: list = None, as_role: str = "", save_user: bool = True):
    """Yields event dicts: {"type":"token","text":..}, {"type":"confirm",..}
    (GUI must answer via resolve_confirm), then {"type":"done",...}
    (or {"type":"error","message":..} if every backend failed).
    The session file is updated with both messages on success.

    `images` is the legacy list of image data URLs; `attachments` is the newer
    [{name,type,url}] list of any-kind files. Both are supported.

    Group chat: `as_role` makes a team @role answer INSIDE this session
    (persona + engine come from the role, the session's agent/folder is
    untouched, the reply is tagged with the role id); `save_user=False` keeps
    a synthetic follow-up cue out of the stored history."""
    agent = get_agent(as_role or session["agent"])
    # Fold any attachments into the message: images go multimodal, text/code
    # files get pasted inline, binaries are noted by name
    user_content, att_images = _expand_attachments(user_text, attachments)
    images = list(images or []) + att_images
    workspace = session.get("project") or os.environ.get("AI_WORKSPACE_PATH") or os.getcwd()

    base_system = agent["system"]
    # The default chat's instructions are user-editable in Settings → General
    if session["agent"] == DEFAULT_AGENT_ID and not as_role:
        try:
            custom = (gui_settings().get("default_system") or "").strip()
            if custom:
                base_system = custom
        except Exception:
            pass
    system_prompt = base_system + STYLE_SYSTEM_ADD
    # Optional "skills": ["caveman", ...] — skill file bodies join the prompt
    for sk in agent.get("skills") or []:
        path = find_skill_file(os.path.join(CFG_DIR, "skills"), sk)
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    system_prompt += "\n\n" + f.read().strip()
            except Exception:
                pass
    # Project instructions (Claude-style "custom instructions") join the prompt
    if session.get("project"):
        meta = _load_project_meta(session["project"])
        if meta.get("instructions"):
            system_prompt += ("\n\n### Project instructions:\n"
                              + meta["instructions"].strip())

    # Group chat: the role speaks as itself among named teammates
    if as_role and session.get("group"):
        _g = get_group(session["group"]) or {}
        _others = [p for p in _g.get("participants", []) if p != as_role]
        system_prompt += (
            f"\n\n### Group chat: you are @{as_role} ({agent.get('name', as_role)}) in the group "
            f"'{_g.get('name', 'group')}' together with the user"
            + (" and your teammates " + ", ".join("@" + o for o in _others) if _others else "")
            + f". Speak ONLY as yourself (@{as_role}) and cover the part of the request that matches "
              "your role — don't answer for teammates or repeat what they already said. Teammate "
              "messages in the history are tagged like [@id]. A message that mentions "
              f"@{as_role} is addressed to you.")

    # Saved notes for this scope are READ-ONLY memory the model can use (e.g. an
    # @role recalling a TL;DR across chats — saves re-reading the whole history).
    # notes_scope/notes_on also drive the HARD write_file guard in the tool loop
    # below — the prompt alone is not enough, weaker models ignore it.
    notes_scope, notes_on = "", False
    try:
        notes_scope, _ = _scope_of(session["agent"], session["id"])
        _ctx = _load_ctx(notes_scope)
        notes_on = bool(_ctx.get("ai_auto"))
        _notes = _ctx.get("notes", [])
        if _notes:
            system_prompt += "\n\n### Saved notes (read-only memory — use them, never claim to have edited them):\n"
            for n in _notes[:12]:
                system_prompt += f"- {n.get('title', 'Note')}: {n.get('body', '')[:1200]}\n"
        # Notes policy — the app has a dedicated Notes feature; the model must NOT
        # fake it by writing .md files.
        if notes_on:
            system_prompt += ("\n\n### Notes: this app has a built-in Notes panel and 'Let the AI keep notes' is ON, "
                              "so a note is saved automatically after your reply. NEVER use write_file or create a .md "
                              "file to save notes — just answer normally with the content. Notes can be exported from "
                              "the panel as PDF, DOCX, XLSX or CSV, so never generate such files for notes either.")
        else:
            system_prompt += ("\n\n### Notes: this app has a built-in Notes panel but 'Let the AI keep notes' is OFF. "
                              "You cannot save notes and must NEVER use write_file or create a .md file to do it — "
                              "any note-like write_file WILL be blocked by the app. If the user asks you to make/save "
                              "notes, give them the content directly in your reply, then add one short line telling "
                              "them to enable 'Let the AI keep notes' in the Notes panel if they want notes saved "
                              "(saved notes can then be exported as PDF, DOCX, XLSX or CSV).")
    except Exception:
        pass

    # @role handoff: if the message names another teammate, pull their latest
    # exchange in as context so 'review @debug's warnings' just works.
    # Group sessions skip this — the mentioned teammates answer IN the group.
    preamble = "" if session.get("group") else _handoff_context(user_content, session["agent"])
    send_content = preamble + user_content

    messages = [{"role": "system", "content": system_prompt}]
    for m in session["messages"]:
        # handoff dividers are UI-only markers — never send them to the model
        if m["role"] not in ("user", "assistant"):
            continue
        d = {"role": m["role"], "content": m["content"]}
        # group history: a teammate's reply is tagged so roles can tell each
        # other (and themselves) apart
        if m["role"] == "assistant" and m.get("agent") and m["agent"] != (as_role or ""):
            d["content"] = f"[@{m['agent']}]\n{m['content']}"
        # only USER images are context; assistant images are model-generated
        # output (display only) and must not be echoed back into the request
        if m["role"] == "user" and m.get("images"):
            d["images"] = m["images"]
        messages.append(d)
    current = {"role": "user", "content": send_content}
    if images:
        current["images"] = images
    messages.append(current)

    acc = []
    gen_images = []  # images generated by an image-output model (display only)
    usage = {}
    errs = []
    offline_hit = False  # set when a cloud call fails for lack of a connection

    # Engine resolution. A team @role's engine is CANONICAL: every session under
    # that role uses the role's backend/model/effort (so they stay consistent and
    # a stale per-session override can't leave one on a wrong/old model). The
    # built-in default agent keeps its per-session /agent-picker override.
    _role_id = as_role or session["agent"]
    is_role = _role_id != DEFAULT_AGENT_ID and any(
        a["id"] == _role_id for a in list_agents())
    src = agent if is_role else session
    if is_role:
        backend = str(agent.get("backend", "openrouter")).strip().lower() or "openrouter"
        role_model = agent.get("model", "")
        effort = str(agent.get("effort", "") or "").strip().lower()
    else:
        backend = (session.get("backend") or "").strip().lower() or agent.get("backend", "openrouter")
        role_model = ""
        effort = (session.get("effort") or "").strip().lower()
    if effort not in VALID_EFFORT:
        effort = ""
    # Sampling: temperature preset (precise/balanced/creative → numeric) + max
    # tokens, from the role config or the per-session picker.
    # unset/"" falls back to the balanced preset — the model never runs on an
    # unknown provider default
    temperature = TEMP_PRESETS.get(str(src.get("temperature", "") or "balanced").strip().lower())
    try:
        max_tokens = int(src.get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0

    def _sampling(body):
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens > 0:
            body["max_tokens"] = max_tokens
        return body
    if backend in ("claude", "codex"):
        # The CLI backends are text-only; attached images are not forwarded
        if backend == "codex" and effort:
            os.environ["CODEX_EFFORT"] = effort
        cli = _stream_claude_cli if backend == "claude" else _stream_codex_cli
        try:
            for text in cli(messages, role_model or session.get("model") or agent.get("model", ""), workspace):
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
    model = role_model or session.get("model") or agent["model"]

    # Image generation: ask OpenRouter for the image modality and hand the
    # picture straight back — NO tool loop (a text model would otherwise try to
    # shell out and write code to fake an image, as the user saw).
    if not acc and backend == "openrouter" and okey and _is_image_model(model):
        # Image models get the current prompt + ONE reference image to edit —
        # NEVER the full chat history (that made them re-run every past request:
        # "make banana" then "make woman" → 2 bananas + 2 women). The reference
        # is what the user attached this turn, else the most recent picture in the
        # thread, so "make it more anime" edits the previous image instead of
        # generating something unrelated.
        parts = [{"type": "text", "text": user_content}]
        ref_added = False
        for u in (images or []):
            parts.append({"type": "image_url", "image_url": {"url": u}})
            ref_added = True
        if not ref_added:
            for m in reversed(session["messages"]):
                if m.get("images"):
                    parts.append({"type": "image_url", "image_url": {"url": m["images"][-1]}})
                    break
        img_convo = [{"role": "user", "content": parts}]
        if agent.get("system"):
            img_convo.insert(0, {"role": "system", "content": agent["system"]})
        body = {"model": model, "messages": img_convo,
                "modalities": ["image", "text"], "usage": {"include": True}}
        req = urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {okey}",
                     "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
            method="POST")
        try:
            with urlreq.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read().decode("utf-8"))
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            text = msg.get("content") or ""
            for im in (msg.get("images") or []):
                url = (im.get("image_url") or {}).get("url") if isinstance(im, dict) else None
                if url:
                    gen_images.append(url)
                    break  # exactly ONE image per response — some models (e.g.
                    #        gemini-3-pro-image) return 2+ near-identical candidates
            u = resp.get("usage") or {}
            usage = {"prompt_tokens": u.get("prompt_tokens", 0),
                     "completion_tokens": u.get("completion_tokens", 0),
                     "cost": u.get("cost", 0) or 0}
            if text:
                acc.append(text)
                yield {"type": "token", "text": text}
            for url in gen_images:
                yield {"type": "image", "url": url}
            if not gen_images and not text:
                errs.append("image model returned no image")
        except urlerr.HTTPError as e:
            try:
                detail = e.read(300).decode("utf-8", errors="ignore")
            except Exception:
                detail = ""
            errs.append(f"HTTP {e.code} from openrouter.ai: {detail}")
        except Exception as e:
            errs.append(f"openrouter.ai: {e}")
            if _is_offline_error(e):
                offline_hit = True

    elif not acc and backend == "openrouter" and okey:
        from agent_core import _EDIT_TOOLS
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
        convo = _api_messages(messages)
        from agent_core import TOOLS_SYSTEM_ADD
        note = TOOLS_SYSTEM_ADD.format(names=", ".join(sorted(builtin)), ws=workspace)
        if convo and convo[0]["role"] == "system":
            convo[0] = {"role": "system", "content": convo[0]["content"] + note}
        else:
            convo.insert(0, {"role": "system", "content": note.strip()})
        model = role_model or session.get("model") or agent["model"]
        for _round in range(6):
            body = {"model": model, "messages": convo, "tools": tools,
                    "usage": {"include": True}}
            if effort:
                body["reasoning"] = {"effort": effort}
            _sampling(body)
            req = urlreq.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {okey}",
                         "HTTP-Referer": "https://github.com/wibawasuyadnya/orkesai"},
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
                if _is_offline_error(e):
                    offline_hit = True
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
                    # HARD notes guard — the system prompt alone doesn't stop
                    # weaker models from faking notes as loose .md files. Notes
                    # OFF: the write is refused with feedback the model must
                    # relay. Notes ON: the content is captured as a real note
                    # in the panel instead of a stray file.
                    if fname == "write_file" and _is_note_file(args.get("path", "")):
                        if not notes_on:
                            yield {"type": "token",
                                   "text": "∗ blocked — 'Let the AI keep notes' is OFF\n"}
                            result = NOTES_DISABLED
                        else:
                            stem = os.path.splitext(os.path.basename(str(args.get("path") or "note")))[0]
                            title = re.sub(r"[-_]+", " ", stem).strip().title() or "Note"
                            note, nerr = create_note(notes_scope, title,
                                                     args.get("content", ""), source="ai")
                            result = (f"[redirected] saved as the note '{title}' in the app's "
                                      "Notes panel instead of a file — that is where AI notes "
                                      "live. Tell the user it is in the Notes panel and can be "
                                      "exported as PDF, DOCX, XLSX or CSV from there."
                                      if not nerr else f"[tool error] {nerr}")
                            yield {"type": "token",
                                   "text": f"∗ saved to the Notes panel — “{title}”\n"}
                        convo.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                      "content": result})
                        continue
                    try:
                        needs, action, detail = _tool_plan(fname, args, workspace)
                        approved = True
                        if needs:
                            cid = _new_confirm()
                            yield {"type": "confirm", "id": cid, "tool": fname,
                                   "action": action, "detail": detail}
                            approved = _wait_confirm(cid)
                        result = _exec_tool(fname, args, workspace) if approved else DENIED
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

    offline_divider = None  # inserted between the user msg and the local answer
    for url, headers, model, timeout in ([] if (acc or gen_images) else _backends(role_model or session.get("model") or agent["model"], backend)):
        is_local = url.startswith("http://localhost")
        # Don't silently retry the cloud once we know we're offline
        if not is_local and offline_hit:
            continue
        if is_local and backend != "local" and offline_hit and not acc:
            # We fell back to local because the cloud is unreachable — ask the
            # user first instead of quietly switching models.
            cid = _new_confirm()
            yield {"type": "offline", "id": cid}
            go_local = _wait_confirm(cid)  # True = use local · False = wait
            if not go_local:
                # Stop: shut the local server back down and pause the thread with
                # a divider so the action is visible, then bail without an answer
                try:
                    from agent_core import shutdown_local_server
                    shutdown_local_server()
                except Exception:
                    pass
                now = int(time.time())
                pmsg = {"role": "user", "content": user_content, "text": user_text, "ts": now}
                if images:
                    pmsg["images"] = images
                session["messages"].append(pmsg)
                session["messages"].append({
                    "role": "divider", "to": "paused", "reason": "offline", "ts": now})
                session["updated"] = now
                _save_session(session)
                yield {"type": "reload"}
                return
            local_name = os.environ.get("LOCAL_MODEL", "").strip() or model
            offline_divider = {"role": "divider", "to": "local", "reason": "offline",
                               "backend": "local", "model": local_name,
                               "ts": int(time.time())}
        if is_local:
            # Local fallback (cloud down / offline): boot llama-server on demand
            try:
                from agent_core import ensure_local_server
                if not ensure_local_server():
                    errs.append("local llama-server unavailable")
                    continue
            except Exception:
                pass
        body = {"model": model, "messages": _api_messages(messages), "stream": True,
                "usage": {"include": True}}
        if effort and not url.startswith("http://localhost"):
            body["reasoning"] = {"effort": effort}
        _sampling(body)
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
            if not is_local and _is_offline_error(e):
                offline_hit = True

    if not acc and not gen_images:
        # All backends failed — show every error, primary first, so a dead
        # localhost fallback can't mask the real (e.g. OpenRouter 400) cause
        yield {"type": "error", "message": "; ".join(errs) or "all backends failed"}
        return

    answer = "".join(acc)
    now = int(time.time())
    # content keeps the file text so later turns retain it; text is the clean
    # thing the user typed (for the bubble); attachments/images redisplay chips
    user_msg = {"role": "user", "content": user_content, "text": user_text, "ts": now}
    if images:
        user_msg["images"] = images
    file_chips = [{"name": a.get("name", "file"), "type": a.get("type", "")}
                  for a in (attachments or [])
                  if not str(a.get("type", "")).startswith("image/")]
    if file_chips:
        user_msg["attachments"] = file_chips
    if save_user:
        session["messages"].append(user_msg)
    if offline_divider:
        # mark where we switched to the local model after going offline
        session["messages"].append(offline_divider)
    assistant_msg = {"role": "assistant", "content": answer, "ts": now}
    if as_role:
        assistant_msg["agent"] = as_role  # which @role said this (group chat)
    if gen_images:
        assistant_msg["images"] = gen_images  # display-only; not re-sent to the model
    session["messages"].append(assistant_msg)
    session["updated"] = now
    session["usage"]["in"] += usage.get("prompt_tokens", 0)
    session["usage"]["out"] += usage.get("completion_tokens", 0)
    if session["title"] in ("New session", "New chat") and (user_text or answer):
        session["title"] = _smart_title(user_text, answer)
    _save_session(session)
    # Global spend ledger (same file the main chat and /usage read)
    try:
        import agent_usage
        agent_usage.record(role_model or session.get("model") or agent.get("model") or backend,
                           usage.get("prompt_tokens", 0),
                           usage.get("completion_tokens", 0),
                           usage.get("cost", 0) or 0.0)
    except Exception:
        pass
    # AI note-taking: only when the user turned it ON for this scope. One note
    # PER TOPIC — new topics get their own note, follow-ups update the matching
    # one (upsert inside summarize_to_note; never touches the user's manual notes).
    try:
        _sc, _ = _scope_of(session["agent"], session["id"])
        if _load_ctx(_sc).get("ai_auto"):
            summarize_to_note(session["agent"], session["id"], source="ai-auto", mode="content")
    except Exception:
        pass
    yield {"type": "done", "usage": session["usage"], "title": session["title"],
           "cost": usage.get("cost", 0)}


_MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")


def stream_group_chat(session: dict, user_text: str, images: list = None,
                      attachments: list = None):
    """Group turn: mentioned participants reply (in mention order); with no
    mention, EVERY participant replies in turn, each covering its own part.
    Yields the same events as stream_chat plus {"type":"role", id, name, icon}
    before each role's reply so the GUI can start a new tagged bubble."""
    g = get_group(session.get("group") or "")
    if not g:
        yield from stream_chat(session, user_text, images, attachments)
        return
    participants = _clean_participants(g.get("participants"))
    if not participants:
        yield {"type": "error",
               "message": "this group has no @role participants left — edit the group and add some"}
        return
    mentioned = [m for m in _MENTION_RE.findall(user_text or "") if m in participants]
    responders = list(dict.fromkeys(mentioned)) or participants
    done_ev = None
    for i, rid in enumerate(responders):
        a = get_agent(rid)
        yield {"type": "role", "id": rid, "name": a.get("name", rid),
               "icon": a.get("icon", "🤖")}
        if i == 0:
            gen = stream_chat(session, user_text, images, attachments, as_role=rid)
        else:
            # later responders see the user message + teammates' replies in the
            # history; this cue is never saved (save_user=False)
            cue = (f"(group turn) Now reply as @{rid} to the user's last message "
                   "above, covering only your part.")
            gen = stream_chat(session, cue, as_role=rid, save_user=False)
        errored = False
        for ev in gen:
            if ev.get("type") == "done":
                done_ev = ev  # only the final aggregate is forwarded
            else:
                if ev.get("type") == "error":
                    errored = True
                yield ev
        if errored:
            break  # a dead backend would just repeat for every next role
    if done_ev:
        yield done_ev


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
