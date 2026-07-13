# File: ~/.config/orkesai/modules/agent_settings.py
"""Persistent user settings for OrkesAI, shared by the chat loop and modules.

Settings live in ~/.config/orkesai/settings.json and survive restarts —
the /settings command reads and writes them. Precedence for the startup
backend: a real shell env var (AI_BACKEND=local ai) always wins, then
settings.json, then .env, then the auto cascade.

Keys:
  spellcheck  bool  spellchecker on startup (/d and /e persist here too)
  agent       str   backend to start on: claude|codex|openrouter|gemini|local|auto
  edit        str   on (default: tools everywhere, y/n per action) | auto | off
"""
import json
import os
import threading

CFG_DIR = os.path.expanduser("~/.config/orkesai")
SETTINGS_FILE = os.path.join(CFG_DIR, "settings.json")
_lock = threading.Lock()

VALID_AGENTS = ("claude", "codex", "openrouter", "gemini", "local", "auto")
VALID_EDIT = ("on", "auto", "off")


def load() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> None:
    with _lock:
        data = load()
        data[key] = value
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)


def apply_startup(real_env_backend: bool, real_env_edit: bool = False) -> None:
    """Applies persisted startup state. Precedence: real shell env vars
    (AI_BACKEND=x ai) win, then settings.json, then .env."""
    data = load()
    agent = str(data.get("agent", "") or "").strip().lower()
    if not real_env_backend and agent in VALID_AGENTS:
        if agent == "auto":
            os.environ.pop("AI_BACKEND", None)
        else:
            os.environ["AI_BACKEND"] = agent
    # Edit mode defaults ON: every agent (any backend, team included) gets
    # file/shell tools, each action gated by the user's y/n. "auto" skips the
    # prompts, "off" is read-only chat.
    if not real_env_edit:
        edit = str(data.get("edit", "on") or "on").strip().lower()
        if edit not in VALID_EDIT:
            edit = "on"
        if edit == "off":
            os.environ.pop("AI_EDIT_MODE", None)
            os.environ.pop("AI_EDIT_CONFIRM", None)
        else:
            os.environ["AI_EDIT_MODE"] = "1"
            os.environ["AI_EDIT_CONFIRM"] = "0" if edit == "auto" else "1"
