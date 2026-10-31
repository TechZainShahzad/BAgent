import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AutopilotReport, type LiveState, type MeetingRow, type Trace } from "./api";
import type { Ctx } from "./data";
import { fmtTime, parseMarkdown, pretty, toneFor, usePolling } from "./lib";
import { Badge, Card, Drawer, Empty, Icon, JiraKey, Modal, Ring, Skeleton, StatusBadge, Tabs, useToast } from "./ui";

/* ---------------------------------------------------- requirement "Why?" drawer */
export function RequirementDrawer({ ctx, id, onClose }: { ctx: Ctx; id: string | null; onClose: () => void }) {
  const toast = useToast();
  const [tab, setTab] = useState("evidence");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState({ title: "", statement: "", reason: "" });
  const { data, error, loading, refresh } = usePolling<Trace>(
    () => api.trace(ctx.clientId, id as string),
    [ctx.clientId, id],
    8000,
    id !== null,
  );
  useEffect(() => {
    setTab("evidence");
  }, [id]);
  useEffect(() => {
    if (data) setEdit((e) => (e.title ? e : { title: data.requirement.title, statement: data.requirement.statement, reason: "" }));
  }, [data]);
  useEffect(() => setEdit({ title: "", statement: "", reason: "" }), [id]);

  const move = async (target: string) => {
    setBusy(true);
    try {
      await api.transition(ctx.clientId, id as string, target, "dashboard action");
      toast("good", `Moved to ${pretty(target)}`);
      refresh();
      ctx.refresh();
    } catch (e) {
      toast("bad", `Refused: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };
  const r = data?.requirement;
  return (
    <Drawer
      open={id !== null}
      onClose={onClose}
      title={r?.title ?? "Loading..."}
      subtitle={
        r && (
          <div className="row wrap">
            <StatusBadge status={r.status} tone={toneFor("req", r.status)} />
            <Badge tone="muted">{r.kind}</Badge>
            <span className="faint">v{r.version}</span>
            {data?.jira.map((j) => <JiraKey key={j.key} k={j.key} base={ctx.control?.jira_base_url} />)}
          </div>
        )
      }
      footer={
        r && (
          <>
            <button className="btn good" disabled={busy} onClick={() => move("confirmed")}><Icon name="check" size={15} /> Confirm</button>
            <button className="btn" disabled={busy} onClick={() => move("ready_for_delivery")}><Icon name="bolt" size={15} /> Mark ready</button>
            <button className="btn bad" disabled={busy} onClick={() => move("rejected")}><Icon name="x" size={15} /> Reject</button>
            <span className="muted right" style={{ alignSelf: "center", fontSize: 12 }}>Guards decide; refusals show why.</span>
          </>
        )
      }
    >
      {loading && !data ? (
        <div className="stack"><Skeleton h={22} /><Skeleton h={90} /><Skeleton h={90} /></div>
      ) : error && !data ? (
        <div className="err">{error}</div>
      ) : data ? (
        <>
          <p style={{ marginTop: 0, fontSize: 15 }}>{data.requirement.statement}</p>
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "evidence", label: `Evidence (${data.evidence.length})` },
              { id: "draft", label: `Draft (${data.drafts.length})` },
              { id: "policy", label: `Policy (${data.policy_decisions.length})` },
              { id: "conflicts", label: `Conflicts (${data.conflicts.length})` },
              { id: "versions", label: `Versions (${data.versions.length})` },
            ]}
          />
          {tab === "evidence" && (
            <div className="timeline">
              {data.evidence.length === 0 && <div className="muted">No evidence stored (expired or purged).</div>}
              {data.evidence.map((e, i) => (
                <div className="ev" key={i}>
                  <div className="quote">“{e.quote}”</div>
                  <div className="row wrap" style={{ marginTop: 6 }}>
                    <Badge tone="info">{e.speaker_label ?? "unknown speaker"}</Badge>
                    {e.start_s !== null && <span className="faint mono">at {fmtTime(e.start_s)}</span>}
                    <Badge tone={e.stakeholder_linked ? "good" : "warn"}>
                      {e.stakeholder_linked ? "linked to a stakeholder" : "identity unverified"}
                    </Badge>
                  </div>
                  <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>Kept until {new Date(e.expires_at).toLocaleDateString()}</div>
                </div>
              ))}
            </div>
          )}
          {tab === "draft" && (
            <div className="stack">
              {data.drafts.length === 0 && <Empty icon="file" title="No Jira draft yet" hint="Drafts are made when the requirement is delivered." />}
              {data.drafts.map((d) => (
                <Card key={d.id}>
                  <div className="row wrap"><Badge tone="cyan">{d.issue_type}</Badge><Badge tone={d.measurable ? "good" : "warn"}>{d.measurable ? "testable" : "not testable yet"}</Badge></div>
                  <div style={{ fontWeight: 650, margin: "8px 0" }}>{d.summary}</div>
                  <ul style={{ margin: 0, paddingLeft: 20 }}>{d.acceptance_criteria.map((a, i) => <li key={i}>{a}</li>)}</ul>
                  {d.flags.length > 0 && <div className="stack" style={{ marginTop: 10 }}>{d.flags.map((f, i) => <div className="err" key={i}>{f}</div>)}</div>}
                </Card>
              ))}
            </div>
          )}
          {tab === "policy" && (
            <div className="stack">
              {data.policy_decisions.length === 0 && <Empty icon="shield" title="No policy decisions yet" />}
              {data.policy_decisions.map((p, i) => (
                <Card key={i}>
                  <div className="row wrap">
                    <Badge tone={p.outcome === "execute" ? "good" : p.outcome === "reject" ? "bad" : "warn"}>{p.outcome}</Badge>
                    {p.rules.map((x) => <span key={x} className="kbd">{x}</span>)}
                    {p.disposition && <Badge tone="muted">{pretty(p.disposition)}</Badge>}
                    {p.mode && <Badge tone="info">{pretty(p.mode)}</Badge>}
                  </div>
                  <div style={{ marginTop: 8 }}>{p.reason}</div>
                </Card>
              ))}
            </div>
          )}
          {tab === "conflicts" && (
            <div className="stack">
              {data.conflicts.length === 0 && <Empty icon="shield" title="No conflicts" />}
              {data.conflicts.map((c) => (
                <Card key={c.id}>
                  <div className="row wrap"><Badge tone={c.relation === "contradiction" ? "bad" : "warn"}>{c.relation}</Badge><span className="muted">similarity {c.similarity}</span><Badge tone="muted">{c.status}</Badge></div>
                  <div style={{ marginTop: 8 }}>{c.explanation}</div>
                </Card>
              ))}
            </div>
          )}
          {tab === "versions" && (
            <div className="stack">
              <div className="timeline">
                {data.versions.map((v) => (
                  <div className="ev" key={v.version}>
                    <div style={{ fontWeight: 650 }}>v{v.version}: {v.title}</div>
                    <div className="muted">{v.statement}</div>
                    <div className="faint" style={{ fontSize: 12 }}>{v.reason}</div>
                  </div>
                ))}
              </div>
              <Card title="Correct this requirement" icon="refresh">
                <div className="stack">
                  <input className="input" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} aria-label="corrected title" />
                  <textarea className="textarea" value={edit.statement} onChange={(e) => setEdit({ ...edit, statement: e.target.value })} aria-label="corrected statement" />
                  <input className="input" placeholder="Why? (required)" value={edit.reason} onChange={(e) => setEdit({ ...edit, reason: e.target.value })} />
                  <button className="btn primary" disabled={busy || !edit.reason.trim()} onClick={async () => {
                    setBusy(true);
                    try { const res = await api.correct(ctx.clientId, id as string, edit.title, edit.statement, edit.reason); toast("good", `Saved as v${res.version}; re-confirmation needed`); refresh(); ctx.refresh(); setEdit({ ...edit, reason: "" }); }
                    catch (e) { toast("bad", (e as Error).message); }
                    finally { setBusy(false); }
                  }}>Save as a new version</button>
                  <div className="faint" style={{ fontSize: 12 }}>The old version is kept. Approved requirements go back to clarification.</div>
                </div>
              </Card>
            </div>
          )}
        </>
      ) : null}
    </Drawer>
  );
}

/* ------------------------------------------------------------- meeting drawer */
const RESULT_TONE = (r: string) =>
  r.startsWith("intent_succeeded") || r === "already_in_jira" ? "good" : r.startsWith("held") ? "warn" : r === "error" ? "bad" : r.startsWith("included") ? "info" : "muted";

export function MeetingDrawer({ ctx, meeting, onClose }: { ctx: Ctx; meeting: MeetingRow | null; onClose: () => void }) {
  const [tab, setTab] = useState("report");
  const id = meeting?.id ?? null;
  useEffect(() => setTab(meeting?.status === "live" ? "live" : "report"), [id, meeting?.status]);
  const report = usePolling<AutopilotReport>(() => api.autopilot(ctx.clientId, id as string), [ctx.clientId, id], 6000, id !== null);
  const minutes = usePolling<{ markdown: string }>(() => api.minutes(ctx.clientId, id as string), [ctx.clientId, id], 15000, id !== null && tab === "minutes");
  const live = usePolling<LiveState>(() => api.liveState(ctx.clientId, id as string), [ctx.clientId, id], 2500, id !== null && tab === "live");
  const rep = report.data?.report;
  const delivered = rep?.delivered ?? [];
  return (
    <Drawer
      open={meeting !== null}
      onClose={onClose}
      title={meeting?.title ?? ""}
      subtitle={meeting && <div className="row wrap"><StatusBadge status={meeting.status} tone={toneFor("meeting", meeting.status)} /><Badge tone="muted">{meeting.source}</Badge><Badge tone={meeting.consent === "recorded" ? "good" : "bad"}>consent {meeting.consent}</Badge>{meeting.transcript_purged && <Badge tone="info">transcript purged</Badge>}</div>}
    >
      {meeting?.failure_reason && <div className="err" style={{ marginBottom: 12 }}>{meeting.failure_reason}</div>}
      {meeting?.degraded_reason && <div className="err" style={{ marginBottom: 12 }}>Degraded: {meeting.degraded_reason}</div>}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "report", label: "Autopilot report" }, { id: "minutes", label: "Minutes" }, { id: "live", label: "Live interview" }]} />
      {tab === "report" && (
        <div className="stack">
          {!report.data?.autopilot && <Empty icon="bolt" title="Autopilot was not enabled for this meeting" hint="Use --autopilot on capture, or deliver requirements manually." />}
          {rep?.error && <div className="err">{rep.error}</div>}
          {rep?.note && <div className="muted">{rep.note}</div>}
          {(rep?.not_confirmed ?? []).map((n) => (
            <div className="err" key={n.requirement}><b>{n.requirement}</b>: {n.reason}</div>
          ))}
          {delivered.length > 0 && (
            <Card flush>
              <table className="tbl">
                <thead><tr><th>Requirement</th><th>Result</th><th>Jira</th></tr></thead>
                <tbody>
                  {delivered.map((d, i) => (
                    <tr key={i} className="hover">
                      <td><div style={{ fontWeight: 600 }}>{d.requirement}</div><div className="faint">{d.kind}</div>
                        {d.follow_up && <div style={{ color: "var(--warn)", fontSize: 12 }}>{d.follow_up}</div>}
                        {(d.reasons ?? []).slice(0, 3).map((x, j) => <div key={j} className="muted" style={{ fontSize: 12 }}>{x}</div>)}</td>
                      <td><Badge tone={RESULT_TONE(d.result)}>{pretty(d.result.replace("intent_", ""))}</Badge></td>
                      <td>{d.jira_key && <JiraKey k={d.jira_key} base={ctx.control?.jira_base_url} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      )}
      {tab === "minutes" && (minutes.data ? <Minutes md={minutes.data.markdown} /> : minutes.error ? <div className="err">{minutes.error}</div> : <Skeleton h={200} />)}
      {tab === "live" && (live.data ? <LivePanel s={live.data} /> : live.error ? <div className="err">{live.error}</div> : <Skeleton h={200} />)}
    </Drawer>
  );
}

function Minutes({ md }: { md: string }) {
  const blocks = parseMarkdown(md);
  const items: JSX.Element[] = [];
  let list: { text: string; depth: number }[] = [];
  const flush = () => {
    if (list.length) {
      items.push(<ul key={`u${items.length}`}>{list.map((l, i) => <li key={i} style={{ marginLeft: l.depth * 14 }}>{l.text}</li>)}</ul>);
      list = [];
    }
  };
  blocks.forEach((b, i) => {
    if (b.type === "li") { list.push({ text: b.text, depth: b.depth }); return; }
    flush();
    if (b.type === "h1") items.push(<h1 key={i}>{b.text}</h1>);
    else if (b.type === "h2") items.push(<h2 key={i}>{b.text}</h2>);
    else if (b.type === "quote") items.push(<blockquote key={i}>{b.text}</blockquote>);
    else items.push(<p key={i}>{b.text}</p>);
  });
  flush();
  return <div className="md">{items}</div>;
}

function LivePanel({ s }: { s: LiveState }) {
  const all = [...s.covered, ...s.remaining];
  return (
    <div className="stack">
      <div className="row" style={{ gap: 20 }}>
        <Ring value={s.coverage} label="topic coverage" />
        <div>
          <div style={{ fontWeight: 650 }}>Interview coverage</div>
          <div className="muted">{s.covered.length} of {all.length} topics · {s.turns} agent turns · {s.client_segments} client lines</div>
          <div className="row" style={{ marginTop: 6 }}><StatusBadge status={s.status} tone={toneFor("meeting", s.status)} />{s.closed && <Badge tone="info">closing</Badge>}</div>
        </div>
      </div>
      {s.degraded_reason && <div className="err">{s.degraded_reason}</div>}
      <div className="row wrap">
        {s.covered.map((t) => <Badge key={t} tone="good">{pretty(t)}</Badge>)}
        {s.remaining.map((t) => <Badge key={t} tone="muted">{pretty(t)}</Badge>)}
      </div>
      {s.pending_preview && <Card title="Next question (planned)" icon="mic"><div>{s.pending_preview}</div></Card>}
    </div>
  );
}

/* ---------------------------------------------------------- import transcript */
export function ImportModal({ ctx, open, onClose }: { ctx: Ctx; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [consent, setConsent] = useState("");
  const [project, setProject] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const reset = useCallback(() => { setFile(null); setTitle(""); setConsent(""); setProject(""); }, []);
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      await api.importTranscript(ctx.clientId, file, title || file.name, consent, project || undefined);
      toast("good", "Imported. The worker will analyze it shortly.");
      reset(); onClose(); ctx.refresh();
    } catch (e) {
      toast("bad", (e as Error).message);
    } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="Import a transcript">
      <div className="stack">
        <div
          className={`dropzone${over ? " over" : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) { setFile(f); if (!title) setTitle(f.name.replace(/\.[^.]+$/, "")); } }}
        >
          <Icon name="upload" size={26} />
          <div style={{ marginTop: 6 }}>{file ? <b>{file.name}</b> : "Drop a .vtt, .txt or .docx here, or click to choose"}</div>
          <input ref={input} type="file" accept=".vtt,.txt,.docx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); if (!title) setTitle(f.name.replace(/\.[^.]+$/, "")); } }} />
        </div>
        <label className="field"><span>Meeting title</span><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field"><span>Consent basis (required: who attested what)</span><input className="input" value={consent} onChange={(e) => setConsent(e.target.value)} placeholder="e.g. All participants consented to AI transcription" /></label>
        {ctx.data.projects.length > 0 && (
          <label className="field"><span>Project (optional)</span>
            <select className="select" value={project} onChange={(e) => setProject(e.target.value)}>
              <option value="">None</option>
              {ctx.data.projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.jira_project_key ? ` (${p.jira_project_key})` : ""}</option>)}
            </select>
          </label>
        )}
        <div className="row">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary right" disabled={!file || !consent.trim() || busy} onClick={submit}>{busy ? "Uploading..." : "Import & analyze"}</button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------ meetings view */
