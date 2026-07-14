// API client for the OrkesAI Python server (server/server.py on :8765)

export const API = process.env.NEXT_PUBLIC_AI_SERVER ?? "http://127.0.0.1:8765";

export const DEFAULT_AGENT = "default";

export interface Agent {
  id: string;
  name: string;
  icon: string;
  model: string;
  system: string;
  backend?: string; // openrouter | claude | codex | local | api:<integration>
  effort?: string; // "" | low | medium | high — canonical for all the role's sessions
  temperature?: string; // "" | precise | balanced | creative
  max_tokens?: number;
}

export interface EnvItem {
  key: string;
  label: string;
  secret: boolean;
  help: string;
  set: boolean;
  value: string; // masked for secrets
}

export interface Backend {
  id: string; // openrouter | claude | codex | local
  label: string;
  available: boolean;
  models: string[];
}

export interface Attachment {
  name: string;
  type: string;
  url?: string; // data URL — present on send, dropped in stored history
}

export interface SessionMeta {
  id: string;
  agent: string; // "default" or a team role id
  model: string;
  backend: string;
  effort: string; // "" | low | medium | high
  temperature: string; // "" | precise | balanced | creative
  max_tokens: number;
  title: string;
  project: string;
  group?: string; // set when this session is a group conversation
  created: number;
  updated: number;
  usage: { in: number; out: number };
}

export interface Message {
  role: "user" | "assistant" | "divider";
  content?: string;
  text?: string; // clean user text (content may include folded-in file text)
  agent?: string; // group chat: which @role said this assistant message
  images?: string[];
  attachments?: Attachment[];
  ts?: number;
  // handoff divider (role === "divider")
  from?: string;
  to?: string;
  backend?: string;
  model?: string;
  reason?: string; // e.g. "offline"
  kind?: string; // "engine" = an @role's backend/model changed here
  who?: string; // the @role the engine divider is about
}

export interface Session extends SessionMeta {
  messages: Message[];
}

export interface Settings {
  agent: string; // terminal startup backend
  edit: string; // on|auto|off
  spellcheck: boolean;
  default_agent: string; // agent id used by "New chat"
  default_backend: string; // backend a new default chat starts with
  default_model: string; // model a new default chat starts with
  default_system: string; // instructions/system prompt for the default chat
  appearance: string; // dark|light|system
  full_disk: boolean;
  onboarded: boolean; // first-run setup wizard completed
  learning: boolean; // opt-in write-back learning (PROFILE.md, auto-skills, learnings)
}

export interface Project {
  name: string;
  path: string;
  description: string;
  instructions: string;
  files: string[];
  chats: number;
}

export interface UsageModel {
  model: string;
  req: number;
  in: number;
  out: number;
  cost: number;
}
export interface Usage {
  range: string;
  models: UsageModel[];
  total: { in: number; out: number; req: number; cost: number };
  series: { day: string; in: number; out: number; req: number; cost: number }[];
  balance: { total: number; used: number; left: number } | null;
}

export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "role"; id: string; name: string; icon: string } // group chat: next reply is by this @role
  | { type: "image"; url: string } // an image an image-output model generated
  | { type: "confirm"; id: string; tool: string; action: string; detail: string }
  | { type: "offline"; id: string } // cloud unreachable — choose local vs wait
  | { type: "reload" } // thread changed on the server; reload it from disk
  | { type: "done"; usage: { in: number; out: number }; title: string; cost: number }
  | { type: "error"; message: string };

async function j<T>(r: Response): Promise<T> {
  const data = await r.json();
  if (!r.ok) throw new Error((data && data.error) || `server error ${r.status}`);
  return data as T;
}

// ── full OpenRouter catalog (for model autocomplete) ─────────────────────────
export interface CatalogModel { id: string; name: string }
export const getOpenrouterCatalog = async (): Promise<{ count: number; models: CatalogModel[] }> =>
  fetch(`${API}/api/models/openrouter`).then(j<{ count: number; models: CatalogModel[] }>);

