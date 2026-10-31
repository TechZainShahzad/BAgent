import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ChatMessage, type ConversationDetail, type ConversationRow } from "./api";
import type { Ctx } from "./data";
import {
  bubbleSide,
  clockTime,
  dayKey,
  dayLabel,
  groupChat,
  initials,
  pretty,
  splitHighlight,
  toTranscriptText,
  toneFor,
  usePolling,
} from "./lib";
import { Badge, Empty, Icon, Skeleton, StatusBadge, useToast } from "./ui";

/* ------------------------------------------------------------------ chat pane */
function Highlight({ text, query }: { text: string; query: string }) {
  return (
    <>
      {splitHighlight(text, query).map((p, i) =>
        p.hit ? (
          <mark key={i} className="hit">{p.text}</mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

export function ChatPane({ ctx, row }: { ctx: Ctx; row: ConversationRow }) {
  const toast = useToast();
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const lastIdx = useRef(-1);
  const live = row.status === "live" || row.status === "degraded";

  // load once per meeting, then poll only for what is new (fast while the call is live)
  useEffect(() => {
    setDetail(null);
    setMessages([]);
    setError("");
    setQuery("");
    setUnseen(0);
    lastIdx.current = -1;
    let stop = false;
    const pull = async () => {
      try {
        const d = await api.conversation(ctx.clientId, row.meeting_id, lastIdx.current);
        if (stop) return;
        setDetail(d);
        if (d.messages.length) {
          lastIdx.current = d.messages[d.messages.length - 1]?.idx ?? lastIdx.current;
          setMessages((prev) => [...prev, ...d.messages.filter((m) => !prev.some((p) => p.idx === m.idx))]);
        }
        setError("");
      } catch (e) {
        if (!stop) setError((e as Error).message);
      }
    };
    void pull();
    const t = window.setInterval(() => void pull(), live ? 2500 : 20000);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [ctx.clientId, row.meeting_id, live]);

  // keep the newest message in view unless the reader scrolled up
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (atBottom) el.scrollTop = el.scrollHeight;
    else setUnseen((u) => u + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length]);

  const groups = useMemo(() => groupChat(messages), [messages]);
  const matches = query.trim() ? messages.filter((m) => m.text.toLowerCase().includes(query.trim().toLowerCase())).length : 0;
  const started = detail?.meeting.started_at ?? row.started_at;
  const agentCount = messages.filter((m) => m.role === "agent").length;
  const lastMsg = messages[messages.length - 1];

  const download = () => {
    const blob = new Blob([toTranscriptText(row.title, started, messages)], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${row.title.replace(/[^a-z0-9]+/gi, "-")}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("good", "Conversation downloaded");
  };

  return (
    <section className="chat">
      <header className="chat-head">
        <div className="avatar big agent"><Icon name="spark" size={20} /></div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="chat-title ellipsis">{row.title}</div>
          <div className="chat-sub ellipsis">
            {new Date(started).toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" })} · {messages.length} messages
            {agentCount ? ` · ${agentCount} from the BA` : ""}
          </div>
        </div>
        <StatusBadge status={row.status} tone={toneFor("meeting", row.status)} />
        {searching ? (
          <div style={{ position: "relative", width: 220 }}>
            <input className="input" autoFocus placeholder="Search in chat..." value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Escape" && (setSearching(false), setQuery(""))} />
            {query && <span className="faint" style={{ position: "absolute", right: 10, top: 9, fontSize: 12 }}>{matches}</span>}
          </div>
        ) : (
          <button className="btn icon ghost" onClick={() => setSearching(true)} aria-label="search in chat"><Icon name="search" /></button>
        )}
        <button className="btn icon ghost" onClick={download} disabled={messages.length === 0} aria-label="download conversation"><Icon name="download" /></button>
      </header>

      <div
        className="chat-body"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          const near = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
          setAtBottom(near);
          if (near) setUnseen(0);
        }}
      >
        {!detail && !error && (
          <div className="stack" style={{ padding: 30 }}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={46} w={`${40 + i * 12}%`} />)}</div>
        )}
        {error && <div className="err" style={{ margin: 20 }}>{error}</div>}
        {detail && messages.length === 0 && live && (
          <div style={{ margin: "auto", maxWidth: 420 }}>
            <Empty icon="mic" title="Waiting for the first words..." hint="The agent greets within a few seconds of the speech model loading. Messages appear here as they are spoken." />
          </div>
        )}
        {detail && messages.length === 0 && !live && (
          <div style={{ margin: "auto", maxWidth: 420 }}>
            <Empty icon="chat" title="No stored conversation for this call" hint="It was recorded before conversations were kept, storage is switched off (CONVERSATION_RETENTION_DAYS=0), or it has expired." />
          </div>
        )}
        {groups.map((g) => (
          <div key={g.key}>
            <div className="day"><span>{g.label}</span></div>
            {g.items.map(({ msg, firstOfRun }) => {
              const side = bubbleSide(msg.role);
              const name = msg.role === "agent" ? "BA assistant" : (msg.speaker ?? (msg.role === "client" ? "Client" : "Participant"));
              return (
                <div key={msg.idx} className={`msg ${side}${firstOfRun ? " first" : ""}`}>
                  {side === "left" && (firstOfRun ? <div className={`avatar ${msg.role}`}>{initials(name)}</div> : <div className="avatar ghost" />)}
                  <div className="bubble" data-idx={msg.idx}>
                    {firstOfRun && <div className="who">{name}</div>}
                    <div className="text"><Highlight text={msg.text} query={query} /></div>
                    <div className="meta">
                      {msg.channel && msg.channel !== "agent" && <span className="chan">{msg.channel}</span>}
                      {clockTime(msg.said_at)}
                      {side === "right" && <span className="tick"><Icon name="check" size={13} /><Icon name="check" size={13} /></span>}
                    </div>
                  </div>
                  {side === "right" && (firstOfRun ? <div className="avatar agent"><Icon name="spark" size={15} /></div> : <div className="avatar ghost" />)}
                </div>
              );
            })}
          </div>
        ))}
        {live && messages.length > 0 && (
          <div className="listening">
            <span className="live-dot" /> {lastMsg?.role === "agent" ? "Listening for the client..." : "BA is thinking..."}
            <span className="dots"><i /><i /><i /></span>
          </div>
        )}
        {unseen > 0 && !atBottom && (
          <button className="jump" onClick={() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; setUnseen(0); }}>
            {unseen} new <Icon name="chevron" size={14} />
          </button>
        )}
      </div>
      <footer className="chat-foot">
        <Icon name="shield" size={14} /> Read-only record. Audio is never stored; this text is kept for the configured retention period, then deleted.
      </footer>
    </section>
  );
}

/* ------------------------------------------------------------ conversations view */
export function ConversationsView({ ctx }: { ctx: Ctx }) {
  const list = usePolling<ConversationRow[]>(() => api.conversations(ctx.clientId), [ctx.clientId], 5000);
  const [selected, setSelected] = useState<string | null>(() => {
    const parts = window.location.hash.split("/");
    return parts[1] === "conversations" && parts[2] ? (parts[2] ?? null) : null;
  });
  const [q, setQ] = useState("");
  const rows = list.data ?? [];
  const withChat = rows.filter((r) => r.messages > 0 || r.status === "live");
  const hidden = rows.length - withChat.length;
  const shown = withChat.filter((r) => r.title.toLowerCase().includes(q.toLowerCase()));

  useEffect(() => {
    if (!selected || !rows.some((r) => r.meeting_id === selected)) {
      const first = withChat[0] ?? null;
      if (first && (selected === null || !rows.some((r) => r.meeting_id === selected))) setSelected(first.meeting_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length, ctx.clientId]);

  const current = rows.find((r) => r.meeting_id === selected) ?? null;
  const pick = (id: string) => {
    setSelected(id);
    window.history.replaceState(null, "", `#/conversations/${id}`);
  };
  let lastDay = "";
  return (
    <div className="convo">
      <aside className="convo-list">
        <div className="convo-search">
          <Icon name="search" size={16} />
          <input placeholder="Search calls..." value={q} onChange={(e) => setQ(e.target.value)} aria-label="search calls" />
        </div>
        <div className="convo-items">
          {list.loading && rows.length === 0 && <div className="stack" style={{ padding: 14 }}>{[0, 1, 2].map((i) => <Skeleton key={i} h={58} />)}</div>}
          {!list.loading && shown.length === 0 && (
            <Empty icon="chat" title="No conversations yet" hint="Run a live capture or import a transcript; every call will appear here." />
          )}
          {shown.map((r) => {
            const k = dayKey(r.started_at);
            const header = k !== lastDay ? dayLabel(r.started_at) : null;
            lastDay = k;
            return (
              <div key={r.meeting_id}>
                {header && <div className="day-head">{header}</div>}
                <button className={`convo-item${r.meeting_id === selected ? " on" : ""}`} onClick={() => pick(r.meeting_id)}>
                  <div className={`avatar big ${r.mode === "interview" ? "agent" : "client"}`}>{r.mode === "interview" ? <Icon name="spark" size={19} /> : <Icon name="file" size={19} />}</div>
                  <div style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="ellipsis" style={{ fontWeight: 650, flex: 1 }}>{r.title}</span>
                      <span className="faint" style={{ fontSize: 11.5 }}>{clockTimeSafe(r.started_at)}</span>
                    </div>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="muted ellipsis" style={{ fontSize: 12.5, flex: 1 }}>
                        {r.messages} messages · {r.agent_messages} from the BA · {pretty(r.source)}
                      </span>
                      {r.status === "live" ? <Badge tone="good" pulse>live</Badge> : r.status === "failed" ? <Badge tone="bad">failed</Badge> : null}
                    </div>
                  </div>
                </button>
              </div>
            );
          })}
          {hidden > 0 && <div className="faint" style={{ padding: 14, fontSize: 12 }}>{hidden} older call(s) have no stored conversation (recorded before conversations were kept).</div>}
        </div>
      </aside>
      {current ? <ChatPane key={current.meeting_id} ctx={ctx} row={current} /> : (
        <section className="chat"><div className="chat-body" style={{ display: "grid", placeItems: "center" }}><Empty icon="chat" title="Select a call" hint="Pick a conversation on the left to read it as a chat." /></div></section>
      )}
    </div>
  );
}

const clockTimeSafe = (iso: string) => clockTime(iso);
