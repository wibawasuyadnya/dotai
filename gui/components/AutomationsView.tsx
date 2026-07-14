"use client";

import { useRef, useState } from "react";
import Icon from "./Icon";
import {
  API, Agent, Automation, AutomationTrigger,
  createAutomation, deleteAutomation, exportAutomation, importAutomation,
  runAutomation, updateAutomation,
} from "@/lib/api";

// Automations page — same pattern as Projects: a grid of cards, click one to
// open its editor page (no popup).

const TEMPLATES: { label: string; data: Partial<Automation> }[] = [
  {
    label: "Check GitLab commits",
    data: {
      name: "GitLab commits check", icon: "🦊",
      trigger: { type: "interval", every_minutes: 60 },
      prompt:
        "Check the latest commits on my GitLab repos with `glab` (or `git log` in the project). " +
        "Summarize what changed since the last check: authors, branches, anything that looks like it needs my review.",
    },
  },
  {
    label: "Project-management tasks digest",
    data: {
      name: "PM tasks digest", icon: "📋",
      trigger: { type: "daily", at: "09:00" },
      prompt:
        "Fetch my open tasks from the project management tool (use the MCP tools or curl the API) " +
        "and write a short morning digest: what is due today, what is blocked, what moved since yesterday.",
    },
  },
  {
    label: "Self-review (heartbeat)",
    data: {
      name: "Self-review", icon: "🪞",
      trigger: { type: "daily", at: "18:00" },
      prompt:
        "Read ~/.config/orkesai/.learnings/learnings.md and ~/.config/orkesai/PROFILE.md (use read_file; " +
        "if a file is missing, say so and continue). Review the recent corrections and failures: what patterns " +
        "keep repeating? Report the 3 most important behavioral improvements for the team, plus any profile " +
        "updates you would recommend. Be specific and short.",
      actions: { webhook_url: "", save_note: true },
    },
  },
  {
    label: "Repo → Obsidian notes",
    data: {
      name: "Repo to Obsidian", icon: "💎",
      trigger: { type: "manual" },
      prompt:
        "Read the latest changes in the project repo and update my Obsidian vault notes for it " +
        "(one markdown note per feature area). Keep the existing note structure, append a dated changelog entry.",
    },
  },
];

const TRIGGERS: { v: AutomationTrigger["type"]; label: string; help: string }[] = [
  { v: "manual", label: "Manual", help: "Runs only when you press Run." },
  { v: "interval", label: "Every N minutes", help: "Runs on a repeating timer while the server is up." },
  { v: "daily", label: "Daily at…", help: "Runs once a day at the given time." },
  { v: "webhook", label: "Webhook", help: "Runs when something POSTs to its hook URL (GitLab, Zapier, curl…)." },
];

function trigLabel(a: Automation): string {
  if (a.trigger.type === "interval") return `every ${a.trigger.every_minutes} min`;
  if (a.trigger.type === "daily") return `daily at ${a.trigger.at}`;
  return a.trigger.type;
}

export default function AutomationsView({
  automations, agents, openId, onOpen, onChanged, onOpenSession,
}: {
  automations: Automation[];
  agents: Agent[];
  openId: string | null; // an automation id, "new", or null (grid)
  onOpen: (id: string | null) => void;
  onChanged: () => void;
  onOpenSession: (id: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const open = openId && openId !== "new" ? automations.find((a) => a.id === openId) ?? null : null;

  async function doImport(f: File) {
    try {
      const data = JSON.parse(await f.text());
      const a = await importAutomation(data);
      onChanged();
      onOpen(a.id);
    } catch (e) { window.alert(e instanceof Error ? e.message : "not a valid template"); }
  }

  if (openId === "new" || open) {
    return (
      <AutomationEditor
        key={openId} // reset the form when switching between automations
        automation={open}
        agents={agents}
        onBack={() => onOpen(null)}
        onChanged={onChanged}
        onOpen={onOpen}
        onOpenSession={onOpenSession}
      />
    );
  }

  return (
    <div className="projects-page">
      <div className="projects-head">
        <h1>Automations</h1>
        <span style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => fileRef.current?.click()}>
            <Icon name="download" size={14} className="flip" /> Import
          </button>
          <input ref={fileRef} type="file" accept="application/json,.json" hidden
            onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])} />
          <button className="btn primary" onClick={() => onOpen("new")}>
            <Icon name="plus" size={15} /> New automation
          </button>
        </span>
      </div>
      <p className="setting-help" style={{ padding: "0 4px 10px" }}>
        A trigger fires a prompt, the agent runs it with its tools and MCP servers, then the actions
        forward the result. Each automation keeps its runs in its own ⚙ chat.
      </p>
      <div className="project-grid">
        {automations.map((a) => (
          <button className="project-card" key={a.id} onClick={() => onOpen(a.id)}>
            <div className="pc-icon"><span className="emoji-glyph" style={{ fontSize: 19 }}>{a.icon || "⚡"}</span></div>
            <div className="pc-title">{a.name}</div>
            <div className="pc-desc">{a.prompt}</div>
            <div className="pc-foot">
              <span>{a.running ? "running…" : a.enabled ? trigLabel(a) : "off"}</span>
              {a.last_run && <span>last: {a.last_run.status}</span>}
            </div>
          </button>
        ))}
        {automations.length === 0 && <p className="dim">No automations yet — create one or import a template.</p>}
      </div>
    </div>
  );
}

