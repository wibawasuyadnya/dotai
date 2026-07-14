# File: ~/.config/orkesai/modules/agent_automations.py
"""User automations: trigger → prompt → actions (no @role required).

An automation is a saved prompt that runs by itself — on a schedule (every N
minutes, or daily at HH:MM), when an external webhook fires, or manually.
The run executes through the normal agent engine (same tools + MCP servers),
in a dedicated session per automation so every run is inspectable as a chat.
Runs are non-interactive: any tool call that would ask the user for approval
is auto-DENIED (reads, listings and non-destructive shell still work).

Actions after a run: POST the result to a user webhook and/or save it as a
note in the automation session's Notes panel.

Stored in ~/.config/orkesai/automations.json. Export strips ids/history so
a template can be shared and imported by someone else.
"""
import json
import os
import threading
import time
import urllib.request as urlreq
import uuid

CFG_DIR = os.path.expanduser("~/.config/orkesai")
AUTOMATIONS_FILE = os.path.join(CFG_DIR, "automations.json")
_lock = threading.Lock()
_running = set()  # automation ids currently executing (no overlapping runs)

TRIGGER_TYPES = ("manual", "interval", "daily", "webhook")
_MAX_RUNS = 20


def _load() -> list:
    try:
        with open(AUTOMATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(lst: list) -> None:
    with _lock:
        tmp = AUTOMATIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False, indent=1)
        os.replace(tmp, AUTOMATIONS_FILE)


def _normalize(data: dict, base: dict = None) -> tuple:
    """Merge user fields into a valid automation dict. Returns (a, error)."""
    a = dict(base or {})
    if "name" in data or not base:
        name = str(data.get("name") or "").strip()
        if not name:
            return None, "name is required"
        a["name"] = name[:80]
    if "prompt" in data or not base:
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            return None, "prompt is required"
        a["prompt"] = prompt[:20000]
    if "icon" in data:
        a["icon"] = str(data.get("icon") or "⚡")[:8]
    if "enabled" in data:
        a["enabled"] = bool(data["enabled"])
    if "agent" in data:
        a["agent"] = str(data.get("agent") or "").strip()
    for k in ("backend", "model"):
        if k in data:
            a[k] = str(data.get(k) or "").strip()
    if "project" in data:
        # the automation's working folder — file writes are allowed INSIDE it
        a["project"] = os.path.expanduser(str(data.get("project") or "").strip())
    if "actions" in data:
        act = data.get("actions") or {}
        a["actions"] = {"webhook_url": str(act.get("webhook_url") or "").strip()[:2000],
                        "save_note": bool(act.get("save_note"))}
    if "trigger" in data or not base:
        t = data.get("trigger") or {}
        typ = str(t.get("type") or "manual").strip().lower()
        if typ not in TRIGGER_TYPES:
            return None, f"trigger type must be one of {', '.join(TRIGGER_TYPES)}"
        trig = {"type": typ}
        if typ == "interval":
            try:
                mins = int(t.get("every_minutes") or 0)
            except (TypeError, ValueError):
                mins = 0
            if mins < 5:
                return None, "interval must be at least 5 minutes"
            trig["every_minutes"] = mins
        if typ == "daily":
            at = str(t.get("at") or "").strip()
            try:
                time.strptime(at, "%H:%M")
            except ValueError:
                return None, "daily trigger needs at like 09:30"
            trig["at"] = at
        a["trigger"] = trig
    a.setdefault("icon", "⚡")
    a.setdefault("enabled", True)
    a.setdefault("agent", "")
    a.setdefault("backend", "")
    a.setdefault("model", "")
    a.setdefault("project", "")
    a.setdefault("actions", {"webhook_url": "", "save_note": False})
    a["updated"] = int(time.time())
    return a, ""


def list_automations() -> list:
    out = []
    for a in _load():
        d = dict(a)
        d.pop("runs", None)  # the list view only needs last_run
        d["running"] = a["id"] in _running
        out.append(d)
    return out


def get_automation(aid: str):
    for a in _load():
        if a.get("id") == aid:
            return a
    return None


def create_automation(data: dict):
    a, err = _normalize(data)
    if err:
        return None, err
    a.update({"id": uuid.uuid4().hex[:10], "session": "",
              "created": int(time.time()), "last_run": None, "runs": []})
    lst = _load()
    lst.insert(0, a)
    _save(lst)
    return a, ""


def update_automation(aid: str, data: dict):
    lst = _load()
    for i, a in enumerate(lst):
        if a.get("id") == aid:
            merged, err = _normalize(data, a)
            if err:
                return None, err
            lst[i] = merged
            _save(lst)
            return merged, ""
    return None, "automation not found"


def delete_automation(aid: str):
    lst = [a for a in _load() if a.get("id") != aid]
    _save(lst)
    return True, ""


def export_automation(aid: str):
    """A shareable template: the recipe without ids, history or private URLs."""
    a = get_automation(aid)
    if not a:
        return None, "automation not found"
    return {"orkesai_automation": 1,
            "name": a["name"], "icon": a.get("icon", "⚡"),
            "trigger": a.get("trigger", {"type": "manual"}),
            "prompt": a.get("prompt", ""),
            "agent": a.get("agent", ""),
            "actions": {"webhook_url": "", "save_note": bool((a.get("actions") or {}).get("save_note"))}}, ""


