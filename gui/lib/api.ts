// API client for the Local-AI Python server (server/server.py on :8765)

export const API = process.env.NEXT_PUBLIC_AI_SERVER ?? "http://127.0.0.1:8765";

export interface Agent {
  id: string;
  name: string;
  icon: string;
  model: string;
  system: string;
}

export interface SessionMeta {
  id: string;
  agent: string;
  model: string;
  title: string;
  created: number;
  updated: number;
  usage: { in: number; out: number };
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  ts?: number;
}

export interface Session extends SessionMeta {
  messages: Message[];
}

export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "done"; usage: { in: number; out: number }; title: string; cost: number }
  | { type: "error"; message: string };

export async function getAgents(): Promise<Agent[]> {
  const r = await fetch(`${API}/api/agents`);
  return (await r.json()).agents;
}

export async function getSessions(): Promise<SessionMeta[]> {
  const r = await fetch(`${API}/api/sessions`);
  return (await r.json()).sessions;
}

export async function getSession(id: string): Promise<Session> {
  const r = await fetch(`${API}/api/sessions/${id}`);
  return r.json();
}

export async function createSession(agent: string): Promise<Session> {
  const r = await fetch(`${API}/api/sessions`, {
    method: "POST",
    body: JSON.stringify({ agent }),
  });
  return r.json();
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${API}/api/sessions/${id}`, { method: "DELETE" });
}

/** POST /api/chat and invoke onEvent for every SSE event until the stream closes. */
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (ev: StreamEvent) => void,
): Promise<void> {
  const r = await fetch(`${API}/api/chat`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!r.ok || !r.body) {
    onEvent({ type: "error", message: `server error ${r.status}` });
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
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
}
