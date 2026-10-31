import { useEffect, useMemo, useRef, useState } from "react";
import { api, type CaptureForm, type JiraCapabilities, type CaptureOptions, type CaptureStatus, type ClientRow, type ConversationRow, type LiveState, type ProjectRow } from "./api";
import { ChatPane } from "./chat";
import type { Ctx } from "./data";
import { pretty, useLocalStorage, usePolling } from "./lib";
import { Badge, Card, Icon, Modal, Ring, Skeleton, Switch, useToast } from "./ui";

const CONSENT_PRESETS = [
  "Operator is the only other participant and consents",
  "All participants were told an AI assistant transcribes this call and agreed",
  "Practice call, no real participants",
];

/** Pure checks shown under the form (unit tested). Returns human-readable problems. */
export function validateForm(f: CaptureForm, hasProject: boolean): string[] {
  const out: string[] = [];
  if (!f.client_id) out.push("Choose a client.");
  if (!f.title.trim()) out.push("Give the call a title.");
  if (f.consent_basis.trim().length < 3) out.push("Write the consent basis (who agreed to being transcribed).");
  if (!f.use_loopback && !f.use_mic) out.push("Turn on at least one audio source.");
  if (f.autopilot) {
    if (f.mode !== "interview") out.push("Autopilot needs the interview mode.");
    if (!hasProject) out.push("Autopilot needs a project linked to a Jira key.");
    if (!f.remote_name.trim() || !f.remote_authority)
      out.push("Autopilot needs the remote speaker's name and 'can confirm requirements', otherwise nothing can be created.");
  }
  return out;
}

/** Suggest a Jira project key from a name: initials, uppercase, 2-6 characters (unit tested). */
export function suggestKey(name: string): string {
  const words = name.match(/[A-Za-z0-9]+/g) ?? [];
  let key = words.map((w) => w[0]).join("").slice(0, 6).toUpperCase() || "PRJ";
  if (!/^[A-Z]/.test(key)) key = `P${key}`;
  return key.padEnd(2, "X");
}
export const validKey = (k: string) => /^[A-Z][A-Z0-9]{1,9}$/.test(k);

const NEW = "__new__";

