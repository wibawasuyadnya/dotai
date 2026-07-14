"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import Icon from "@/components/Icon";
import Composer from "@/components/Composer";
import ChatList from "@/components/ChatList";
import ProjectsView from "@/components/ProjectsView";
import SettingsModal from "@/components/SettingsModal";
import SetupView from "@/components/SetupView";
import AgentModal from "@/components/AgentModal";
import AskModal, { AskSpec } from "@/components/AskModal";
import AutomationsView from "@/components/AutomationsView";
import GroupModal from "@/components/GroupModal";
import FilesPanel from "@/components/FilesPanel";
import { downloadImage } from "@/lib/files";
import {
  Agent,
  Attachment,
  Automation,
  Backend,
  DEFAULT_AGENT,
  Group,
  Message,
  Project,
  Session,
  SessionMeta,
  Settings,
  addModel,
  answerConfirm,
  createProject,
  createSession,
  deleteSession,
  getAgents,
  getAutomations,
  getBackends,
  getGroups,
  getOpenrouterCatalog,
  getProjects,
  getSession,
  getSessions,
  getSettings,
  runAutomation,
  saveSettings,
  streamChat,
  updateSession,
} from "@/lib/api";

type ConfirmReq = { id: string; tool: string; action: string; detail: string };
type View = "chat" | "projects" | "automations";
// A live stream tied to one session, so it renders only in its own chat
type StreamState = {
  messages: Message[];
  streaming: boolean;
  confirm: ConfirmReq | null;
  controller: AbortController;
};

/** A fenced code block: header with the language name + a copy button, and the
 *  syntax-highlighted body (rehype-highlight adds the color spans). */
function CodeBlock({ children }: { children?: React.ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  // language comes from the child <code class="language-xxx"> that markdown emits
  const child = Array.isArray(children) ? children[0] : children;
  const cls: string = (child as { props?: { className?: string } })?.props?.className ?? "";
  const lang = /language-([\w-]+)/.exec(cls)?.[1] ?? "";
  const copy = () => {
    const text = ref.current?.innerText ?? "";
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  };
  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span className="codeblock-lang">{lang || "text"}</span>
        <button className="codeblock-copy" onClick={copy} title="Copy code">
          <Icon name={copied ? "check" : "copy"} size={15} />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre ref={ref}>{children}</pre>
    </div>
  );
}

/** Group chat: @mentions of participants render bold + accent-colored. */
function MentionText({ text, ids }: { text: string; ids: string[] }) {
  const parts = text.split(/(@[A-Za-z0-9_-]+)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("@") && ids.includes(p.slice(1))
          ? <b key={i} className="mention">{p}</b>
          : p,
      )}
    </>
  );
}

/** Stacked participant avatars (up to 4 + "+N") for a group row / topbar. */
function GroupAvatars({ group, agents, size = 22 }: { group: Group; agents: Agent[]; size?: number }) {
  const members = group.participants
    .map((id) => agents.find((a) => a.id === id))
    .filter(Boolean) as Agent[];
  const shown = members.slice(0, 4);
  const extra = members.length - shown.length;
  return (
    <span className="stack-avatars" style={{ height: size }}>
      {shown.map((a) => (
        <span key={a.id} className="stack-avatar emoji-glyph" style={{ width: size, height: size, fontSize: size * 0.62 }} title={`@${a.id}`}>
          {a.icon}
        </span>
      ))}
      {extra > 0 && (
        <span className="stack-avatar stack-more" style={{ width: size, height: size }} title={members.slice(4).map((a) => "@" + a.id).join(" ")}>
          +{extra}
        </span>
      )}
    </span>
  );
}

/** Split assistant text into markdown blocks and "∗ tool" activity chips. */
function splitParts(content: string): { type: "text" | "act"; text: string }[] {
  const parts: { type: "text" | "act"; text: string }[] = [];
  for (const line of content.split("\n")) {
    if (line.startsWith("∗ ")) parts.push({ type: "act", text: line.slice(2).trim() });
    else if (parts.length && parts[parts.length - 1].type === "text") parts[parts.length - 1].text += "\n" + line;
    else parts.push({ type: "text", text: line });
  }
  return parts.filter((p) => p.type === "act" || p.text.trim());
}

