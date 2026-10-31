import { useMemo, useState } from "react";
import { api, type IntentRow } from "./api";
import { jiraKeys, type Ctx } from "./data";
import { attentionItems, countBy, filterRequirements, pretty, toneFor } from "./lib";
import {
  Badge,
  Card,
  Donut,
  Empty,
  Icon,
  JiraKey,
  Modal,
  Skeleton,
  Stat,
  StatusBadge,
  Switch,
  useToast,
} from "./ui";

const KIND_ICON: Record<string, string> = {
  goal: "spark",
  functional: "bolt",
  rule: "shield",
  nfr: "clock",
  constraint: "layers",
  actor: "user",
};

/* ------------------------------------------------------------------ Overview */
export function OverviewView({ ctx, loading }: { ctx: Ctx; loading: boolean }) {
  const { data, control, goto, openRequirement } = ctx;
  const byStatus = countBy(data.requirements, (r) => r.status);
  const order = ["extracted", "under_clarification", "confirmed", "ready_for_delivery", "synced", "blocked", "rejected"];
  const parts = order
    .filter((s) => byStatus[s])
    .map((s) => ({ label: s, value: byStatus[s] ?? 0, tone: toneFor("req", s) }));
  const total = data.requirements.length;
  const inJira = jiraKeys(data.intents).length;
  const confirmed = data.requirements.filter((r) => ["confirmed", "ready_for_delivery", "synced"].includes(r.status)).length;
  const attention = attentionItems({
    jobs: data.jobs,
    intents: data.intents,
    meetings: data.meetings,
    questions: data.questions,
    conflicts: data.conflicts,
    auditOk: data.auditOk,
  });
  const openQ = data.questions.filter((q) => q.status === "open" || q.status === "asked").length;
  const funnel = [
    { name: "Captured", n: total, tone: "var(--info)" },
    { name: "Confirmed", n: confirmed, tone: "var(--violet)" },
    { name: "Ready", n: data.requirements.filter((r) => ["ready_for_delivery", "synced"].includes(r.status)).length, tone: "var(--cyan)" },
    { name: "In Jira", n: inJira, tone: "var(--good)" },
  ];
  const max = Math.max(1, ...funnel.map((f) => f.n));

  if (loading && total === 0)
    return (
      <div className="page">
        <div className="grid g4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={110} />)}</div>
        <Skeleton h={260} />
      </div>
    );

  return (
    <div className="page">
      <div className="grid g4">
        <Stat label="Requirements" value={total} icon="list" hint={`${confirmed} confirmed`} />
        <Stat label="Jira issues created" value={inJira} icon="link" hint={control?.jira_allowlist?.join(", ") ?? ""} />
        <Stat label="Open questions" value={openQ} icon="help" hint={`${data.questions.filter((q) => q.blocking && q.status === "open").length} blocking`} />
        <Stat label="Needs attention" value={attention.length} icon="alert" hint={attention.length ? "see the list below" : "all clear"} />
      </div>

      <div className="grid g21">
        <Card title="Requirement pipeline" icon="layers">
          {total === 0 ? (
            <Empty icon="list" title="No requirements yet" hint="Import a transcript or run a live meeting to get started." />
          ) : (
            <div className="grid g2" style={{ alignItems: "center" }}>
              <Donut parts={parts} center={total} sub="requirements" />
              <div className="funnel">
                {funnel.map((f) => (
                  <div className="step" key={f.name}>
                    <div className="name">{f.name}</div>
                    <div className="bar">
                      <i style={{ width: `${(f.n / max) * 100}%`, background: f.tone }} />
                    </div>
                    <div className="n">{f.n}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
        <Card title="Needs attention" icon="alert">
          <div className="stack">
            {attention.length === 0 ? (
              <div className="allgood">
                <Icon name="check" /> Nothing needs you right now.
              </div>
            ) : (
              attention.map((a, i) => (
                <div key={i} className={`attn ${a.level}`} onClick={() => goto(a.go)} role="button" tabIndex={0}>
                  <Icon name={a.level === "bad" ? "alert" : "clock"} />
                  <span style={{ flex: 1 }}>{a.text}</span>
                  <Icon name="chevron" size={15} />
                </div>
              ))
            )}
          </div>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Recent Jira actions" icon="bolt" flush right={<button className="btn sm ghost" onClick={() => goto("actions")}>View all</button>}>
          {data.intents.length === 0 ? (
            <Empty icon="bolt" title="No actions yet" />
          ) : (
            <div className="list">
              {data.intents.slice(0, 5).map((i) => (
                <div className="list-item" key={i.id} onClick={() => goto("actions")}>
                  <div className="grow">
                    <div className="title ellipsis">{i.summary ?? pretty(i.kind)}</div>
                    <div className="sub">{pretty(i.kind)}</div>
                  </div>
                  {i.jira_key && <JiraKey k={i.jira_key} base={control?.jira_base_url} />}
                  <StatusBadge status={i.status} tone={toneFor("intent", i.status)} />
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Recent requirements" icon="list" flush right={<button className="btn sm ghost" onClick={() => goto("requirements")}>View all</button>}>
          {total === 0 ? (
            <Empty icon="list" title="Nothing captured" />
          ) : (
            <div className="list">
              {data.requirements.slice(0, 5).map((r) => (
                <div className="list-item" key={r.id} onClick={() => openRequirement(r.id)}>
                  <Icon name={KIND_ICON[r.kind] ?? "spark"} />
                  <div className="grow">
                    <div className="title ellipsis">{r.title}</div>
                    <div className="sub">{r.kind}</div>
                  </div>
                  <StatusBadge status={r.status} tone={toneFor("req", r.status)} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- Requirements */
export function RequirementsView({ ctx }: { ctx: Ctx }) {
  const { data, openRequirement } = ctx;
  const [q, setQ] = useState("");
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const allStatuses = useMemo(() => [...new Set(data.requirements.map((r) => r.status))], [data.requirements]);
  const allKinds = useMemo(() => [...new Set(data.requirements.map((r) => r.kind))], [data.requirements]);
  const rows = filterRequirements(data.requirements, q, statuses, kinds);
  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    setter(n);
  };
  const jiraByReq = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of data.intents) if (i.status === "succeeded" && i.jira_key && i.requirement_id && i.kind !== "add_reference") m.set(i.requirement_id, i.jira_key);
    return m;
  }, [data.intents]);
  return (
    <div className="page">
      <Card>
        <div className="stack">
          <div className="row">
            <div style={{ position: "relative", flex: 1 }}>
              <input
                className="input"
                style={{ paddingLeft: 38 }}
                placeholder="Search requirements..."
                value={q}
                onChange={(e) => setQ(e.target.value)}
                aria-label="search requirements"
              />
              <span style={{ position: "absolute", left: 12, top: 9, color: "var(--faint)" }}>
                <Icon name="search" size={17} />
              </span>
            </div>
            {(q || statuses.size > 0 || kinds.size > 0) && (
              <button className="btn ghost" onClick={() => { setQ(""); setStatuses(new Set()); setKinds(new Set()); }}>
                <Icon name="x" size={15} /> Clear
              </button>
            )}
          </div>
          <div className="row wrap">
            {allStatuses.map((s) => (
              <button key={s} className={`chip${statuses.has(s) ? " on" : ""}`} onClick={() => toggle(statuses, setStatuses, s)}>
                {pretty(s)}
              </button>
            ))}
            <span className="faint">|</span>
            {allKinds.map((k) => (
              <button key={k} className={`chip${kinds.has(k) ? " on" : ""}`} onClick={() => toggle(kinds, setKinds, k)}>
                <Icon name={KIND_ICON[k] ?? "spark"} size={13} /> {k}
              </button>
            ))}
          </div>
        </div>
      </Card>
      <Card flush>
        {rows.length === 0 ? (
          <Empty icon="search" title={data.requirements.length ? "No match" : "No requirements yet"} hint={data.requirements.length ? "Try clearing the filters." : "Import a transcript in Meetings, or run a live capture."} />
        ) : (
          <div className="list">
            {rows.map((r) => (
              <div className="list-item" key={r.id} onClick={() => openRequirement(r.id)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openRequirement(r.id)}>
                <Icon name={KIND_ICON[r.kind] ?? "spark"} />
                <div className="grow">
                  <div className="title ellipsis">{r.title}</div>
                  <div className="sub">
                    {r.kind} · v{r.version}
                    {r.explicit ? "" : " · inferred"}
                  </div>
                </div>
                {jiraByReq.get(r.id) && <JiraKey k={jiraByReq.get(r.id) as string} base={ctx.control?.jira_base_url} />}
                <StatusBadge status={r.status} tone={toneFor("req", r.status)} />
                <Icon name="chevron" size={16} />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------- Actions */
const INTENT_HELP: Record<string, string> = {
  dry_run: "Recorded only. Nothing was written to Jira (dry-run mode).",
  awaiting_approval: "Waiting for you. Approve to let the worker write it to Jira.",
  held: "Held back (kill switch, open conflict or possible duplicate). You can approve to release it.",
  approved: "Approved; the worker will execute it next.",
  executing: "In flight. If the outcome is unknown it is reconciled by label before any retry.",
  unknown_outcome: "Jira did not answer in time; the platform searches by label before retrying. No duplicates.",
  succeeded: "Done.",
  failed: "Jira rejected it. See the reason; fix the requirement and re-propose.",
  rejected: "Rejected by a human.",
  cancelled: "Cancelled because the requirement changed or was replaced before it ran.",
};

export function ActionsView({ ctx }: { ctx: Ctx }) {
  const { data, clientId, control, refresh } = ctx;
  const toast = useToast();
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState<string | null>(null);
  const statuses = ["all", ...new Set(data.intents.map((i) => i.status))];
  const rows = data.intents.filter((i) => filter === "all" || i.status === filter);
  const act = async (i: IntentRow, kind: "approve" | "reject") => {
    setBusy(i.id);
    try {
      if (kind === "approve") await api.approve(clientId, i.id);
      else await api.reject(clientId, i.id, "rejected in dashboard");
      toast("good", kind === "approve" ? "Approved. The worker will execute it." : "Rejected.");
      refresh();
    } catch (e) {
      toast("bad", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="page">
      <div className="row wrap">
        {statuses.map((s) => (
          <button key={s} className={`chip${filter === s ? " on" : ""}`} onClick={() => setFilter(s)}>
            {pretty(s)} {s !== "all" && <span className="faint">{data.intents.filter((i) => i.status === s).length}</span>}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <Card><Empty icon="bolt" title="No Jira actions" hint="Actions appear here when a requirement is delivered (or would have been in dry-run)." /></Card>
      ) : (
        <div className="stack">
          {rows.map((i) => (
            <Card key={i.id}>
              <div className="row wrap">
                <Icon name={i.kind === "create_issue" ? "plus" : i.kind === "update_issue" ? "refresh" : "link"} />
                <div style={{ flex: 1, minWidth: 220 }}>
                  <div style={{ fontWeight: 650 }}>{i.summary ?? pretty(i.kind)}</div>
                  <div className="muted" style={{ fontSize: 12.5 }}>
                    {pretty(i.kind)} → {i.project_key} · attempts {i.attempts}
                  </div>
                </div>
                {i.jira_key && <JiraKey k={i.jira_key} base={control?.jira_base_url} />}
                <StatusBadge status={i.status} tone={toneFor("intent", i.status)} />
                {(i.status === "awaiting_approval" || i.status === "held") && (
                  <>
                    <button className="btn good sm" disabled={busy === i.id} onClick={() => act(i, "approve")}>
                      <Icon name="check" size={14} /> Approve
                    </button>
                    <button className="btn bad sm" disabled={busy === i.id} onClick={() => act(i, "reject")}>
                      <Icon name="x" size={14} /> Reject
                    </button>
                  </>
                )}
              </div>
              <div className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>{INTENT_HELP[i.status] ?? ""}</div>
              {i.last_error && <div className="err" style={{ marginTop: 10 }}>{i.last_error}</div>}
              <div className="faint mono" style={{ marginTop: 8 }}>{i.marker}</div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Insights */
export function InsightsView({ ctx }: { ctx: Ctx }) {
  const { data, openRequirement } = ctx;
  const titleOf = (id: string) => data.requirements.find((r) => r.id === id)?.title ?? id.slice(0, 8);
  const qs = [...data.questions].sort((a, b) => Number(b.blocking) - Number(a.blocking));
  return (
    <div className="page">
      <div className="grid g2">
        <Card title="Conflicts & duplicates" icon="alert" flush>
          {data.conflicts.length === 0 ? (
            <Empty icon="shield" title="No conflicts" hint="Contradictions are never resolved automatically; they appear here." />
          ) : (
            <div className="list">
              {data.conflicts.map((c) => (
                <div key={c.id} className="list-item" style={{ alignItems: "flex-start", cursor: "default" }}>
                  <div className="grow">
                    <div className="row wrap" style={{ marginBottom: 6 }}>
                      <Badge tone={c.relation === "contradiction" ? "bad" : c.relation === "duplicate" ? "warn" : "info"}>{c.relation}</Badge>
                      <span className="faint">similarity</span>
                      <div className="bar" style={{ width: 70 }}><i style={{ width: `${c.similarity * 100}%` }} /></div>
                      <span className="muted">{c.similarity}</span>
                      <Badge tone={c.status === "open" ? "warn" : "muted"}>{c.status}</Badge>
                    </div>
                    <div style={{ whiteSpace: "normal" }}>{c.explanation}</div>
                    <div className="row wrap" style={{ marginTop: 8 }}>
                      <button className="chip" onClick={() => openRequirement(c.requirement_id)}>{titleOf(c.requirement_id)}</button>
                      <span className="faint">vs</span>
                      <button className="chip" onClick={() => openRequirement(c.other_requirement_id)}>{titleOf(c.other_requirement_id)}</button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Open questions" icon="help" flush>
          {qs.length === 0 ? (
            <Empty icon="help" title="No questions" />
          ) : (
            <div className="list">
              {qs.map((q) => (
                <div key={q.id} className="list-item" style={{ alignItems: "flex-start", cursor: "default" }}>
                  <div className="grow">
                    <div className="row wrap" style={{ marginBottom: 4 }}>
                      {q.blocking && <Badge tone="bad">blocking</Badge>}
                      <Badge tone={q.status === "answered" ? "good" : q.status === "dropped" ? "muted" : "info"}>{q.status}</Badge>
                    </div>
                    <div style={{ whiteSpace: "normal", fontWeight: 600 }}>{q.question}</div>
                    <div className="muted" style={{ whiteSpace: "normal", fontSize: 12.5 }}>{q.why}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------- People */
export function PeopleView({ ctx }: { ctx: Ctx }) {
  const { data, clientId, refresh } = ctx;
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", role: "", authority: false, verified: false, note: "" });
  const save = async (s: { name: string; role?: string | null; has_decision_authority: boolean; identity_verified: boolean; verification_note?: string | null }) => {
    try {
      await api.upsertStakeholder(clientId, s);
      toast("good", `Saved ${s.name}`);
      refresh();
    } catch (e) {
      toast("bad", (e as Error).message);
    }
  };
  return (
    <div className="page">
      <Card>
        <div className="row">
          <div className="muted" style={{ flex: 1 }}>
            Only a <b>verified</b> stakeholder with <b>decision authority</b> can confirm a requirement. Verification is a human decision, never inferred from a transcript label.
          </div>
          <button className="btn primary" onClick={() => setOpen(true)}>
            <Icon name="plus" size={16} /> Add person
          </button>
        </div>
      </Card>
      <Card flush>
        {data.stakeholders.length === 0 ? (
          <Empty icon="people" title="No stakeholders yet" hint="Add the people whose words may confirm requirements." />
        ) : (
          <table className="tbl">
            <thead><tr><th>Name</th><th>Role</th><th>Decision authority</th><th>Identity verified</th><th>Note</th></tr></thead>
            <tbody>
              {data.stakeholders.map((s) => (
                <tr key={s.id} className="hover">
                  <td style={{ fontWeight: 600 }}>{s.name}</td>
                  <td className="muted">{s.role ?? "-"}</td>
                  <td>
                    <Switch label={`authority ${s.name}`} on={s.has_decision_authority} onChange={(v) => save({ name: s.name, role: s.role, has_decision_authority: v, identity_verified: s.identity_verified, verification_note: s.verification_note })} />
                  </td>
                  <td>
                    <Switch label={`verified ${s.name}`} on={s.identity_verified} onChange={(v) => save({ name: s.name, role: s.role, has_decision_authority: s.has_decision_authority, identity_verified: v, verification_note: v ? "verified in dashboard" : s.verification_note })} />
                  </td>
                  <td className="muted" style={{ maxWidth: 300 }}>{s.verification_note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="Add a person">
        <div className="stack">
          <label className="field"><span>Name (as it appears in transcripts)</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field"><span>Role</span><input className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} placeholder="e.g. Head of Product" /></label>
          <label className="field"><span>How did you verify them?</span><input className="input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <div className="row"><Switch label="authority" on={form.authority} onChange={(v) => setForm({ ...form, authority: v })} /> Can confirm requirements</div>
          <div className="row"><Switch label="verified" on={form.verified} onChange={(v) => setForm({ ...form, verified: v })} /> Identity verified by me</div>
          <div className="row">
            <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary right" disabled={!form.name.trim()} onClick={async () => { await save({ name: form.name.trim(), role: form.role || null, has_decision_authority: form.authority, identity_verified: form.verified, verification_note: form.note || null }); setOpen(false); setForm({ name: "", role: "", authority: false, verified: false, note: "" }); }}>Save</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

/* -------------------------------------------------------------------- System */
export function SystemView({ ctx, onKill }: { ctx: Ctx; onKill: (on: boolean) => void }) {
  const { data, control, clientId, refresh } = ctx;
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState(control?.jira_allowlist?.[0] ?? "");
  const failed = data.jobs.filter((j) => j.status === "failed");
  return (
    <div className="page">
      <div className="grid g3">
        <Card title="Kill switch" icon="shield">
          <div className="row">
            <Switch danger label="kill switch" on={control?.kill_switch ?? false} onChange={onKill} />
            <div>
              <div style={{ fontWeight: 650 }}>{control?.kill_switch ? "ALL Jira writes are paused" : "Writes are allowed"}</div>
              <div className="muted" style={{ fontSize: 12.5 }}>Takes effect immediately, even for approved actions.</div>
            </div>
          </div>
        </Card>
        <Card title="Autonomy" icon="bolt">
          <div style={{ fontSize: 22, fontWeight: 700, textTransform: "capitalize" }}>{pretty(control?.autonomy_mode ?? "?")}</div>
          <div className="muted" style={{ fontSize: 12.5 }}>This is the API process's mode. The worker has its own (set in the window that starts it). Allowed Jira projects: <b>{control?.jira_allowlist?.join(", ") || "none"}</b></div>
        </Card>
        <Card title="Audit chain" icon="shield">
          <div className="row">
            <Badge tone={data.auditOk === null ? "muted" : data.auditOk ? "good" : "bad"}>
              {data.auditOk === null ? "unknown" : data.auditOk ? "verified" : "BROKEN"}
            </Badge>
            <button className="btn sm" onClick={refresh}><Icon name="refresh" size={14} /> Verify now</button>
          </div>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>Every action is hash-chained; any edit to history breaks verification.</div>
        </Card>
      </div>

      <Card title="Jobs" icon="clock" flush right={failed.length ? <Badge tone="bad">{failed.length} failed</Badge> : undefined}>
        {data.jobs.length === 0 ? <Empty icon="clock" title="No jobs yet" /> : (
          <table className="tbl">
            <thead><tr><th>Kind</th><th>Status</th><th>Attempts</th><th>Last error</th></tr></thead>
            <tbody>
              {data.jobs.slice(0, 40).map((j) => (
                <tr key={j.id} className="hover">
                  <td className="mono">{j.kind}</td>
                  <td><Badge tone={j.status === "succeeded" ? "good" : j.status === "failed" ? "bad" : j.status === "running" ? "info" : "muted"}>{j.status}</Badge></td>
                  <td>{j.attempts}</td>
                  <td className="muted" style={{ maxWidth: 420, whiteSpace: "normal" }}>{j.last_error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Projects" icon="layers" right={<button className="btn sm" onClick={() => setOpen(true)}><Icon name="plus" size={14} /> New project</button>}>
        {data.projects.length === 0 ? <div className="muted">No projects yet. Create one linked to a Jira project key.</div> : (
          <div className="row wrap">
            {data.projects.map((p) => (
              <div className="card" key={p.id} style={{ padding: 12 }}>
                <div style={{ fontWeight: 650 }}>{p.name}</div>
                <div className="faint mono">{p.id.slice(0, 8)}</div>
                {p.jira_project_key && <JiraKey k={p.jira_project_key} />}
              </div>
            ))}
          </div>
        )}
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="New project">
        <div className="stack">
          <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field"><span>Jira project key (must be in the allowlist)</span><input className="input" value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} /></label>
          <div className="row">
            <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary right" disabled={!name.trim()} onClick={async () => {
              try { await api.createProject(clientId, name.trim(), key || null); toast("good", "Project created"); setOpen(false); setName(""); refresh(); }
              catch (e) { toast("bad", (e as Error).message); }
            }}>Create</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
