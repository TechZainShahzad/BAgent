import { api, type Overview } from "./api";
import { clockTime, dayLabel, pretty, toneFor, usePolling } from "./lib";
import { Badge, Card, Donut, Empty, Icon, Skeleton, Stat, StatusBadge } from "./ui";

const ORDER = ["observed", "extracted", "under_clarification", "confirmed", "ready_for_delivery", "synced", "blocked", "rejected", "superseded"];

/** Home: everything in the database across ALL clients, so nothing is hidden behind a client picker. */
export function HomeView({
  clientId,
  onPickClient,
  goto,
}: {
  clientId: string;
  onPickClient: (id: string, view?: string) => void;
  goto: (v: string) => void;
}) {
  const ov = usePolling<Overview>(() => api.overview(), [], 5000);
  const d = ov.data;
  if (!d) {
    return (
      <div className="page">
        {ov.error ? <div className="err">Could not load the overview: {ov.error}</div> : <div className="grid g4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={110} />)}</div>}
      </div>
    );
  }
  const t = d.totals;
  const parts = ORDER.filter((s) => d.requirements_by_status[s]).map((s) => ({
    label: s,
    value: d.requirements_by_status[s] ?? 0,
    tone: toneFor("req", s),
  }));
  const problems = t.failed_jobs + t.failed_actions + t.open_contradictions;
  return (
    <div className="page">
      <div className="grid g4">
        <Stat label="Clients" value={t.clients} icon="people" hint={`${t.meetings} calls in total`} />
        <Stat label="Requirements" value={t.requirements} icon="list" hint={`${t.open_questions} open questions`} />
        <Stat label="Jira issues created" value={t.jira_issues} icon="link" hint={t.waiting_actions ? `${t.waiting_actions} waiting` : "none waiting"} />
        <Stat label="Conversation messages" value={t.messages} icon="chat" hint={t.live_calls ? `${t.live_calls} call live now` : "no live call"} />
      </div>

      {problems > 0 && (
        <div className="err">
          {t.failed_jobs > 0 && `${t.failed_jobs} failed job(s). `}
          {t.failed_actions > 0 && `${t.failed_actions} failed Jira action(s). `}
          {t.open_contradictions > 0 && `${t.open_contradictions} open contradiction(s). `}
          See the System, Jira actions and Insights screens of the affected client.
        </div>
      )}

      <div className="grid g21">
        <Card title="Clients" icon="people" flush>
          {d.clients.length === 0 ? (
            <Empty icon="people" title="No clients yet" hint="Register one from Live capture (+ New client)." />
          ) : (
            <table className="tbl">
              <thead>
                <tr><th>Client</th><th>Calls</th><th>Messages</th><th>Requirements</th><th>Jira</th><th>Last activity</th></tr>
              </thead>
              <tbody>
                {d.clients.map((c) => (
                  <tr key={c.id} className={c.id === clientId ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => onPickClient(c.id)}>
                    <td style={{ fontWeight: 600 }}>{c.name}{c.id === clientId && <span className="faint"> (selected)</span>}</td>
                    <td>{c.meetings}</td>
                    <td>{c.messages}</td>
                    <td>{c.requirements}</td>
                    <td>{c.jira_issues}</td>
                    <td className="muted">{dayLabel(c.last_activity_at)}, {clockTime(c.last_activity_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Requirements, all clients" icon="layers">
          {t.requirements === 0 ? (
            <Empty icon="list" title="No requirements yet" hint="Run a live capture or import a transcript." />
          ) : (
            <Donut parts={parts} center={t.requirements} sub="requirements" />
          )}
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Recent calls" icon="mic" flush right={<button className="btn sm ghost" onClick={() => goto("conversations")}>Conversations</button>}>
          {d.recent_meetings.length === 0 ? (
            <Empty icon="mic" title="No calls yet" />
          ) : (
            <div className="list">
              {d.recent_meetings.map((m) => (
                <div className="list-item" key={m.meeting_id} onClick={() => onPickClient(m.client_id, "conversations")}>
                  <div className="grow">
                    <div className="title ellipsis">{m.title}</div>
                    <div className="sub">{m.client_name} · {m.messages} messages · {dayLabel(m.started_at)}</div>
                  </div>
                  {m.status === "live" ? <Badge tone="good" pulse>live</Badge> : <StatusBadge status={m.status} tone={toneFor("meeting", m.status)} />}
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Recent Jira actions" icon="bolt" flush right={<button className="btn sm ghost" onClick={() => goto("actions")}>Jira actions</button>}>
          {d.recent_actions.length === 0 ? (
            <Empty icon="bolt" title="No Jira actions yet" />
          ) : (
            <div className="list">
              {d.recent_actions.map((a, i) => (
                <div className="list-item" key={i} onClick={() => onPickClient(a.client_id, "actions")}>
                  <div className="grow">
                    <div className="title ellipsis">{a.summary ?? pretty(a.kind)}</div>
                    <div className="sub">{a.client_name} · {pretty(a.kind)}{a.jira_key ? ` · ${a.jira_key}` : ""}</div>
                  </div>
                  <StatusBadge status={a.status} tone={toneFor("intent", a.status)} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <div className="faint" style={{ fontSize: 12 }}><Icon name="shield" size={13} /> These numbers are read straight from the database on every refresh.</div>
    </div>
  );
}
