# File: ~/.config/local-ai/modules/agent_usage.py
"""Persistent per-model token/spend ledger shared by the main chat loop,
the team agent service, and the /usage command.

The ledger lives in projects/database/usage.json with two views of the same
turns: all-time totals per model and per-day totals per model. Backends that
report real usage (OpenRouter, llama-server, Claude CLI) record exact counts
and cost; backends without usage reporting record ~4 chars/token estimates
at zero cost.
"""
import json
import os
import threading
import time
import urllib.request as urlreq

CFG_DIR = os.path.expanduser("~/.config/local-ai")
USAGE_FILE = os.path.join(CFG_DIR, "projects", "database", "usage.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"models": {}, "daily": {}}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(USAGE_FILE), exist_ok=True)
    tmp = USAGE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, USAGE_FILE)


def record(model: str, in_tok: int, out_tok: int, cost: float = 0.0) -> None:
    """Adds one completed turn to both the all-time and per-day ledgers."""
    model = (model or "unknown").strip()
    day = time.strftime("%Y-%m-%d")
    with _lock:
        data = _load()
        buckets = (
            data.setdefault("models", {}),
            data.setdefault("daily", {}).setdefault(day, {}),
        )
        for bucket in buckets:
            m = bucket.setdefault(model, {"req": 0, "in": 0, "out": 0, "cost": 0.0})
            m["req"] += 1
            m["in"] += int(in_tok or 0)
            m["out"] += int(out_tok or 0)
            m["cost"] += float(cost or 0.0)
        _save(data)


def today_cost() -> float:
    day = time.strftime("%Y-%m-%d")
    return sum(m.get("cost", 0.0) for m in _load().get("daily", {}).get(day, {}).values())