// ── first-run setup wizard ────────────────────────────────────────────────────
export interface TeamTemplate { id: string; label: string; persona: string; roles: { name: string; icon: string }[] }
export const getTeamTemplates = async (): Promise<TeamTemplate[]> =>
  (await j<{ templates: TeamTemplate[] }>(await fetch(`${API}/api/team/templates`))).templates;
export const applyTeamTemplate = (id: string): Promise<{ created: Agent[]; agents: Agent[] }> =>
  fetch(`${API}/api/team/template`, { method: "POST", body: JSON.stringify({ id }) }).then(j<{ created: Agent[]; agents: Agent[] }>);
export interface CliStatus {
  claude: boolean; codex: boolean; npm: boolean; brew: boolean;
  installing: string[]; errors: Record<string, string>;
}
export const getCliStatus = (): Promise<CliStatus> =>
  fetch(`${API}/api/setup/clis`).then(j<CliStatus>);
export const installClis = (clis: string[]): Promise<CliStatus> =>
  fetch(`${API}/api/setup/install`, { method: "POST", body: JSON.stringify({ clis }) }).then(j<CliStatus>);

// ── custom API integrations (Settings → Integrations) ────────────────────────
export interface Integration {
  id: string; name: string; base_url: string;
  auth: "bearer" | "plain"; header: string; api_key: string; // masked on read
}
export const getIntegrations = async (): Promise<Integration[]> =>
  (await j<{ integrations: Integration[] }>(await fetch(`${API}/api/integrations`))).integrations;
export const addIntegration = (data: Partial<Integration>): Promise<Integration> =>
  fetch(`${API}/api/integrations`, { method: "POST", body: JSON.stringify(data) }).then(j<Integration>);
export const deleteIntegration = (id: string): Promise<void> =>
  fetch(`${API}/api/integrations/${id}`, { method: "DELETE" }).then(j).then(() => undefined);

// ── groups (one chat, several @role participants) ─────────────────────────────
export interface Group {
  id: string; name: string; icon: string;
  participants: string[]; // team @role ids
  session: string; // the group's single conversation
  created: number;
}
export const getGroups = async (): Promise<Group[]> =>
  (await j<{ groups: Group[] }>(await fetch(`${API}/api/groups`))).groups;
export const createGroup = (data: Partial<Group>): Promise<Group> =>
  fetch(`${API}/api/groups`, { method: "POST", body: JSON.stringify(data) }).then(j<Group>);
export const updateGroup = (id: string, data: Partial<Group>): Promise<Group> =>
  fetch(`${API}/api/groups/${id}`, { method: "PUT", body: JSON.stringify(data) }).then(j<Group>);
export const deleteGroup = (id: string): Promise<void> =>
  fetch(`${API}/api/groups/${id}`, { method: "DELETE" }).then(j).then(() => undefined);

// ── automations (trigger → prompt → actions) ──────────────────────────────────
export interface AutomationTrigger { type: "manual" | "interval" | "daily" | "webhook"; every_minutes?: number; at?: string }
export interface AutomationRun { ts: number; status: string; summary: string }
export interface Automation {
  id: string; name: string; icon: string; enabled: boolean;
  trigger: AutomationTrigger; prompt: string; agent: string;
  backend: string; model: string;
  actions: { webhook_url: string; save_note: boolean };
  session: string; created: number; updated: number;
  last_run: AutomationRun | null; runs?: AutomationRun[]; running?: boolean;
}
export const getAutomations = async (): Promise<Automation[]> =>
  (await j<{ automations: Automation[] }>(await fetch(`${API}/api/automations`))).automations;
export const getAutomation = (id: string): Promise<Automation> =>
  fetch(`${API}/api/automations/${id}`).then(j<Automation>);
export const createAutomation = (data: Partial<Automation>): Promise<Automation> =>
  fetch(`${API}/api/automations`, { method: "POST", body: JSON.stringify(data) }).then(j<Automation>);