export default function Home() {
  const [view, setView] = useState<View>("chat");
  const [railOpen, setRailOpen] = useState(true);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [backends, setBackends] = useState<Backend[]>([]);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [settings, setSettings] = useState<Settings>({
    agent: "auto", edit: "on", spellcheck: true, default_agent: DEFAULT_AGENT,
    default_backend: "openrouter", default_model: "", default_system: "", appearance: "dark", full_disk: false,
    onboarded: true, // real value arrives with getSettings — avoids a splash flash
    learning: false,
  });

  const [active, setActive] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [confirmReq, setConfirmReq] = useState<ConfirmReq | null>(null);
  const [offline, setOffline] = useState(false);

  // composer selection for the *next* new chat
  const [selRole, setSelRole] = useState(DEFAULT_AGENT);
  const [selBackend, setSelBackend] = useState("openrouter");
  const [selModel, setSelModel] = useState("");
  const [selEffort, setSelEffort] = useState("");
  const [selTemp, setSelTemp] = useState("");
  const [selMaxTokens, setSelMaxTokens] = useState(0);
  const [selProject, setSelProject] = useState("");
  const [offlineReq, setOfflineReq] = useState<{ id: string } | null>(null);
  const [filesOpen, setFilesOpen] = useState(false);
  const [filesVer, setFilesVer] = useState(0);

  const [showSettings, setShowSettings] = useState(false);
  // true once settings + first data load resolved — gates the setup splash
  const [booted, setBooted] = useState(false);
  const [agentModal, setAgentModal] = useState<{ agent: Agent | null } | null>(null);
  const [automations, setAutomations] = useState<Automation[]>([]);
  // which automation the Automations PAGE shows: an id, "new", or null (grid)
  const [autoFocus, setAutoFocus] = useState<string | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupModal, setGroupModal] = useState<{ group: Group | null } | null>(null);
  const [ask, setAsk] = useState<AskSpec | null>(null);
  // inline "edit & resend" state for a user bubble
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const chatRef = useRef<HTMLDivElement>(null);
  // which session is on screen right now — read inside async stream callbacks
  const activeIdRef = useRef<string | null>(null);
  // in-flight streams keyed by session id, so a "thinking" bubble or a late
  // answer only ever renders in the conversation it belongs to
  const streamsRef = useRef<Map<string, StreamState>>(new Map());

  // window.prompt() is unsupported in Electron — this promise-based dialog is
  // the drop-in replacement used for project/model prompts.
  const askText = useCallback(
    (spec: Omit<AskSpec, "resolve">): Promise<string | null> =>
      new Promise((resolve) =>
        setAsk({ ...spec, resolve: (v) => { setAsk(null); resolve(v); } }),
      ),
    [],
  );

  const refresh = useCallback(async () => {
    try {
      const [a, s, p, b, au, g] = await Promise.all([getAgents(), getSessions(), getProjects(), getBackends(), getAutomations(), getGroups()]);
      setAgents(a);
      setSessions(s);
      setProjects(p);
      setBackends(b);
      setAutomations(au);
      setGroups(g);
      setOffline(false);
      if (!selModel) {
        const or = b.find((x) => x.id === "openrouter") ?? b[0];
        if (or) setSelModel(or.models[0] ?? "");
      }
    } catch {
      setOffline(true);
    }
  }, [selModel]);

  useEffect(() => {
    Promise.allSettled([
      refresh(),
      getSettings().then((r) => setSettings(r.settings)),
    ]).then(() => setBooted(true));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = document.documentElement;
    el.dataset.theme = settings.appearance === "system" ? "" : settings.appearance;
  }, [settings.appearance]);

  // Pre-wizard installs already have a team — never bother them with setup
  useEffect(() => {
    if (!booted || settings.onboarded || agents.length === 0) return;
    saveSettings({ onboarded: true }).catch(() => {});
    setSettings((s) => ({ ...s, onboarded: true }));
  }, [booted, agents.length, settings.onboarded]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight });
  }, [messages, confirmReq]);


  // Keep the composer's engine in sync when a team agent's backend/model is
  // edited — so saving the AgentModal updates /agent · /model in realtime for
  // the next new chat with that role.
  useEffect(() => {
    if (active || selRole === DEFAULT_AGENT) return;
    const a = agents.find((x) => x.id === selRole);
    if (a) {
      setSelBackend(a.backend ?? "openrouter");
      setSelModel(a.model);
    }
  }, [agents, selRole, active]);

  const agentOf = (id: string) => agents.find((a) => a.id === id);
  const activeAgent = active ? (active.agent === DEFAULT_AGENT ? null : agentOf(active.agent)) : null;
  // group conversation: participants drive avatars, mentions and the composer
  const activeGroup = active?.group ? groups.find((g) => g.id === active.group) ?? null : null;
  const groupMembers = activeGroup ? agents.filter((a) => activeGroup.participants.includes(a.id)) : [];
  const chatAgent = active ? agentOf(active.agent) : agentOf(selRole);
  const chatName = active
    ? activeGroup ? activeGroup.name : active.agent === DEFAULT_AGENT ? "OrkesAI" : activeAgent?.name ?? "OrkesAI"
    : selRole === DEFAULT_AGENT ? "OrkesAI" : chatAgent?.name ?? "OrkesAI";
  const chatIcon = active ? activeAgent?.icon : agentOf(selRole)?.icon;
  const projName = (path: string) => projects.find((p) => p.path === path)?.name ?? (path ? path.split("/").pop() : "");

  // A team @role's engine is CANONICAL — every session under it shows the role's
  // backend/model/effort (change it by editing the @role, not per-session). The
  // default agent keeps its per-session picker.
  const curBackend = active ? (activeAgent ? activeAgent.backend || "openrouter" : active.backend || "openrouter") : selBackend;
  const curModel = active ? (activeAgent ? activeAgent.model : active.model) : selModel;
  const curEffort = active ? (activeAgent ? activeAgent.effort || "" : active.effort || "") : selEffort;
  const curTemp = active ? (activeAgent ? activeAgent.temperature || "" : active.temperature || "") : selTemp;
  const curMaxTokens = active ? (activeAgent ? activeAgent.max_tokens || 0 : active.max_tokens || 0) : selMaxTokens;
  const engineLocked = !!activeAgent; // role session → engine set on the role
  const curProject = active ? active.project : selProject;
  const localModelName = backends.find((b) => b.id === "local")?.models[0] || "the local model";

  function newChat(role?: string) {
    const r = role ?? settings.default_agent ?? DEFAULT_AGENT;
    setActive(null);
    activeIdRef.current = null;
    setMessages([]);
    setConfirmReq(null);
    setOfflineReq(null);
    setStreaming(false);
    setEditIdx(null);
    setView("chat");
    setSelRole(r);
    const a = agentOf(r);
    if (a) {
      // team @role: use its configured backend/model
      setSelBackend(a.backend ?? "openrouter");
      setSelModel(a.model);
    } else {
      // built-in default agent → the "New session with" setting picks the
      // backend + model, so a stale choice can't leak into the new chat
      const db = settings.default_backend || "openrouter";
      const bk = backends.find((b) => b.id === db) ?? backends.find((b) => b.id === "openrouter") ?? backends[0];
      setSelBackend(bk?.id ?? "openrouter");
      setSelModel(settings.default_model || bk?.models[0] || "");
    }
  }

  async function openSession(id: string) {
    const s = await getSession(id);
    setActive(s);
    activeIdRef.current = id;
    setEditIdx(null);
    setView("chat");
    // if this chat has a stream in flight, show its live buffer; otherwise the
    // saved history — never the other conversation's "thinking" bubble
    const entry = streamsRef.current.get(id);
    if (entry) {
      setMessages(entry.messages);
      setStreaming(entry.streaming);
      setConfirmReq(entry.confirm);
    } else {
      setMessages(s.messages);
      setStreaming(false);
      setConfirmReq(null);
    }
  }

  async function removeSession(id: string) {
    if (!window.confirm("Delete this chat?")) return;
    await deleteSession(id);
    if (active?.id === id) newChat();
    refresh();
  }

  // Composer's role button. In a NEW chat it just sets the role. In an EXISTING
  // default session it delegates the thread in place → @role (keep history +
  // project, switch persona + engine, drop a handoff divider, move it under the
  // role in the sidebar).
  function pickRole(roleId: string) {
    if (active && roleId !== DEFAULT_AGENT && roleId !== active.agent) {
      delegateSession(roleId);
    } else {
      newChat(roleId);
    }
  }

  async function delegateSession(roleId: string) {
    if (!active) return;
    try {
      const s = await updateSession(active.id, { agent: roleId });
      setActive(s);
      setMessages(s.messages);
      setEditIdx(null);
      refresh(); // resort the sidebar — the session now lives under @roleId
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "could not delegate");
    }
  }

  async function pickEngine(backend: string, model: string) {
    if (active) {
      const s = await updateSession(active.id, { backend, model });
      setActive((a) => (a ? { ...a, backend: s.backend, model: s.model } : a));
    } else {
      setSelBackend(backend);
      setSelModel(model);
    }
  }

  async function pickEffort(effort: string) {
    if (active) {
      const s = await updateSession(active.id, { effort });
      setActive((a) => (a ? { ...a, effort: s.effort } : a));
    } else {
      setSelEffort(effort);
    }
  }

  async function pickTemp(temperature: string) {
    if (active) {
      const s = await updateSession(active.id, { temperature });
      setActive((a) => (a ? { ...a, temperature: s.temperature } : a));
    } else {
      setSelTemp(temperature);
    }
  }

  async function pickMaxTokens(max_tokens: number) {
    if (active) {
      const s = await updateSession(active.id, { max_tokens });
      setActive((a) => (a ? { ...a, max_tokens: s.max_tokens } : a));
    } else {
      setSelMaxTokens(max_tokens);
    }
  }

  async function pickProject(path: string) {
    if (active) {
      const s = await updateSession(active.id, { project: path });
      setActive((a) => (a ? { ...a, project: s.project } : a));
    } else {
      setSelProject(path);
    }
    refresh();
  }

  async function newProjectFlow() {
    const name = await askText({ title: "New project", placeholder: "Project name", okLabel: "Create" });
    if (!name?.trim()) return;
    try {
      const p = await createProject(name.trim());
      await refresh();
      pickProject(p.path);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "could not create");
    }
  }

  async function addModelFlow(backend: string) {
    // OpenRouter: offer the FULL live catalog as autocomplete (Odysseus-style)
    let suggestions: string[] = [];
    let hint = "";
    if (backend === "openrouter") {
      try {
        const c = await getOpenrouterCatalog();
        suggestions = c.models.map((x) => x.id);
        hint = `${c.count} models loaded from openrouter.ai — start typing to search`;
      } catch { /* offline — plain input still works */ }
    }
    const m = await askText({ title: `Add a model for ${backend}`, placeholder: "exact model slug", okLabel: "Add", suggestions, hint });
    if (!m?.trim()) return;
    try {
      const b = await addModel(backend, m.trim());
      setBackends(b);
      pickEngine(backend, m.trim());
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "could not add");
    }
  }

  // send from the main composer (creates a session lazily for new chats). An
  // explicit `override` session is used when a caller already made one (e.g.
  // "start chat in project"), so we never create a duplicate.
  async function send(text: string, atts: Attachment[], resendTruncate?: number, override?: Session) {
    let sess = override ?? active;
    if (sess && resendTruncate !== undefined) {
      await updateSession(sess.id, { truncate: resendTruncate });
    }
    const wasActive = !!sess;
    if (!sess) {
      try {
        sess = await createSession(selRole, { project: selProject, backend: selBackend, model: selModel, effort: selEffort, temperature: selTemp, max_tokens: selMaxTokens });
      } catch {
        setOffline(true);
        return;
      }
      setActive(sess);
      activeIdRef.current = sess.id;
    }
    const sid = sess.id;

    const prior = resendTruncate !== undefined ? messages.slice(0, resendTruncate) : wasActive && !override ? messages : [];
    const userMsg: Message = {
      role: "user", content: text, text,
      attachments: atts.filter((a) => !a.type.startsWith("image/")),
      images: atts.filter((a) => a.type.startsWith("image/")).map((a) => a.url!),
    };
    const startMsgs: Message[] = [...prior, userMsg, { role: "assistant", content: "" }];

    // register this stream against its session so it renders only there
    const controller = new AbortController();
    const st: StreamState = { messages: startMsgs, streaming: true, confirm: null, controller };
    streamsRef.current.set(sid, st);
    if (activeIdRef.current === sid) { setMessages(startMsgs); setStreaming(true); setConfirmReq(null); }

    let acc = "";
    await streamChat(sid, text, atts, (ev) => {
      if (ev.type === "token") {
        acc += ev.text;
        const last = st.messages[st.messages.length - 1];
        st.messages = [...st.messages.slice(0, -1), { ...last, role: "assistant", content: acc }];
        if (activeIdRef.current === sid) setMessages(st.messages);
      } else if (ev.type === "role") {
        // group chat: the next reply is by this @role — start a fresh bubble
        acc = "";
        const last = st.messages[st.messages.length - 1];
        if (last.role === "assistant" && !last.content && !last.images?.length) {
          st.messages = [...st.messages.slice(0, -1), { ...last, agent: ev.id }];
        } else {
          st.messages = [...st.messages, { role: "assistant", content: "", agent: ev.id }];
        }
        if (activeIdRef.current === sid) setMessages(st.messages);
      } else if (ev.type === "image") {
        // an image-output model returned a picture — attach it to the answer
        const last = st.messages[st.messages.length - 1];
        st.messages = [...st.messages.slice(0, -1), { ...last, role: "assistant", images: [...(last.images ?? []), ev.url] }];
        if (activeIdRef.current === sid) setMessages(st.messages);
      } else if (ev.type === "confirm") {
        st.confirm = ev;
        if (activeIdRef.current === sid) setConfirmReq(ev);
      } else if (ev.type === "offline") {
        if (activeIdRef.current === sid) setOfflineReq({ id: ev.id });
      } else if (ev.type === "reload") {
        // server changed the thread on disk (e.g. paused after going offline)
        if (activeIdRef.current === sid) {
          setOfflineReq(null);
          getSession(sid).then((s) => {
            if (activeIdRef.current === sid) { setMessages(s.messages); setActive(s); }
          }).catch(() => {});
        }
      } else if (ev.type === "done") {
        st.streaming = false; st.confirm = null;
        if (activeIdRef.current === sid) { setConfirmReq(null); setStreaming(false); }
        setActive((a) => (a && a.id === sid ? { ...a, usage: ev.usage, title: ev.title } : a));
        refresh();
        setFilesVer((v) => v + 1); // a completed turn may have added files
      } else if (ev.type === "error") {
        st.streaming = false; st.confirm = null;
        st.messages = [...st.messages.slice(0, -1), { role: "assistant", content: `⚠️ **Something went wrong:** ${ev.message}` }];
        if (activeIdRef.current === sid) { setConfirmReq(null); setStreaming(false); setMessages(st.messages); }
      }
    }, controller.signal);

    // stream closed (done, error, or stopped) — clear its live entry so a later
    // reopen reads the saved history from disk
    st.streaming = false;
    if (activeIdRef.current === sid) { setStreaming(false); setOfflineReq(null); }
    streamsRef.current.delete(sid);
  }

  function stopGenerating() {
    const id = activeIdRef.current;
    if (!id) return;
    const st = streamsRef.current.get(id);
    if (st) { st.controller.abort(); st.streaming = false; }
    setStreaming(false);
    setConfirmReq(null);
  }

  function onComposerSend() {
    const text = draft.trim();
    if ((!text && attachments.length === 0) || streaming) return;
    const atts = attachments;
    setDraft("");
    setAttachments([]);
    send(text, atts);
  }

  function startEdit(index: number) {
    const m = messages[index];
    setEditIdx(index);
    setEditText(m.text ?? m.content ?? "");
  }

  function cancelEdit() {
    setEditIdx(null);
    setEditText("");
  }

  async function saveEdit(index: number) {
    const text = editText.trim();
    if (!text) return;
    setEditIdx(null);
    setEditText("");
    // drop this user turn and everything after it, then resend the edit
    send(text, [], index);
  }

  async function startChatInProject(
    projectPath: string,
    firstMessage: string,
    atts: Attachment[] = [],
    opts: { backend?: string; model?: string; effort?: string; role?: string; temperature?: string; max_tokens?: number } = {},
  ) {
    let sess: Session;
    try {
      sess = await createSession(opts.role ?? DEFAULT_AGENT, {
        project: projectPath,
        backend: opts.backend ?? selBackend,
        model: opts.model ?? selModel,
        effort: opts.effort ?? selEffort,
        temperature: opts.temperature ?? selTemp,
        max_tokens: opts.max_tokens ?? selMaxTokens,
      });
    } catch {
      setOffline(true);
      return;
    }
    setActive(sess);
    activeIdRef.current = sess.id;
    setMessages([]);
    setSelProject(projectPath);
    setView("chat");
    send(firstMessage, atts, undefined, sess);
  }

  async function answer(approve: boolean) {
    if (!confirmReq) return;
    await answerConfirm(confirmReq.id, approve);
    setConfirmReq(null);
  }

  // offline choice: approve = continue with the local model, deny = wait
  async function answerOffline(useLocal: boolean) {
    if (!offlineReq) return;
    await answerConfirm(offlineReq.id, useLocal);
    setOfflineReq(null);
  }

  const composerEl = (
    <Composer
      agents={agents}
      backends={backends}
      projects={projects}
      draft={draft}
      setDraft={setDraft}
      attachments={attachments}
      setAttachments={setAttachments}
      spellcheck={settings.spellcheck}
      streaming={streaming}
      backend={curBackend}
      model={curModel}
      effort={curEffort}
      temperature={curTemp}
      maxTokens={curMaxTokens}
      role={active ? active.agent : selRole}
      project={curProject}
      locked={!!active}
      engineLocked={engineLocked || !!activeGroup}
      groupMembers={activeGroup ? groupMembers : undefined}
      onEditRole={() => activeAgent && setAgentModal({ agent: activeAgent })}
      onPickEngine={pickEngine}
      onPickEffort={pickEffort}
      onPickTemp={pickTemp}
      onPickMaxTokens={pickMaxTokens}
      onPickRole={pickRole}
      onPickProject={pickProject}
      onNewProject={newProjectFlow}
      onNewRole={() => setAgentModal({ agent: null })}
      onAddModel={addModelFlow}
      onSend={onComposerSend}
      onStop={stopGenerating}
    />
  );

  // First-run setup splash: fresh installs ship with no @roles and the wizard
  // not yet completed. Existing installs (a team already exists) skip it.
  if (booted && !offline && !settings.onboarded && agents.length === 0) {
    return (
      <SetupView
        onDone={() => {
          setSettings((s) => ({ ...s, onboarded: true }));
          refresh();
          newChat();
        }}
      />
    );
  }

  return (
    <div className="app">
      {/* ── single sidebar (collapses to icons) ── */}
      <aside className={`sidebar ${railOpen ? "open" : "collapsed"}`}>
        <div className="sb-top">
          <button className="sb-brand" onClick={() => newChat()} title="OrkesAI">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className="sb-logo" src="/orkesai-icon.png" alt="OrkesAI" width={22} height={22} />
            {railOpen && <span>OrkesAI</span>}
          </button>
          <button className="sb-collapse" title={railOpen ? "Collapse" : "Expand"} onClick={() => setRailOpen((v) => !v)}>
            <Icon name="sidebar" size={railOpen ? 17 : 22} />
          </button>
        </div>

        <button className="newchat" onClick={() => newChat()} title="New session">
          <Icon name="plus" size={16} />
          {railOpen && <span>New session</span>}
        </button>

        <div className="sb-nav">
          <button className={`nav-item ${view === "projects" ? "active" : ""}`} onClick={() => setView("projects")} title="Projects">
            <Icon name="folder" size={18} />
            {railOpen && <span>Projects</span>}
          </button>
        </div>

        {railOpen && (
          <div className="side-scroll">
            <div className="section-label">
              Automations
              {/* one flow: "+ add" lands on the Automations page (import or
                  new), the editor opens from there */}
              <button className="mini" title="Automations" onClick={() => { setAutoFocus(null); setView("automations"); }}>
                <Icon name="plus" size={13} /> add
              </button>
            </div>
            {automations.map((a) => (
              <div key={a.id} className="team-item">
                <button className="team-open" title={a.prompt} onClick={() => { setAutoFocus(a.id); setView("automations"); }}>
                  <span className="role-emoji emoji-glyph">{a.icon || "⚡"}</span>
                  <span className="team-name">{a.name}</span>
                  <span className="team-model">
                    {a.running ? "running…"
                      : !a.enabled ? "off"
                      : a.trigger.type === "interval" ? `${a.trigger.every_minutes}m`
                      : a.trigger.type === "daily" ? a.trigger.at
                      : a.trigger.type}
                  </span>
                </button>
                <button className="team-edit" title="Run now"
                  onClick={async () => { await runAutomation(a.id).catch(() => {}); refresh(); }}>
                  <Icon name="play" size={13} />
                </button>
              </div>
            ))}
            {automations.length === 0 && (
              <button className="team-item team-open dim-add" onClick={() => { setAutoFocus(null); setView("automations"); }}>
                <Icon name="zap" size={14} /> <span className="team-name">Add an automation</span>
              </button>
            )}

            <div className="section-label">
              Groups
              <button className="mini" title="New group" onClick={() => setGroupModal({ group: null })}>
                <Icon name="plus" size={13} /> add
              </button>
            </div>
            {groups.map((g) => (
              <div key={g.id} className="team-item">
                <button className="team-open" title={g.participants.map((p) => "@" + p).join(" ")} onClick={() => openSession(g.session)}>
                  <GroupAvatars group={g} agents={agents} size={20} />
                  <span className="team-name">{g.name}</span>
                </button>
                <button className="team-edit" title={`Edit ${g.name}`} onClick={() => setGroupModal({ group: g })}>
                  <Icon name="edit" size={14} />
                </button>
              </div>
            ))}
            {groups.length === 0 && (
              <button className="team-item team-open dim-add" onClick={() => setGroupModal({ group: null })}>
                <Icon name="plus" size={14} /> <span className="team-name">Add new group</span>
              </button>
            )}

            <div className="section-label">Chats</div>
            <ChatList
              agents={agents}
              sessions={sessions.filter((s) => !s.group)}
              activeId={active?.id ?? null}
              onOpen={openSession}
              onDelete={removeSession}
              onNew={(r) => newChat(r)}
            />

            <div className="section-label">
              Team
              <button className="mini" title="Add a team agent" onClick={() => setAgentModal({ agent: null })}>
                <Icon name="plus" size={13} /> add
              </button>
            </div>
            {agents.map((a) => (
              <div key={a.id} className="team-item">
                <button className="team-open" title={`New chat with @${a.id}`} onClick={() => newChat(a.id)}>
                  <span className="role-emoji emoji-glyph">{a.icon}</span>
                  <span className="team-name">{a.name}</span>
                  <span className="team-model">{a.model.split("/").pop()}</span>
                </button>
                <button className="team-edit" title={`Edit ${a.name}`} onClick={() => setAgentModal({ agent: a })}>
                  <Icon name="edit" size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        {!railOpen && (
          <div className="side-mini">
            <ChatList
              agents={agents}
              sessions={sessions.filter((s) => !s.group)}
              activeId={active?.id ?? null}
              onOpen={openSession}
              onDelete={removeSession}
              onNew={(r) => newChat(r)}
              collapsed
              onExpand={() => setRailOpen(true)}
            />
          </div>
        )}

        <div className="sb-foot">
          <button className="nav-item" onClick={() => setShowSettings(true)} title="Settings">
            <Icon name="settings" size={18} />
            {railOpen && <span>Settings</span>}
          </button>
          <span className={`status ${offline ? "bad" : ""}`} title={offline ? "server offline — run ais" : "connected"}>
            <span className="dot" />
            {railOpen && (offline ? "offline" : "connected")}
          </span>
        </div>
      </aside>

      {/* ── main ── */}
      <main className="main">
        {view === "automations" ? (
          <AutomationsView
            automations={automations}
            agents={agents}
            openId={autoFocus}
            onOpen={setAutoFocus}
            onChanged={refresh}
            onOpenSession={openSession}
          />
        ) : view === "projects" ? (
          <ProjectsView
            projects={projects}
            sessions={sessions}
            agents={agents}
            backends={backends}
            settings={settings}
            onRefresh={refresh}
            onOpenChat={openSession}
            onStartChat={startChatInProject}
            onNewRole={() => setAgentModal({ agent: null })}
            askText={askText}
          />
        ) : (
          <div className="chat-wrap">
           <div className="chat-col">
            {active && (
              <div className="topbar">
                <span className="agent-pill">
                  {activeGroup ? (
                    <GroupAvatars group={activeGroup} agents={agents} size={18} />
                  ) : activeAgent ? (
                    <span className="role-emoji">{activeAgent.icon}</span>
                  ) : (
                    <Icon name="spark" size={14} fill />
                  )}{" "}
                  {chatName}
                </span>
                <span className="top-title">{active.title}</span>
                <span className="top-right">
                  <span className="usage">
                    {active.usage.in.toLocaleString()} tokens sent · {active.usage.out.toLocaleString()} received
                  </span>
                  <button
                    className={`top-del ${filesOpen ? "on" : ""}`}
                    title={filesOpen ? "Hide context panel" : "Show context panel (files, notes, links)"}
                    onClick={() => setFilesOpen((v) => !v)}
                  >
                    <Icon name="panelRight" size={17} />
                  </button>
                  {!activeGroup && (
                    <button className="top-del" title="Delete chat" onClick={() => removeSession(active.id)}>
                      <Icon name="trash" size={17} />
                    </button>
                  )}
                </span>
              </div>
            )}

            <div className="chat-scroll" ref={chatRef}>
              {!active ? (
                <div className="welcome">
                  <div className="welcome-icon">
                    {chatIcon ? (
                      <span className="role-emoji big">{chatIcon}</span>
                    ) : (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src="/orkesai-icon.png" alt="OrkesAI" width={56} height={56} style={{ borderRadius: 14 }} />
                    )}
                  </div>
                  <h1>Chat with {chatName}</h1>
                  <p className="welcome-sub">
                    {curBackend} · {curModel}
                    {selProject ? ` · in ${projName(selProject)}` : ""}
                  </p>
                  {/* fresh start: the message box sits center-stage */}
                  <div className="welcome-composer">{composerEl}</div>
                  {offline && (
                    <div className="offline-note">
                      The OrkesAI server isn’t running. Open a terminal, type <code>ais</code>, then come back.
                    </div>
                  )}
                </div>
              ) : (
                <div className="thread">
                  {messages.map((m, i) =>
                    m.role === "divider" ? (
                      <div className="handoff" key={i}>
                        <span className="handoff-label">
                          {m.kind === "engine" ? (
                            <>
                              {agentOf(m.who ?? "")?.name ?? m.who} <b className="mention">@{m.who}</b>
                              {" changed to · "}
                              {m.backend || "openrouter"} · {(m.model || "").split("/").pop()}
                            </>
                          ) : m.reason === "offline" && m.to === "local" ? (
                            <>session with local · {m.model ? m.model.split("/").pop() : "local model"}</>
                          ) : m.to === "paused" ? (
                            <>paused — waiting for connection</>
                          ) : (
                            <>
                              <Icon name="bot" size={13} /> handed off to @{m.to}
                              {m.backend ? ` · ${m.backend}` : ""}
                              {m.model ? ` · ${m.model.split("/").pop()}` : ""}
                            </>
                          )}
                        </span>
                      </div>
                    ) : m.role === "user" ? (
                      <div className="turn user" key={i}>
                        {m.images?.map((u, j) => (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img className="msg-img" src={u} alt="attachment" key={j} />
                        ))}
                        {m.attachments?.map((a, j) => (
                          <span className="file-chip" key={"f" + j}>
                            <Icon name="file" size={13} /> {a.name}
                          </span>
                        ))}
                        {editIdx === i ? (
                          <div className="bubble editing">
                            <textarea
                              autoFocus
                              rows={2}
                              value={editText}
                              onChange={(e) => setEditText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveEdit(i); }
                                if (e.key === "Escape") cancelEdit();
                              }}
                            />
                            <div className="bubble-edit-actions">
                              <button className="btn" onClick={cancelEdit}>Cancel</button>
                              <button className="btn primary" onClick={() => saveEdit(i)} disabled={!editText.trim()}>
                                <Icon name="send2" size={13} /> Resend
                              </button>
                            </div>
                          </div>
                        ) : (
                          (m.text ?? m.content) && (
                            <div className="bubble">
                              {activeGroup
                                ? <MentionText text={m.text ?? m.content ?? ""} ids={activeGroup.participants} />
                                : (m.text ?? m.content)}
                              {!streaming && (
                                <button className="bubble-edit" title="Edit & resend" onClick={() => startEdit(i)}>
                                  <Icon name="edit" size={13} />
                                </button>
                              )}
                            </div>
                          )
                        )}
                      </div>
                    ) : (
                      <div className={`turn assistant ${activeAgent || m.agent ? "" : "plain"}`} key={i}>
                        {(m.agent || activeAgent) && (
                          <span className="avatar" title={m.agent ? `@${m.agent}` : undefined}>
                            <span className="role-emoji emoji-glyph">
                              {m.agent ? agentOf(m.agent)?.icon ?? "🤖" : activeAgent!.icon}
                            </span>
                          </span>
                        )}
                        <div className="answer">
                          {m.agent && (
                            <span className="group-msg-name">
                              {agentOf(m.agent)?.name ?? m.agent} <span className="mention">@{m.agent}</span>
                              {agentOf(m.agent) && (
                                <span className="group-msg-model" title={`@${m.agent}'s own engine`}>
                                  {(agentOf(m.agent)!.backend ?? "openrouter").replace(/^api:.*/, "api")}
                                  {" · "}
                                  {(agentOf(m.agent)!.model || "").split("/").pop()}
                                </span>
                              )}
                            </span>
                          )}
                          {m.content
                            ? splitParts(m.content).map((p, j) =>
                                p.type === "act" ? (
                                  <span className="act-chip" key={j}>
                                    <Icon name="terminal" size={12} /> {p.text}
                                  </span>
                                ) : (
                                  <div className="md" key={j}>
                                    <ReactMarkdown
                                      remarkPlugins={[remarkGfm]}
                                      rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                                      components={{ pre: CodeBlock }}
                                    >
                                      {p.text}
                                    </ReactMarkdown>
                                  </div>
                                ),
                              )
                            : null}
                          {m.images?.map((u, j) => (
                            <span className="gen-img-wrap" key={"g" + j}>
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img className="gen-img" src={u} alt="generated" />
                              <button className="gen-dl" title="Download image" onClick={() => downloadImage(u, `orkesai-image-${Date.now()}`)}>
                                <Icon name="download" size={15} /> Download
                              </button>
                            </span>
                          ))}
                          {!m.content && !m.images?.length && (
                            <span className="thinking">thinking<span className="dots">…</span></span>
                          )}
                        </div>
                      </div>
                    ),
                  )}

                  {confirmReq && (
                    <div className="confirm-card">
                      <div className="confirm-head">The agent asks for your permission</div>
                      <div className="confirm-action">{confirmReq.action}</div>
                      {confirmReq.detail && <pre className="confirm-detail">{confirmReq.detail}</pre>}
                      <div className="confirm-btns">
                        <button className="btn allow" onClick={() => answer(true)}>
                          <Icon name="check" size={15} /> Allow
                        </button>
                        <button className="btn deny" onClick={() => answer(false)}>
                          <Icon name="close" size={15} /> Deny
                        </button>
                      </div>
                    </div>
                  )}

                  {offlineReq && (
                    <div className="confirm-card offline">
                      <div className="confirm-head">
                        <span className="offline-badge">offline</span>
                        Connection refused — you seem to have no internet
                      </div>
                      <div className="confirm-action">
                        Run this on your device with the local model
                        {" "}<b>{localModelName}</b> (private, no internet needed, slower and less
                        capable than the cloud), or wait until you’re back online?
                      </div>
                      <div className="confirm-btns">
                        <button className="btn allow" onClick={() => answerOffline(true)}>
                          <Icon name="check" size={15} /> Continue with {localModelName}
                        </button>
                        <button className="btn deny" onClick={() => answerOffline(false)}>
                          <Icon name="close" size={15} /> Wait until online
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {active && composerEl}
            <div className="statusbar">
              <span>
                {activeGroup
                  ? `${chatName} · ${groupMembers.length} @roles — each replies with its own model`
                  : `${chatName} · ${curBackend} · ${curModel.split("/").pop()}`}
              </span>
              <span className="right">
                {curProject && (
                  <>
                    <Icon name="folder" size={12} /> {projName(curProject)} ·{" "}
                  </>
                )}
                {settings.edit === "on" ? "asks before acting" : settings.edit === "auto" ? "acts freely" : "chat only"}
              </span>
            </div>
           </div>
           {filesOpen && active && (
             <FilesPanel
               agent={active.agent}
               session={active.id}
               agentLabel={activeGroup ? "this group" : activeAgent ? `@${active.agent} chats` : "this chat"}
               reloadKey={filesVer}
               onOpenSession={openSession}
             />
           )}
          </div>
        )}
      </main>

      {showSettings && <SettingsModal agents={agents} backends={backends} onClose={() => setShowSettings(false)} onSaved={setSettings} />}
      {agentModal && <AgentModal agent={agentModal.agent} onClose={() => setAgentModal(null)} onChanged={refresh} />}
      {groupModal && (
        <GroupModal
          group={groupModal.group}
          agents={agents}
          onClose={() => setGroupModal(null)}
          onChanged={refresh}
          onOpen={openSession}
        />
      )}
      {ask && <AskModal spec={ask} />}
    </div>
  );
}