export function elapsed(startedAt: string | undefined, now: number): string {
  if (!startedAt) return "0:00";
  const s = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

const DEFAULTS: CaptureForm = {
  client_id: "", project_id: null, title: "", consent_basis: CONSENT_PRESETS[0] ?? "", mode: "interview",
  autopilot: false, remote_name: "", remote_role: "", remote_authority: true, use_loopback: true,
  loopback_device: "", use_mic: false, mic_device: "", agent_output_device: "", agent_voice: "",
  duration_min: 0, model: "small.en",
};

function logTone(line: string): string {
  if (/FAILED|failed|unreachable|Traceback|refus|error:/i.test(line)) return "log bad";
  if (line.includes("AGENT:")) return "log agent";
  if (line.startsWith("[status]")) return "log warn";
  return "log";
}

export function CaptureView({
  ctx,
  status,
  onStatus,
  onDismiss,
}: {
  ctx: Ctx;
  status: CaptureStatus;
  onStatus: () => void;
  onDismiss: () => void;
}) {
  const toast = useToast();
  const [saved, setSaved] = useLocalStorage("ba.captureForm", "{}");
  const [form, setForm] = useState<CaptureForm>(() => {
    let prev: Partial<CaptureForm> = {};
    try { prev = JSON.parse(saved) as Partial<CaptureForm>; } catch { /* ignore */ }
    return { ...DEFAULTS, ...prev, client_id: ctx.clientId, project_id: null, title: "" };
  });
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [options, setOptions] = useState<CaptureOptions | null>(null);
  const [busy, setBusy] = useState(false);
  const [caps, setCaps] = useState<JiraCapabilities | null>(null);
  const [newClient, setNewClient] = useState(false);
  const [newProject, setNewProject] = useState(false);
  const [nc, setNc] = useState({ name: "" });
  const [np, setNp] = useState({ name: "", key: "", keyTouched: false, inJira: true });
  const [creating, setCreating] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [test, setTest] = useState<{ voice?: string; audio?: { peak: number; hearing: boolean } | string; busy?: string }>({});
  const logRef = useRef<HTMLDivElement>(null);
  const active = status.state === "running" || status.state === "stopping";
  const set = <K extends keyof CaptureForm>(k: K, v: CaptureForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => { api.clients().then(setClients).catch(() => undefined); api.captureOptions().then(setOptions).catch(() => undefined); api.jiraCapabilities().then(setCaps).catch(() => undefined); }, []);
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => {
    if (!form.client_id) return;
    api.projects(form.client_id).then((p) => {
      setProjects(p);
      setForm((f) => (f.project_id && p.some((x) => x.id === f.project_id) ? f : { ...f, project_id: p[0]?.id ?? null }));
    }).catch(() => setProjects([]));
  }, [form.client_id]);
  useEffect(() => { if (ctx.clientId && !active) setForm((f) => (f.client_id ? f : { ...f, client_id: ctx.clientId })); }, [ctx.clientId, active]);
  // first run: preselect the virtual cable and the first voice when the machine has them
  useEffect(() => {
    if (!options) return;
    setForm((f) => ({
      ...f,
      agent_output_device: f.agent_output_device || (options.speakers.find((s) => s.includes("CABLE Input")) ?? ""),
      agent_voice: f.agent_voice || (options.voices.find((v) => v.includes("Zira")) ?? options.voices[0] ?? ""),
    }));
  }, [options]);
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [status.log?.length]);

  const createClient = async () => {
    setCreating(true);
    try {
      const c = await api.createClient(nc.name.trim());
      toast("good", `Client "${c.name}" registered`);
      setClients(await api.clients());
      set("client_id", c.id);
      ctx.setClient(c.id);
      setNewClient(false);
      setNp({ name: nc.name.trim(), key: suggestKey(nc.name), keyTouched: false, inJira: caps?.can_create_projects ?? false });
      setNc({ name: "" });
      setNewProject(true); // a client needs a project to deliver anything: offer it right away
    } catch (e) { toast("bad", (e as Error).message); } finally { setCreating(false); }
  };
  const createProject = async () => {
    setCreating(true);
    try {
      const p = await api.provisionProject(form.client_id, np.name.trim(), np.inJira ? np.key : null, np.inJira);
      toast("good", p.jira_provisioned ? `Jira project ${p.jira_project_key} created and registered` : `Project "${p.name}" registered`);
      setProjects(await api.projects(form.client_id));
      set("project_id", p.id);
      setNewProject(false);
    } catch (e) { toast("bad", (e as Error).message); } finally { setCreating(false); }
  };
  const problems = useMemo(() => validateForm(form, !!form.project_id), [form]);
  const projectKey = projects.find((p) => p.id === form.project_id)?.jira_project_key;
  const workerMode = ctx.control?.autonomy_mode ?? "?";

  const start = async () => {
    setBusy(true);
    try {
      await api.captureStart({ ...form, project_id: form.project_id || null, title: form.title.trim() });
      setSaved(JSON.stringify({ ...form, title: "", project_id: null, client_id: "" }));
      toast("good", "Call capture started. Join the meeting if you haven't.");
      onStatus();
    } catch (e) {
      toast("bad", (e as Error).message);
    } finally { setBusy(false); }
  };
  const stop = async () => {
    setConfirmStop(false);
    try { await api.captureStop(); toast("info", "Ending the call and starting the analysis..."); onStatus(); }
    catch (e) { toast("bad", (e as Error).message); }
  };
  const runTest = async (kind: "voice" | "audio") => {
    setTest((t) => ({ ...t, busy: kind }));
    try {
      if (kind === "voice") { await api.testVoice(form.agent_output_device, form.agent_voice); setTest((t) => ({ ...t, voice: "played", busy: undefined })); }
      else { const r = await api.testAudio(form.loopback_device); setTest((t) => ({ ...t, audio: { peak: r.peak, hearing: r.hearing_sound }, busy: undefined })); }
    } catch (e) { setTest((t) => ({ ...t, [kind]: (e as Error).message, busy: undefined })); toast("bad", (e as Error).message); }
  };

  const liveRow: ConversationRow | null = status.meeting_id
    ? { meeting_id: status.meeting_id, title: status.title ?? "Live call", status: active ? "live" : "analyzed", source: "local_capture", mode: status.mode ?? "interview", started_at: status.started_at ?? new Date().toISOString(), ended_at: status.ended_at ?? null, messages: 0, agent_messages: 0, other_messages: 0, first_at: null, last_at: null }
    : null;
  const live = usePolling<LiveState>(() => api.liveState(status.client_id as string, status.meeting_id as string), [status.meeting_id], 2500, active && !!status.meeting_id && status.mode === "interview");

  const sel = (label: string, value: string, onChange: (v: string) => void, items: string[], blank: string) => (
    <label className="field"><span>{label}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{blank}</option>
        {items.map((i) => <option key={i} value={i}>{i}</option>)}
      </select>
    </label>
  );

  /* ---------------------------------------------------------------- running view */
  if (active || status.state === "ended") {
    const ended = status.state === "ended";
    return (
      <div className="page">
        <Card>
          <div className="row wrap">
            <div className={`rec ${ended ? "off" : ""}`}><span className="rec-dot" />{status.state === "stopping" ? "ENDING" : ended ? (status.exit_code === 0 ? "ENDED" : "STOPPED") : "ON AIR"}</div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div style={{ fontWeight: 700, fontSize: 17 }}>{status.title}</div>
              <div className="muted" style={{ fontSize: 12.5 }}>
                {pretty(status.mode ?? "")} · started {status.started_at ? new Date(status.started_at).toLocaleTimeString() : ""} by {status.started_by}
                {status.autopilot ? " · autopilot ON" : ""}
              </div>
            </div>
            <div className="timer">{ended ? elapsed(status.started_at, new Date(status.ended_at ?? now).getTime()) : elapsed(status.started_at, now)}</div>
            {!ended && <button className="btn bad" disabled={status.state === "stopping"} onClick={() => setConfirmStop(true)}><Icon name="x" size={16} /> End call &amp; analyze</button>}
            {ended && <button className="btn primary" onClick={() => { setForm((f) => ({ ...f, title: "" })); onDismiss(); }}><Icon name="plus" size={16} /> New capture</button>}
          </div>
          {ended && (
            <div className={status.exit_code === 0 ? "allgood" : "err"} style={{ marginTop: 12 }}>
              {status.exit_code === 0
                ? "The call ended cleanly. The analysis is running now; requirements, questions and (with autopilot) Jira issues appear in the other screens in a minute or two."
                : `The capture program stopped with exit code ${status.exit_code}. Check the log below.`}
            </div>
          )}
          <div className="row wrap" style={{ marginTop: 12 }}>
            {status.meeting_id && <button className="btn" onClick={() => { window.location.hash = `#/conversations/${status.meeting_id}`; }}><Icon name="chat" size={15} /> Open conversation</button>}
            <button className="btn" onClick={() => ctx.goto("meetings")}><Icon name="mic" size={15} /> Meetings</button>
            <button className="btn" onClick={() => ctx.goto("requirements")}><Icon name="list" size={15} /> Requirements</button>
          </div>
        </Card>

        <div className="grid g21">
          <Card flush className="live-chat">
            {liveRow ? <div style={{ height: 460, display: "flex" }}><ChatPane ctx={{ ...ctx, clientId: status.client_id ?? ctx.clientId }} row={liveRow} /></div> : <div className="empty"><Skeleton h={120} /><div style={{ marginTop: 8 }}>Starting up (loading the speech model takes about 10 seconds)...</div></div>}
          </Card>
          <div className="stack">
            {status.mode === "interview" && (
              <Card title="Interview coverage" icon="spark">
                {live.data ? (
                  <div className="stack">
                    <div className="row" style={{ gap: 16 }}>
                      <Ring value={live.data.coverage} />
                      <div className="muted">{live.data.covered.length} of {live.data.covered.length + live.data.remaining.length} topics · {live.data.turns} agent turns{live.data.reply_wait_s != null ? ` · last reply ready ${live.data.reply_wait_s}s after you stopped` : ""}</div>
                    </div>
                    <div className="row wrap">
                      {live.data.covered.map((t) => <Badge key={t} tone="good">{pretty(t)}</Badge>)}
                      {live.data.remaining.map((t) => <Badge key={t} tone="muted">{pretty(t)}</Badge>)}
                    </div>
                    {live.data.degraded_reason && <div className="err">{live.data.degraded_reason}</div>}
                  </div>
                ) : <Skeleton h={90} />}
              </Card>
            )}
            <Card title="Technical log" icon="file">
              <div className="termlog" ref={logRef}>
                {(status.log ?? []).filter((l) => l.trim() && !l.startsWith("=") && !l.includes("Raw audio") ).map((l, i) => <div key={i} className={logTone(l)}>{l}</div>)}
              </div>
            </Card>
          </div>
        </div>
        <Modal open={confirmStop} onClose={() => setConfirmStop(false)} title="End the call?">
          <div className="stack">
            <div className="muted">The agent stops listening and speaking, the meeting is closed, and the analysis starts. {status.autopilot ? "Autopilot will then create the Epic and Stories in Jira." : ""}</div>
            <div className="row"><button className="btn ghost" onClick={() => setConfirmStop(false)}>Keep going</button><button className="btn bad right" onClick={stop}>End call &amp; analyze</button></div>
          </div>
        </Modal>
      </div>
    );
  }

  /* ---------------------------------------------------------------- setup form */
  return (
    <div className="page">
      <div className="grid g21">
        <div className="stack">
          <Card title="1. Who is this call with?" icon="people">
            <div className="grid g2">
              <label className="field"><span>Client</span>
                <select className="select" value={form.client_id} onChange={(e) => { if (e.target.value === NEW) setNewClient(true); else { set("client_id", e.target.value); ctx.setClient(e.target.value); } }} aria-label="client">
                  {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  <option value={NEW}>+ New client...</option>
                </select>
              </label>
              <label className="field"><span>Project (decides the Jira project)</span>
                <select className="select" value={form.project_id ?? ""} onChange={(e) => { if (e.target.value === NEW) { const n = clients.find((c) => c.id === form.client_id)?.name ?? ""; setNp({ name: n, key: suggestKey(n), keyTouched: false, inJira: caps?.can_create_projects ?? false }); setNewProject(true); } else set("project_id", e.target.value || null); }} aria-label="project" disabled={!form.client_id}>
                  <option value="">No project</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.jira_project_key ? ` (Jira ${p.jira_project_key})` : ""}</option>)}
                  <option value={NEW}>+ New project (and Jira project)...</option>
                </select>
              </label>
            </div>
            <label className="field" style={{ marginTop: 12 }}><span>Call title</span><input className="input" aria-label="call title" placeholder={`e.g. School portal kickoff, ${new Date().toLocaleDateString()}`} value={form.title} onChange={(e) => set("title", e.target.value)} /></label>
          </Card>

          <Modal open={newClient} onClose={() => setNewClient(false)} title="Register a new client">
            <div className="stack">
              <p className="muted" style={{ margin: 0 }}>A client is a separate workspace: its own requirements, conversations and Jira project. Next you can create its project.</p>
              <label className="field"><span>Client name</span><input className="input" autoFocus aria-label="new client name" value={nc.name} onChange={(e) => setNc({ name: e.target.value })} /></label>
              <div className="row"><button className="btn ghost" onClick={() => setNewClient(false)}>Cancel</button>
                <button className="btn primary right" disabled={!nc.name.trim() || creating} onClick={() => void createClient()}>{creating ? "Creating..." : "Register client"}</button></div>
            </div>
          </Modal>
          <Modal open={newProject} onClose={() => setNewProject(false)} title="New project">
            <div className="stack">
              <label className="field"><span>Project name</span><input className="input" autoFocus aria-label="new project name" value={np.name} onChange={(e) => setNp((x) => ({ ...x, name: e.target.value, key: x.keyTouched ? x.key : suggestKey(e.target.value) }))} /></label>
              <label className="check"><input type="checkbox" aria-label="create in jira" checked={np.inJira} disabled={!caps?.can_create_projects} onChange={(e) => setNp((x) => ({ ...x, inJira: e.target.checked }))} /> Also create this project in Jira (Scrum, with a backlog) and put all Epics and Stories there</label>
              {caps && !caps.can_create_projects && <div className="err">Jira project creation is not available: {caps.reason || "unknown"}. The project is registered here only; link an existing Jira key via the CLI, or give the API token administrator rights.</div>}
              {np.inJira && (
                <label className="field"><span>Jira key (2-10 letters/digits, unique in Jira)</span>
                  <input className="input" aria-label="jira key" value={np.key} onChange={(e) => setNp((x) => ({ ...x, key: e.target.value.toUpperCase(), keyTouched: true }))} />
                  {np.key && !validKey(np.key) && <small className="bad-text">Start with a letter; letters and digits only.</small>}
                </label>
              )}
              <div className="row"><button className="btn ghost" onClick={() => setNewProject(false)}>Cancel</button>
                <button className="btn primary right" disabled={!np.name.trim() || creating || (np.inJira && !validKey(np.key))} onClick={() => void createProject()}>{creating ? "Creating..." : np.inJira ? "Create in Jira and register" : "Register project"}</button></div>
            </div>
          </Modal>

          <Card title="2. What should the agent do?" icon="spark">
            <div className="seg" role="radiogroup" aria-label="mode">
              <button role="radio" aria-checked={form.mode === "interview"} className={form.mode === "interview" ? "on" : ""} onClick={() => set("mode", "interview")}>
                <Icon name="mic" /> <b>Interview</b><small>The agent speaks: asks plain questions, confirms back, closes by itself</small>
              </button>
              <button role="radio" aria-checked={form.mode === "copilot"} className={form.mode === "copilot" ? "on" : ""} onClick={() => { set("mode", "copilot"); set("autopilot", false); }}>
                <Icon name="help" /> <b>Listen only</b><small>The agent stays silent and suggests questions to you on screen</small>
              </button>
            </div>
            <div className="row" style={{ marginTop: 14, alignItems: "flex-start" }}>
              <Switch label="autopilot" on={form.autopilot} onChange={(v) => set("autopilot", v)} />
              <div>
                <div style={{ fontWeight: 650 }}>Autopilot: create the Epic and Stories in Jira after the call</div>
                <div className="muted" style={{ fontSize: 12.5 }}>
                  {form.autopilot
                    ? workerMode === "autonomous" || workerMode === "?" ? `No approval step. Issues are created in ${projectKey ?? "the linked Jira project"} by the worker.` : `Worker mode here is "${pretty(workerMode)}": actions are recorded${workerMode === "propose" ? " and wait for your approval" : " but nothing is written to Jira"}.`
                    : "Off: requirements are stored and analyzed; nothing is sent to Jira."}
                </div>
              </div>
            </div>
          </Card>

          <Card title="3. Who is the other person?" icon="user">
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>The system cannot recognize voices. You declare who is speaking; it is stored as "declared by operator", never as verified.</div>
            <div className="grid g2">
              <label className="field"><span>Name</span><input className="input" aria-label="remote name" placeholder="e.g. Zain" value={form.remote_name} onChange={(e) => set("remote_name", e.target.value)} /></label>
              <label className="field"><span>Role</span><input className="input" placeholder="e.g. Client sponsor" value={form.remote_role} onChange={(e) => set("remote_role", e.target.value)} /></label>
            </div>
            <div className="row" style={{ marginTop: 10 }}><Switch label="can confirm requirements" on={form.remote_authority} onChange={(v) => set("remote_authority", v)} /> <span>Their words can confirm requirements (decision authority)</span></div>
          </Card>

          <Card title="4. Consent" icon="shield">
            <label className="field"><span>Who agreed to being transcribed, and how? (required, stored with the call)</span>
              <textarea className="textarea" aria-label="consent basis" value={form.consent_basis} onChange={(e) => set("consent_basis", e.target.value)} />
            </label>
            <div className="row wrap" style={{ marginTop: 8 }}>{CONSENT_PRESETS.map((c) => <button key={c} className="chip" onClick={() => set("consent_basis", c)}>{c.length > 44 ? c.slice(0, 42) + "..." : c}</button>)}</div>
          </Card>

          <Card title="5. Audio" icon="radio">
            {options && !options.available && <div className="err" style={{ marginBottom: 10 }}>Audio devices are unavailable: {options.reason}. Install the capture extras: <span className="kbd">uv sync --extra capture</span></div>}
            <div className="stack">
              <div className="row"><Switch label="system audio" on={form.use_loopback} onChange={(v) => set("use_loopback", v)} /> <b>Listen to the call</b> <span className="muted">(what this computer plays: the other person via Teams/Zoom/Meet)</span></div>
              {form.use_loopback && sel("Device to listen to", form.loopback_device, (v) => set("loopback_device", v), options?.loopback ?? [], `Default speakers${options?.default_speaker ? ` (${options.default_speaker})` : ""}`)}
              <div className="row"><Switch label="microphone" on={form.use_mic} onChange={(v) => set("use_mic", v)} /> <b>Also record this computer's microphone</b> <span className="muted">(usually off: the agent's own voice goes through the cable)</span></div>
              {form.use_mic && sel("Microphone", form.mic_device, (v) => set("mic_device", v), options?.microphones ?? [], "Default microphone")}
              {form.mode === "interview" && (
                <div className="grid g2">
                  {sel("Where the agent's voice plays", form.agent_output_device, (v) => set("agent_output_device", v), options?.speakers ?? [], "Default speakers (you hear it, the call does not)")}
                  {sel("Voice", form.agent_voice, (v) => set("agent_voice", v), options?.voices ?? [], "Windows default voice")}
                </div>
              )}
              {form.mode === "interview" && options && !options.cable_found && <div className="muted" style={{ fontSize: 12.5 }}>Virtual cable not found: the agent can only speak through the speakers. For a real call install VB-Audio Virtual Cable (see the README).</div>}
              <div className="row wrap">
                {form.mode === "interview" && <button className="btn sm" disabled={!!test.busy || !options?.available} onClick={() => runTest("voice")}><Icon name="mic" size={14} /> {test.busy === "voice" ? "Speaking..." : "Test the agent's voice"}</button>}
                <button className="btn sm" disabled={!!test.busy || !options?.available || !form.use_loopback} onClick={() => runTest("audio")}><Icon name="radio" size={14} /> {test.busy === "audio" ? "Listening 2 s..." : "Test: can I hear the call?"}</button>
                {typeof test.audio === "object" && <Badge tone={test.audio.hearing ? "good" : "warn"}>{test.audio.hearing ? `hearing sound (level ${test.audio.peak})` : "silence: play something, or check Teams' speaker device"}</Badge>}
                {test.voice === "played" && <Badge tone="info">played: did it reach the call?</Badge>}
              </div>
            </div>
          </Card>

          <Card title="Advanced" icon="gear">
            <div className="grid g2">
              <label className="field"><span>Stop automatically after (minutes, 0 = never)</span><input className="input" type="number" min={0} max={480} value={form.duration_min} onChange={(e) => set("duration_min", Math.max(0, Number(e.target.value) || 0))} /></label>
              <label className="field"><span>Speech recognition model</span>
                <select className="select" value={form.model} onChange={(e) => set("model", e.target.value)}>
                  <option value="tiny.en">tiny (fastest, least accurate)</option><option value="base.en">base</option><option value="small.en">small (recommended)</option><option value="medium.en">medium (slow on CPU)</option>
                </select>
              </label>
            </div>
          </Card>
        </div>

        <div className="stack sticky">
          <Card title="Ready?" icon="check">
            <ul className="checks">
              <li className={options?.available ? "ok" : "no"}>{options?.available ? "Audio devices detected" : "Audio devices not available"}</li>
              <li className={form.mode !== "interview" || options?.cable_found ? "ok" : "warn"}>{options?.cable_found ? "Virtual cable found (agent can speak into the call)" : form.mode === "interview" ? "No virtual cable: the agent will only speak through your speakers" : "Cable not needed in listen-only mode"}</li>
              <li className={form.project_id ? "ok" : "warn"}>{form.project_id ? `Project linked${projectKey ? ` to Jira ${projectKey}` : ""}` : "No project: nothing can be delivered to Jira"}</li>
              <li className={problems.length === 0 ? "ok" : "no"}>{problems.length === 0 ? "Form is complete" : `${problems.length} thing(s) to fix`}</li>
            </ul>
            {problems.length > 0 && <div className="stack" style={{ marginTop: 8 }}>{problems.map((p) => <div key={p} className="err">{p}</div>)}</div>}
            <button className="btn primary start" disabled={problems.length > 0 || busy} onClick={start}><Icon name="radio" size={18} /> {busy ? "Starting..." : "Start capture"}</button>
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>Join the meeting first (Teams on this computer), then start. The agent greets within about 10 seconds.</div>
          </Card>
          <Card title="In Teams, before you start" icon="help">
            <ol className="steps">
              <li>Microphone: <b>CABLE Output (VB-Audio Virtual Cable)</b></li>
              <li>Speaker: <b>{options?.default_speaker ?? "the default speakers"}</b> (not the cable)</li>
              <li>Unmute here and on the other person's device; they should wear headphones</li>
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}