export const updateAutomation = (id: string, data: Partial<Automation>): Promise<Automation> =>
  fetch(`${API}/api/automations/${id}`, { method: "PUT", body: JSON.stringify(data) }).then(j<Automation>);
export const deleteAutomation = (id: string): Promise<void> =>
  fetch(`${API}/api/automations/${id}`, { method: "DELETE" }).then(j).then(() => undefined);
export const runAutomation = (id: string): Promise<void> =>
  fetch(`${API}/api/automations/${id}/run`, { method: "POST" }).then(j).then(() => undefined);
export const exportAutomation = (id: string): Promise<object> =>
  fetch(`${API}/api/automations/${id}/export`).then(j<object>);
export const importAutomation = (data: object): Promise<Automation> =>
  fetch(`${API}/api/automations/import`, { method: "POST", body: JSON.stringify(data) }).then(j<Automation>);

// ── agents ──────────────────────────────────────────────────────────────────
export const getAgents = async (): Promise<Agent[]> =>
  (await j<{ agents: Agent[] }>(await fetch(`${API}/api/agents`))).agents;

export const createAgent = (data: Partial<Agent>): Promise<Agent> =>
  fetch(`${API}/api/agents`, { method: "POST", body: JSON.stringify(data) }).then(j<Agent>);

export const updateAgent = (id: string, data: Partial<Agent>): Promise<Agent> =>
  fetch(`${API}/api/agents/${id}`, { method: "PUT", body: JSON.stringify(data) }).then(j<Agent>);

export const deleteAgent = (id: string): Promise<void> =>
  fetch(`${API}/api/agents/${id}`, { method: "DELETE" }).then(j).then(() => undefined);

export const enhancePrompt = async (text: string, name: string, model: string): Promise<string> =>
  (await j<{ text: string }>(
    await fetch(`${API}/api/enhance-prompt`, { method: "POST", body: JSON.stringify({ text, name, model }) }),
  )).text;

// ── backends / models ───────────────────────────────────────────────────────
export const getBackends = async (): Promise<Backend[]> =>
  (await j<{ backends: Backend[] }>(await fetch(`${API}/api/backends`))).backends;

export const addModel = async (backend: string, model: string): Promise<Backend[]> =>
  (await j<{ backends: Backend[] }>(
    await fetch(`${API}/api/backends`, { method: "POST", body: JSON.stringify({ backend, model }) }),
  )).backends;

// ── settings ────────────────────────────────────────────────────────────────
export const getSettings = (): Promise<{ settings: Settings; valid_agents: string[]; valid_edit: string[] }> =>
  fetch(`${API}/api/settings`).then(j<{ settings: Settings; valid_agents: string[]; valid_edit: string[] }>);

export const saveSettings = (data: Partial<Settings>): Promise<void> =>
  fetch(`${API}/api/settings`, { method: "PUT", body: JSON.stringify(data) }).then(j).then(() => undefined);

// ── .env config ─────────────────────────────────────────────────────────────
export const getEnv = async (): Promise<EnvItem[]> =>
  (await j<{ env: EnvItem[] }>(await fetch(`${API}/api/env`))).env;

export const saveEnv = async (patch: Record<string, string>): Promise<EnvItem[]> =>
  (await j<{ env: EnvItem[] }>(
    await fetch(`${API}/api/env`, { method: "PUT", body: JSON.stringify(patch) }),
  )).env;

// ── projects ────────────────────────────────────────────────────────────────
export const getProjects = async (): Promise<Project[]> =>
  (await j<{ projects: Project[] }>(await fetch(`${API}/api/projects`))).projects;

export const createProject = (name: string, description = ""): Promise<Project> =>
  fetch(`${API}/api/projects`, { method: "POST", body: JSON.stringify({ name, description }) }).then(j<Project>);

export const updateProject = (name: string, data: Partial<Project>): Promise<Project> =>
  fetch(`${API}/api/projects/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(data) }).then(j<Project>);

export const deleteProject = (name: string): Promise<void> =>
  fetch(`${API}/api/projects/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j).then(() => undefined);

