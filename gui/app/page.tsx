"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Agent,
  Message,
  Session,
  SessionMeta,
  createSession,
  deleteSession,
  getAgents,
  getSession,
  getSessions,
  streamChat,
} from "@/lib/api";

export default function Home() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [active, setActive] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [lastCost, setLastCost] = useState(0);
  const [offline, setOffline] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([getAgents(), getSessions()]);
      setAgents(a);
      setSessions(s);
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight });
  }, [messages]);

  const agentOf = (id: string) => agents.find((a) => a.id === id);

  async function openSession(id: string) {
    const s = await getSession(id);
    setActive(s);
    setMessages(s.messages);
    setOpenTabs((tabs) => (tabs.includes(id) ? tabs : [...tabs, id]));
  }

  async function newSession(agentId: string) {
    const s = await createSession(agentId);
    setActive({ ...s });
    setMessages([]);
    setOpenTabs((tabs) => [...tabs, s.id]);
    refresh();
  }

  async function removeSession(id: string) {
    await deleteSession(id);
    setOpenTabs((tabs) => tabs.filter((t) => t !== id));
    if (active?.id === id) {
      setActive(null);
      setMessages([]);
    }
    refresh();
  }

  async function send() {
    const text = draft.trim();
    if (!text || !active || streaming) return;
    setDraft("");
    setStreaming(true);
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);

    let acc = "";
    await streamChat(active.id, text, (ev) => {
      if (ev.type === "token") {
        acc += ev.text;
        const snapshot = acc;
        setMessages((m) => [...m.slice(0, -1), { role: "assistant", content: snapshot }]);
      } else if (ev.type === "done") {
        setActive((s) => (s ? { ...s, usage: ev.usage, title: ev.title } : s));
        setLastCost(ev.cost);
        refresh();
      } else if (ev.type === "error") {
        setMessages((m) => [
          ...m.slice(0, -1),
          { role: "assistant", content: `**[error]** ${ev.message}` },
        ]);
      }
    });
    setStreaming(false);
  }

  const activeAgent = active ? agentOf(active.agent) : undefined;
  const tabMeta = (id: string) =>
    sessions.find((s) => s.id === id) ?? (active?.id === id ? active : undefined);

  return (
    <div className="frame">
      {/* ── sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span className="mark">⚡</span> DotAI
        </div>
        <div className="sidebar-section">🗂 Workspaces</div>
        <div className="sidebar-scroll">
          {agents.map((agent) => (
            <div className="agent-group" key={agent.id}>
              <button className="agent-head" onClick={() => newSession(agent.id)}>
                <span>{agent.icon}</span> {agent.name}
                <span className="model-tag">{agent.model.split("/").pop()}</span>
              </button>
              {sessions
                .filter((s) => s.agent === agent.id)
                .map((s) => (
                  <button
                    key={s.id}
                    className={`session-item ${active?.id === s.id ? "active" : ""}`}
                    onClick={() => openSession(s.id)}
                    title={s.title}
                  >
                    {s.title}
                  </button>
                ))}
              <button className="session-item session-new" onClick={() => newSession(agent.id)}>
                + New session
              </button>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          {offline ? (
            <span style={{ color: "var(--red)" }}>● server offline — run `ais`</span>
          ) : (
            <span>
              <span className="dot" /> server connected
            </span>
          )}
        </div>
      </aside>

      {/* ── main ── */}
      <main className="main">
        <div className="topbar">
          <span className="crumb">Dashboard</span>
          {activeAgent && (
            <span className="pill">
              <span className="dot" />
              {activeAgent.name} · {active?.model}
            </span>
          )}
          <div className="stats">
            {active && (
              <span>
                ↓{(active.usage.in / 1000).toFixed(1)}k ↑{(active.usage.out / 1000).toFixed(1)}k
              </span>
            )}
            {lastCost > 0 && <span>${lastCost.toFixed(5)}</span>}
            <span>v0.1.0</span>
          </div>
        </div>

        {openTabs.length > 0 && (
          <div className="tabs">
            {openTabs.map((id) => {
              const meta = tabMeta(id);
              return (
                <button
                  key={id}
                  className={`tab ${active?.id === id ? "active" : ""}`}
                  onClick={() => openSession(id)}
                >
                  <span className="dot" />
                  {meta?.title ?? id}
                </button>
              );
            })}
          </div>
        )}

        {active ? (
          <>
            <div className="session-head">
              <h1>{active.title}</h1>
              <span className="sub">
                {active.model} · {activeAgent?.name}
              </span>
              <span className="spacer" />
              <button className="danger" onClick={() => removeSession(active.id)}>
                🗑 Delete
              </button>
            </div>

            <div className="chat" ref={chatRef}>
              {messages.map((m, i) => (
                <div className={`msg ${m.role}`} key={i}>
                  {m.content ? (
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                  ) : (
                    <span className="thinking">thinking…</span>
                  )}
                </div>
              ))}
            </div>

            <div className="composer">
              <div className="composer-box">
                <textarea
                  rows={2}
                  placeholder="Type a message…"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <button className="send" disabled={streaming || !draft.trim()} onClick={send}>
                  ➤
                </button>
              </div>
            </div>
            <div className="statusline">
              <span>
                {active.model} · OpenRouter{streaming ? " · streaming…" : ""}
              </span>
              <span className="right">{activeAgent?.icon} {activeAgent?.name} agent</span>
            </div>
          </>
        ) : (
          <div className="empty">
            <div className="inner">
              <div className="big">⚡</div>
              <p>Pick an agent on the left or start a new session.</p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