export function MeetingsView({ ctx }: { ctx: Ctx }) {
  const [importOpen, setImportOpen] = useState(false);
  const { data, openMeeting } = ctx;
  return (
    <div className="page">
      <Card>
        <div className="row wrap">
          <div className="muted" style={{ flex: 1, minWidth: 240 }}>
            Live calls are started from the capture companion (<span className="kbd">ba capture</span>). Transcript files can be imported here.
          </div>
          <button className="btn primary" onClick={() => setImportOpen(true)}><Icon name="upload" size={16} /> Import transcript</button>
        </div>
      </Card>
      <Card flush>
        {data.meetings.length === 0 ? (
          <Empty icon="mic" title="No meetings yet" hint="Import a transcript, or start a live capture." />
        ) : (
          <div className="list">
            {data.meetings.map((m) => (
              <div className="list-item" key={m.id} onClick={() => openMeeting(m.id)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openMeeting(m.id)}>
                <Icon name={m.status === "live" ? "mic" : "file"} />
                <div className="grow">
                  <div className="title ellipsis">{m.title}</div>
                  <div className="sub">{m.source} · consent {m.consent}{m.transcript_purged ? " · transcript purged" : ""}</div>
                  {(m.failure_reason || m.degraded_reason) && <div style={{ color: "var(--bad)", fontSize: 12.5 }}>{m.failure_reason ?? m.degraded_reason}</div>}
                </div>
                <StatusBadge status={m.status} tone={toneFor("meeting", m.status)} />
                <Icon name="chevron" size={16} />
              </div>
            ))}
          </div>
        )}
      </Card>
      <ImportModal ctx={ctx} open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
}