def _fmt_tok(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_cost(c: float) -> str:
    if not c:
        return "free"
    return f"${c:.4f}" if c < 1 else f"${c:.2f}"


def turn_line(in_tok: int, out_tok: int, cost: float, ctx_used: int = None, ctx_max: int = None) -> str:
    """Dim one-liner printed under the speed stats after each answer:
    tokens in/out, this turn's cost, today's running spend, context left.
    Full words, no abbreviations."""
    parts = [
        f"{_fmt_tok(in_tok)} tokens sent · {_fmt_tok(out_tok)} received",
        f"turn cost {_fmt_cost(cost)}",
        f"today {_fmt_cost(today_cost())}",
    ]
    if ctx_used is not None and ctx_max:
        parts.append(f"context {_fmt_tok(ctx_used)} of {_fmt_tok(ctx_max)} used · {_fmt_tok(max(0, ctx_max - ctx_used))} left")
    return "\033[2m▪ " + " · ".join(parts) + "\033[0m"


def backend_of_model(model: str) -> str:
    """Maps a recorded model name back to its backend bucket."""
    m = (model or "").lower()
    if m.startswith("claude"):
        return "claude"
    if m.startswith("codex"):
        return "codex"
    if m == "local-model" or "gguf" in m or "localhost" in m:
        return "local"
    if m.startswith("gemini"):
        return "gemini"
    return "openrouter"


def today_by_backend() -> dict:
    """Today's totals folded into claude / codex / openrouter / gemini / local."""
    day = time.strftime("%Y-%m-%d")
    out = {}
    for model, m in _load().get("daily", {}).get(day, {}).items():
        agg = out.setdefault(backend_of_model(model), {"req": 0, "in": 0, "out": 0, "cost": 0.0})
        for k in ("req", "in", "out"):
            agg[k] += m.get(k, 0)
        agg["cost"] += m.get("cost", 0.0)
    return out


def active_backend() -> str:
    """The backend the main chat currently talks to (mirrors the cascade)."""
    b = os.environ.get("AI_BACKEND", "").strip().lower()
    if b in ("claude", "codex", "openrouter", "local", "gemini"):
        return b
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "local"


# OpenRouter balance is fetched off-thread and cached so the statusline and
# header never block on the network
_balance = {"ts": 0.0, "left": None, "total": None}


def refresh_balance_async(min_age: float = 60.0) -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        return
    if time.time() - _balance["ts"] < min_age:
        return
    _balance["ts"] = time.time()

    def _job():
        data = fetch_openrouter_credits()
        if data:
            total = data.get("total_credits") or 0.0
            used = data.get("total_usage") or 0.0
            _balance.update(left=max(0.0, total - used), total=total)

    threading.Thread(target=_job, daemon=True).start()


def balance_left() -> float or None:
    return _balance["left"]


def _backend_spend_bit(backend: str, m: dict) -> str:
    """'openrouter $0.0213' for paid turns, 'local 1.2k tokens' for free ones."""
    if m.get("cost"):
        return f"{backend} {_fmt_cost(m['cost'])}"
    return f"{backend} {_fmt_tok(m.get('out', 0))} tokens"


def statusline_spend() -> str:
    """Compact today-spend per backend for the bottom statusline, plus the
    remaining OpenRouter balance once the async fetch has landed."""
    per = today_by_backend()
    bits = [_backend_spend_bit(b, per[b])
            for b in ("openrouter", "claude", "codex", "gemini", "local") if b in per]
    left = balance_left()
    if left is not None:
        bits.append(f"balance ${left:.2f}")
    return " · ".join(bits)


def header_spend() -> str or None:
    """Today's spend for the active backend, shown in the session header."""
    b = active_backend()
    m = today_by_backend().get(b)
    if not m:
        return None
    spend = f"today {_fmt_cost(m['cost'])}" if m.get("cost") else f"today {_fmt_tok(m.get('out', 0))} tokens"
    left = balance_left()
    if b == "openrouter" and left is not None:
        spend += f" · ${left:.2f} left"
    return spend


def fetch_openrouter_credits() -> dict or None:
    """Live account balance from OpenRouter: {total_credits, total_usage}."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urlreq.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urlreq.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8")).get("data")
    except Exception:
        return None


def print_summary() -> None:
    """The /usage command: per-model totals, today's spend, remaining credits."""
    data = _load()
    models = data.get("models", {})
    if not models:
        print("\033[2m[usage] no recorded turns yet — talk to the agent first\033[0m\n")
        return
    print("\033[1musage\033[0m \033[2m(all time)\033[0m")
    print(f"  \033[2m{'model':<38}{'req':>5}{'in':>9}{'out':>9}{'cost':>11}\033[0m")
    for name, m in sorted(models.items(), key=lambda kv: (-kv[1].get("cost", 0.0), -kv[1].get("out", 0))):
        print(
            f"  \033[1m{name[:37]:<38}\033[0m{m.get('req', 0):>5}"
            f"{_fmt_tok(m.get('in', 0)):>9}{_fmt_tok(m.get('out', 0)):>9}"
            f"{_fmt_cost(m.get('cost', 0.0)):>11}"
        )
    day = time.strftime("%Y-%m-%d")
    today = data.get("daily", {}).get(day, {})
    t_req = sum(m.get("req", 0) for m in today.values())
    t_in = sum(m.get("in", 0) for m in today.values())
    t_out = sum(m.get("out", 0) for m in today.values())
    t_cost = sum(m.get("cost", 0.0) for m in today.values())
    print(f"  \033[2mtoday: {t_req} requests · {_fmt_tok(t_in)} tokens sent · {_fmt_tok(t_out)} received · {_fmt_cost(t_cost)}\033[0m")
    per = today_by_backend()
    if per:
        bits = [_backend_spend_bit(b, per[b])
                for b in ("openrouter", "claude", "codex", "gemini", "local") if b in per]
        print(f"  \033[2mby agent: {' · '.join(bits)}\033[0m")
    credits = fetch_openrouter_credits()
    if credits:
        total = credits.get("total_credits") or 0.0
        used = credits.get("total_usage") or 0.0
        print(f"  \033[2mopenrouter balance: ${max(0.0, total - used):.2f} remaining of ${total:.2f} (used ${used:.4f})\033[0m")
    print()


def reset() -> None:
    with _lock:
        _save({"models": {}, "daily": {}})