function AutomationEditor({
  automation, agents, onBack, onChanged, onOpen, onOpenSession,
}: {
  automation: Automation | null;
  agents: Agent[];
  onBack: () => void;
  onChanged: () => void;
  onOpen: (id: string | null) => void;
  onOpenSession: (id: string) => void;
}) {
  const [name, setName] = useState(automation?.name ?? "");
  const [icon, setIcon] = useState(automation?.icon ?? "⚡");
  const [enabled, setEnabled] = useState(automation?.enabled ?? true);
  const [trigType, setTrigType] = useState<AutomationTrigger["type"]>(automation?.trigger.type ?? "manual");
  const [everyMin, setEveryMin] = useState(automation?.trigger.every_minutes ?? 60);
  const [dailyAt, setDailyAt] = useState(automation?.trigger.at ?? "09:00");
  const [prompt, setPrompt] = useState(automation?.prompt ?? "");
  const [agent, setAgent] = useState(automation?.agent ?? "");
  const [project, setProject] = useState(automation?.project ?? "");
  const [webhookUrl, setWebhookUrl] = useState(automation?.actions.webhook_url ?? "");
  const [saveNote, setSaveNote] = useState(automation?.actions.save_note ?? false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const hookUrl = automation ? `${API}/api/hooks/${automation.id}` : "";

  function applyTemplate(i: number) {
    const t = TEMPLATES[i].data;
    setName(t.name ?? "");
    setIcon(t.icon ?? "⚡");
    setPrompt(t.prompt ?? "");
    setTrigType(t.trigger?.type ?? "manual");
    if (t.trigger?.every_minutes) setEveryMin(t.trigger.every_minutes);
    if (t.trigger?.at) setDailyAt(t.trigger.at);
    if (t.actions) { setWebhookUrl(t.actions.webhook_url ?? ""); setSaveNote(!!t.actions.save_note); }
  }

  function payload(): Partial<Automation> {
    const trigger: AutomationTrigger = { type: trigType };
    if (trigType === "interval") trigger.every_minutes = everyMin;
    if (trigType === "daily") trigger.at = dailyAt;
    return {
      name: name.trim(), icon, enabled, trigger, prompt: prompt.trim(), agent,
      project: project.trim(),
      actions: { webhook_url: webhookUrl.trim(), save_note: saveNote },
    };
  }

  async function save(): Promise<Automation | null> {
    setErr(""); setBusy(true);
    try {
      const a = automation
        ? await updateAutomation(automation.id, payload())
        : await createAutomation(payload());
      onChanged();
      setBusy(false);
      if (!automation) onOpen(a.id); // a new automation stays open as itself
      return a;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not save");
      setBusy(false);
      return null;
    }
  }

  async function runNow() {
    const a = await save();
    if (!a) return;
    try {
      await runAutomation(a.id);
      setMsg("Run started — the result shows in this automation's chat and under Last run.");
      onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : "could not run"); }
  }

  async function doExport() {
    if (!automation) return;
    const tpl = await exportAutomation(automation.id);
    const blob = new Blob([JSON.stringify(tpl, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `orkesai-automation-${name.trim().replace(/\W+/g, "-") || "template"}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  }

  async function remove() {
    if (!automation) return;
    if (!window.confirm(`Delete the "${automation.name}" automation? Its chat history stays on disk.`)) return;
    await deleteAutomation(automation.id);
    onChanged();
    onBack();
  }

  return (
    <div className="project-detail auto-editor">
      <button className="back-btn" onClick={onBack}>
        <Icon name="chevron" size={14} className="flip" /> Automations
      </button>

      <div className="auto-head">
        <span className="auto-head-icon emoji-glyph">{icon || "⚡"}</span>
        <h1>{automation ? automation.name : "New automation"}</h1>
        <label className="auto-enabled">
          {enabled ? "Enabled" : "Off"}
          <input type="checkbox" className="toggle" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        </label>
      </div>

      {!automation && (
        <div className="auto-section">
          <div className="auto-section-label">Start from a template <span className="field-val">optional</span></div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {TEMPLATES.map((t, i) => (
              <button key={t.label} className="btn" onClick={() => applyTemplate(i)}>{t.label}</button>
            ))}
          </div>
        </div>
      )}

      <div className="auto-section">
        <div className="auto-section-label">Details</div>
        <div className="row">
          <div className="field" style={{ width: 72 }}>
            <label>Icon</label>
            <input value={icon} onChange={(e) => setIcon(e.target.value)} style={{ textAlign: "center" }} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Name</label>
            <input autoFocus={!automation} value={name} placeholder="e.g. GitLab commits check" onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
      </div>

      <div className="auto-section">
        <div className="auto-section-label">Trigger</div>
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>When it runs</label>
            <select value={trigType} onChange={(e) => setTrigType(e.target.value as AutomationTrigger["type"])}>
              {TRIGGERS.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
            </select>
          </div>
          {trigType === "interval" && (
            <div className="field" style={{ width: 130 }}>
              <label>Every (min)</label>
              <input type="number" min={5} value={everyMin} onChange={(e) => setEveryMin(parseInt(e.target.value || "5", 10))} />
            </div>
          )}
          {trigType === "daily" && (
            <div className="field" style={{ width: 130 }}>
              <label>At</label>
              <input type="time" value={dailyAt} onChange={(e) => setDailyAt(e.target.value)} />
            </div>
          )}
          <div className="field" style={{ flex: 1 }}>
            <label>Runs as</label>
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">Default agent</option>
              {agents.map((a) => <option key={a.id} value={a.id}>@{a.id} — {a.name}</option>)}
            </select>
          </div>
        </div>
        <p className="help">
          {TRIGGERS.find((t) => t.v === trigType)?.help}{" "}
          Runs are non-interactive: actions that would normally ask for your approval are denied; reads and safe commands work.
        </p>
        {trigType === "webhook" && automation && (
          <div className="field">
            <label>Hook URL <span className="field-val" style={{ cursor: "pointer" }}
              onClick={() => navigator.clipboard?.writeText(hookUrl)}>copy</span></label>
            <input readOnly value={hookUrl} onFocus={(e) => e.target.select()} />
            <p className="help">POST anything to this URL to fire the automation — the request body is passed to the prompt.</p>
          </div>
        )}
        {trigType === "webhook" && !automation && (
          <p className="help">Save first — the hook URL appears here after the automation is created.</p>
        )}
      </div>

      <div className="auto-section">
        <div className="auto-section-label">Works in folder <span className="field-val">optional</span></div>
        <div className="field">
          <input placeholder="e.g. ~/Documents/Obsidian Vault — file writes are allowed INSIDE this folder only"
            value={project} onChange={(e) => setProject(e.target.value)} />
          <p className="help">
            Commands run here and the agent may create/update files in it without asking.
            Writes outside it and destructive shell commands are always denied in automation runs.
          </p>
        </div>
      </div>

      <div className="auto-section">
        <div className="auto-section-label">Prompt</div>
        <div className="field">
          <label>What should the agent do each run?</label>
          <textarea rows={6} value={prompt} placeholder="e.g. Check the repo for new commits and summarize them…"
            onChange={(e) => setPrompt(e.target.value)} />
        </div>
      </div>

      <div className="auto-section">
        <div className="auto-section-label">After each run</div>
        <div className="field">
          <label>Forward the result to a webhook <span className="field-val">optional</span></label>
          <input placeholder="https://hooks.slack.com/… (POSTs a JSON with the output)" value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)} />
        </div>
        <label className="auto-check">
          <input type="checkbox" className="toggle" checked={saveNote} onChange={(e) => setSaveNote(e.target.checked)} />
          Save the result as a note in this automation&apos;s chat
        </label>
      </div>

      {automation?.last_run && (
        <div className="auto-section">
          <div className="auto-section-label">
            Last run — {new Date(automation.last_run.ts * 1000).toLocaleString()} · {automation.last_run.status}
            {automation.session && (
              <span className="field-val" style={{ cursor: "pointer", marginLeft: 8 }}
                onClick={() => onOpenSession(automation.session)}>
                open its chat
              </span>
            )}
          </div>
          <pre className="confirm-detail" style={{ maxHeight: 160, overflow: "auto" }}>{automation.last_run.summary}</pre>
        </div>
      )}

      {err && <p className="form-err">{err}</p>}
      {msg && <p className="help" style={{ color: "var(--accent)" }}>{msg}</p>}

      <div className="modal-actions auto-actions">
        {automation && (
          <>
            <button className="btn danger" onClick={remove} disabled={busy}><Icon name="trash" size={13} /> Delete</button>
            <button className="btn" onClick={doExport} disabled={busy}><Icon name="download" size={13} /> Export</button>
          </>
        )}
        <span className="spacer" />
        <button className="btn" onClick={runNow} disabled={busy || !name.trim() || !prompt.trim()}>
          <Icon name="play" size={12} /> {automation?.running ? "Running…" : "Run now"}
        </button>
        <button className="btn primary" onClick={save} disabled={busy || !name.trim() || !prompt.trim()}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