export const addProjectFile = (name: string, file: Attachment): Promise<Project> =>
  fetch(`${API}/api/projects/${encodeURIComponent(name)}/files`, {
    method: "POST",
    body: JSON.stringify({ name: file.name, url: file.url }),
  }).then(j<Project>);

export const deleteProjectFile = (name: string, filename: string): Promise<Project> =>
  fetch(`${API}/api/projects/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  }).then(j<Project>);

// ── usage / skills / mcp / databases ────────────────────────────────────────
export const getUsage = (range = "all"): Promise<Usage> =>
  fetch(`${API}/api/usage?range=${range}`).then(j<Usage>);

export const getSkills = async (): Promise<{ name: string; category: string; ok?: boolean; detail?: string }[]> =>
  (await j<{ skills: { name: string; category: string; ok?: boolean; detail?: string }[] }>(await fetch(`${API}/api/skills`))).skills;

export const addSkill = (name: string, source: string): Promise<void> =>
  fetch(`${API}/api/skills`, { method: "POST", body: JSON.stringify({ name, source }) }).then(j).then(() => undefined);

export const removeSkill = (name: string): Promise<void> =>
  fetch(`${API}/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j).then(() => undefined);

export const getMcp = async (): Promise<{ name: string; command: string; ok?: boolean; detail?: string }[]> =>
  (await j<{ servers: { name: string; command: string; ok?: boolean; detail?: string }[] }>(await fetch(`${API}/api/mcp`))).servers;

export const addMcp = (name: string, command: string): Promise<void> =>
  fetch(`${API}/api/mcp`, { method: "POST", body: JSON.stringify({ name, command }) }).then(j).then(() => undefined);

export const removeMcp = (name: string): Promise<void> =>
  fetch(`${API}/api/mcp/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j).then(() => undefined);

export const getDatabases = async (): Promise<
  { name: string; size: number; tables: { table: string; rows: number }[]; kind?: string }[]
> => (await j<{ databases: never[] }>(await fetch(`${API}/api/databases`))).databases;

export const deleteDatabase = (name: string): Promise<void> =>
  fetch(`${API}/api/databases/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j).then(() => undefined);

// ── sessions ────────────────────────────────────────────────────────────────
export const getSessions = async (): Promise<SessionMeta[]> =>
  (await j<{ sessions: SessionMeta[] }>(await fetch(`${API}/api/sessions`))).sessions;

export const getSession = (id: string): Promise<Session> => fetch(`${API}/api/sessions/${id}`).then(j<Session>);

export interface AgentFile {
  kind: "image" | "file";
  name: string;
  type: string;
  url?: string;
  session: string;
  title: string;
  ts: number;
  generated: boolean;
}
export const getAgentFiles = async (agent: string): Promise<AgentFile[]> =>
  (await j<{ files: AgentFile[] }>(await fetch(`${API}/api/agent-files/${encodeURIComponent(agent)}`))).files;

// ── conversation context: files + notes + links (right panel) ────────────────
export interface Note { id: string; title: string; body: string; source: string; created: number; updated: number; }
export interface CtxLink { id?: string; url: string; title: string; source: string; session?: string; }
export interface ContextData {
  scope: string;
  is_role: boolean;
  files: AgentFile[];
  notes: Note[];
  links: CtxLink[];
  ai_auto: boolean;
}
export const getContext = (agent: string, session: string): Promise<ContextData> =>
  fetch(`${API}/api/context?agent=${encodeURIComponent(agent)}&session=${encodeURIComponent(session)}`).then(j<ContextData>);

export const createNote = (scope: string, title: string, body: string): Promise<Note> =>
  fetch(`${API}/api/notes`, { method: "POST", body: JSON.stringify({ scope, title, body }) }).then(j<Note>);
export const updateNote = (scope: string, id: string, data: { title?: string; body?: string }): Promise<Note> =>
  fetch(`${API}/api/notes/${id}`, { method: "PUT", body: JSON.stringify({ scope, ...data }) }).then(j<Note>);