def import_automation(data: dict):
    if not isinstance(data, dict) or not data.get("orkesai_automation"):
        return None, "not a OrkesAI automation template (missing orkesai_automation marker)"
    return create_automation(data)


# ── Running ──────────────────────────────────────────────────────────────────

def _record_run(aid: str, status: str, summary: str) -> None:
    lst = _load()
    for a in lst:
        if a.get("id") == aid:
            run = {"ts": int(time.time()), "status": status, "summary": summary[:2000]}
            a["last_run"] = run
            a["runs"] = ([run] + (a.get("runs") or []))[:_MAX_RUNS]
    _save(lst)


def _set_session(aid: str, sid: str) -> None:
    lst = _load()
    for a in lst:
        if a.get("id") == aid:
            a["session"] = sid
    _save(lst)


def run_automation(aid: str, payload: str = ""):
    """Execute one automation now (blocking — callers that must not wait spawn
    this in a thread via run_async). Returns (run_record, error)."""
    import agent_service as svc
    a = get_automation(aid)
    if not a:
        return None, "automation not found"
    if aid in _running:
        return None, "already running"
    _running.add(aid)
    try:
        # every automation owns one session, so runs read as a normal chat
        # (history, files, notes) — created lazily on the first run
        sess = svc.find_session(a.get("session") or "") if a.get("session") else None
        if not sess:
            agent_id = a.get("agent") or "default"
            if agent_id != "default" and not any(x["id"] == agent_id for x in svc.list_agents()):
                agent_id = "default"
            sess = svc.create_session(agent_id, title=f"⚙ {a['name']}",
                                      backend=a.get("backend", ""), model=a.get("model", ""),
                                      project=a.get("project", ""))
            _set_session(aid, sess["id"])
        elif sess.get("project") != a.get("project", ""):
            # the automation's working folder changed — follow it
            sess["project"] = a.get("project", "")
            svc._save_session(sess)
        prompt = a["prompt"]
        if payload:
            prompt += f"\n\n### Webhook payload:\n{payload[:6000]}"
        parts, status = [], "ok"
        for ev in svc.stream_chat(sess, prompt):
            t = ev.get("type")
            if t == "token":
                parts.append(ev.get("text", ""))
            elif t == "confirm":
                # Non-interactive policy: file WRITES inside the automation's
                # own project folder are allowed (that's what the folder is
                # for); everything else that would ask a human — writes outside
                # the project, destructive shell — is denied.
                ok = (ev.get("tool") == "write_file"
                      and bool(a.get("project"))
                      and "outside the project" not in str(ev.get("action", "")))
                svc.resolve_confirm(ev.get("id", ""), ok)
            elif t == "offline":
                # never silently switch to the local model unattended
                svc.resolve_confirm(ev.get("id", ""), False)
            elif t == "error":
                status = "error"
                parts.append(f"\n[error] {ev.get('message', '')}")
        output = "".join(parts).strip() or "(no output)"
        # ── actions ──
        act = a.get("actions") or {}
        if act.get("webhook_url"):
            try:
                body = json.dumps({"automation": aid, "name": a["name"],
                                   "ts": int(time.time()), "status": status,
                                   "output": output[:20000]}).encode("utf-8")
                req = urlreq.Request(act["webhook_url"], data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
                urlreq.urlopen(req, timeout=15)
            except Exception as e:
                status = status if status == "error" else "ok (webhook failed)"
                output += f"\n[webhook action failed: {e}]"
        if act.get("save_note"):
            try:
                scope, _ = svc._scope_of(sess["agent"], sess["id"])
                stamp = time.strftime("%Y-%m-%d %H:%M")
                svc.create_note(scope, f"⚙ {a['name']} — {stamp}", output[:20000],
                                source="automation")
            except Exception:
                pass
        if status == "error":
            # feed failures into the (opt-in) learning loop — no-op when off
            try:
                svc._record_learning("automation failed", f"'{a['name']}': {output[:250]}")
            except Exception:
                pass
        _record_run(aid, status, output)
        return {"status": status, "summary": output[:2000], "session": sess["id"]}, ""
    except Exception as e:
        _record_run(aid, "error", str(e))
        return None, f"run failed: {e}"
    finally:
        _running.discard(aid)


def run_async(aid: str, payload: str = "") -> None:
    threading.Thread(target=run_automation, args=(aid, payload), daemon=True).start()


# ── Scheduler (daemon thread, started once by the server) ────────────────────

def _due(a: dict, now: float) -> bool:
    trig = a.get("trigger") or {}
    last = (a.get("last_run") or {}).get("ts", 0)
    if trig.get("type") == "interval":
        return now - last >= int(trig.get("every_minutes", 0)) * 60
    if trig.get("type") == "daily":
        lt = time.localtime(now)
        if time.strftime("%H:%M", lt) != trig.get("at"):
            return False
        return time.strftime("%Y-%m-%d", time.localtime(last)) != time.strftime("%Y-%m-%d", lt)
    return False


def _scheduler_loop() -> None:
    while True:
        try:
            now = time.time()
            for a in _load():
                if (a.get("enabled") and a.get("id") not in _running
                        and (a.get("trigger") or {}).get("type") in ("interval", "daily")
                        and _due(a, now)):
                    run_async(a["id"])
        except Exception:
            pass  # the scheduler must survive anything
        time.sleep(30)


_scheduler_started = False


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()