export const deleteNote = (scope: string, id: string): Promise<void> =>
  fetch(`${API}/api/notes/${id}?scope=${encodeURIComponent(scope)}`, { method: "DELETE" }).then(j).then(() => undefined);
export const setNotesAuto = (scope: string, on: boolean): Promise<void> =>
  fetch(`${API}/api/notes-auto`, { method: "PUT", body: JSON.stringify({ scope, on }) }).then(j).then(() => undefined);
export const summarizeNote = (agent: string, session: string): Promise<Note> =>
  fetch(`${API}/api/notes/summarize`, { method: "POST", body: JSON.stringify({ agent, session }) }).then(j<Note>);
export const NOTE_EXPORT_FORMATS = ["pdf", "docx", "xlsx", "csv"] as const;
export type NoteExportFormat = (typeof NOTE_EXPORT_FORMATS)[number];
export const exportNotes = async (scope: string, fmt: NoteExportFormat, noteId = ""): Promise<void> => {
  const note = noteId ? `&note=${encodeURIComponent(noteId)}` : "";
  const r = await fetch(`${API}/api/notes/export?scope=${encodeURIComponent(scope)}&fmt=${fmt}${note}`);
  if (!r.ok) {
    const e = await r.json().catch(() => null) as { error?: string } | null;
    throw new Error(e?.error ?? `export failed (${r.status})`);
  }
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "")?.[1] ?? `notes.${fmt}`;
  const href = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
};
export const addLink = (scope: string, url: string, title: string): Promise<CtxLink> =>
  fetch(`${API}/api/links`, { method: "POST", body: JSON.stringify({ scope, url, title }) }).then(j<CtxLink>);
export const deleteLink = (scope: string, id: string): Promise<void> =>
  fetch(`${API}/api/links/${id}?scope=${encodeURIComponent(scope)}`, { method: "DELETE" }).then(j).then(() => undefined);

export const createSession = (
  agent: string,
  opts: { project?: string; backend?: string; model?: string; effort?: string; temperature?: string; max_tokens?: number } = {},
): Promise<Session> =>
  fetch(`${API}/api/sessions`, { method: "POST", body: JSON.stringify({ agent, ...opts }) }).then(j<Session>);

export const updateSession = (
  id: string,
  data: Partial<SessionMeta> & { truncate?: number },
): Promise<Session> =>
  fetch(`${API}/api/sessions/${id}`, { method: "PUT", body: JSON.stringify(data) }).then(j<Session>);

export const deleteSession = (id: string): Promise<void> =>
  fetch(`${API}/api/sessions/${id}`, { method: "DELETE" }).then(() => undefined);

export const answerConfirm = (id: string, approve: boolean): Promise<void> =>
  fetch(`${API}/api/confirm`, { method: "POST", body: JSON.stringify({ id, approve }) }).then(() => undefined);

/** POST /api/chat and invoke onEvent for every SSE event until the stream closes. */
export async function streamChat(
  sessionId: string,
  message: string,
  attachments: Attachment[],
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const images = attachments.filter((a) => a.type.startsWith("image/")).map((a) => a.url!);
  const files = attachments.filter((a) => !a.type.startsWith("image/"));
  let r: Response;
  try {
    r = await fetch(`${API}/api/chat`, {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, message, images, attachments: files }),
      signal,
    });
  } catch (e) {
    // aborted before the response arrived — treat as a clean stop
    if ((e as Error)?.name !== "AbortError") onEvent({ type: "error", message: "network error" });
    return;
  }
  if (!r.ok || !r.body) {
    onEvent({ type: "error", message: `server error ${r.status}` });
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (frame.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(frame.slice(6)) as StreamEvent);
          } catch {
            /* partial/garbled frame — ignore */
          }
        }
      }
    }
  } catch (e) {
    // user hit Stop → reader.read() rejects with AbortError; end quietly
    if ((e as Error)?.name !== "AbortError") onEvent({ type: "error", message: "stream interrupted" });
  }
}
