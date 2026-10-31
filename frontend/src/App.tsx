import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CaptureStatus, type ClientRow, type Control } from "./api";
import { EMPTY_DATA, loadClientData, type ClientData, type Ctx } from "./data";
import { CaptureView } from "./capture";
import { HomeView } from "./home";
import { ConversationsView } from "./chat";
import { MeetingDrawer, MeetingsView, RequirementDrawer } from "./drawers";
import { attentionItems, useLocalStorage, usePolling } from "./lib";
import { ActionsView, InsightsView, OverviewView, PeopleView, RequirementsView, SystemView } from "./views";
import { Icon, Modal, Switch, ToastProvider, useToast } from "./ui";
import "./styles.css";

export { JobPanel } from "./compat";

const VIEWS = [
  { id: "home", label: "Home", icon: "dashboard" },
  { id: "overview", label: "Client overview", icon: "layers" },
  { id: "capture", label: "Live capture", icon: "radio" },
  { id: "requirements", label: "Requirements", icon: "list" },
  { id: "actions", label: "Jira actions", icon: "bolt" },
  { id: "meetings", label: "Meetings", icon: "mic" },
  { id: "conversations", label: "Conversations", icon: "chat" },
  { id: "insights", label: "Insights", icon: "help" },
  { id: "people", label: "People", icon: "people" },
  { id: "system", label: "System", icon: "gear" },
] as const;

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <form
        className="card"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await api.login(email, password);
            onDone();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="logo-lg"><Icon name="spark" size={28} /></div>
        <h1>BA Platform</h1>
        <p className="muted" style={{ marginTop: 0 }}>Your AI technical business analyst.</p>
        <div className="stack">
          <label className="field"><span>Email</span><input aria-label="email" className="input" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="field"><span>Password</span><input aria-label="password" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          {error && <div role="alert" className="err">{error === "invalid credentials" ? "Wrong email or password." : error}</div>}
          <button className="btn primary" type="submit" disabled={busy || !email || !password} style={{ justifyContent: "center" }}>{busy ? "Signing in..." : "Sign in"}</button>
        </div>
      </form>
    </div>
  );
}

function Palette({ open, onClose, items }: { open: boolean; onClose: () => void; items: { id: string; label: string; hint: string; icon: string; run: () => void }[] }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  useEffect(() => { if (open) { setQ(""); setSel(0); } }, [open]);
  const shown = useMemo(() => items.filter((i) => `${i.label} ${i.hint}`.toLowerCase().includes(q.toLowerCase())).slice(0, 30), [items, q]);
  if (!open) return null;
  const go = (i: number) => { const it = shown[i]; if (it) { onClose(); it.run(); } };
  return (
    <>
      <div className="scrim" style={{ zIndex: 59 }} onClick={onClose} />
      <div className="palette" role="dialog" aria-label="command palette">
        <input autoFocus placeholder="Jump to a screen, requirement, client or action..." value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(shown.length - 1, s + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
            else if (e.key === "Enter") go(sel);
          }} />
        <div className="res">
          {shown.length === 0 && <div className="muted" style={{ padding: 16 }}>Nothing matches.</div>}
          {shown.map((it, i) => (
            <button key={it.id} className={i === sel ? "sel" : ""} onMouseEnter={() => setSel(i)} onClick={() => go(i)}>
              <Icon name={it.icon} /> <span style={{ flex: 1 }}>{it.label}</span> <span className="faint">{it.hint}</span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const toast = useToast();
  const [theme, setTheme] = useLocalStorage("ba.theme", "dark");
  const [clientId, setClientId] = useLocalStorage("ba.client", "");
  const viewFromHash = () => window.location.hash.replace("#/", "").split("/")[0] || "home";
  const [view, setView] = useState<string>(viewFromHash);
  const [reqId, setReqId] = useState<string | null>(null);
  const [meetingId, setMeetingId] = useState<string | null>(null);
  const [palette, setPalette] = useState(false);
  const [newClient, setNewClient] = useState(false);
  const [clientName, setClientName] = useState("");

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    const h = () => setView(viewFromHash());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  const goto = useCallback((v: string) => { window.location.hash = `#/${v}`; setView(v); }, []);

  const clients = usePolling<ClientRow[]>(() => api.clients(), [], 15000);
  const control = usePolling<Control>(() => api.control(), [], 5000);
  const captureRaw = usePolling<CaptureStatus>(() => api.captureStatus(), [], 2500);
  const [dismissedRun, setDismissedRun] = useState<string | null>(null);
  const capture: CaptureStatus =
    captureRaw.data && !(captureRaw.data.state === "ended" && captureRaw.data.run_id === dismissedRun)
      ? captureRaw.data
      : { state: "idle" };
  const onAir = capture.state === "running" || capture.state === "stopping";
  const list = clients.data ?? [];
  // The list is newest-activity first. Start on the client that actually has data; never stay on a
  // stored client that no longer exists, and never sit on an empty client while another has activity.
  const initialPick = useRef(true);
  useEffect(() => {
    if (!list.length) return;
    const stored = list.find((c) => c.id === clientId);
    const empty = (c: ClientRow) => !(c.meetings || c.requirements || c.messages);
    if (!stored) setClientId(list[0]?.id ?? "");
    else if (initialPick.current && empty(stored) && !empty(list[0] as ClientRow)) setClientId(list[0]?.id ?? "");
    initialPick.current = false;
  }, [list, clientId, setClientId]);
  // a call for another client is on air (or just ended): follow it so its data is what you see
  const followed = useRef<string | null>(null);
  useEffect(() => {
    const c = capture.client_id;
    if (onAir && c && capture.run_id && followed.current !== capture.run_id) {
      followed.current = capture.run_id;
      if (c !== clientId) setClientId(c);
    }
  }, [onAir, capture.client_id, capture.run_id, clientId, setClientId]);
  const callClient = capture.state !== "idle" ? list.find((c) => c.id === capture.client_id) : undefined;
  const mismatch = callClient && callClient.id !== clientId ? callClient : undefined;

  const dataPoll = usePolling<ClientData>(() => loadClientData(clientId), [clientId], 5000, clientId !== "");
  const data = dataPoll.data ?? EMPTY_DATA;
  const ctx: Ctx = {
    clientId, setClient: setClientId, data, control: control.data, refresh: dataPoll.refresh, goto,
    openRequirement: setReqId, openMeeting: setMeetingId,
  };

  const attention = attentionItems({ jobs: data.jobs, intents: data.intents, meetings: data.meetings, questions: data.questions, conflicts: data.conflicts, auditOk: data.auditOk });
  const setKill = async (on: boolean) => {
    try { await api.setKillSwitch(on); toast(on ? "bad" : "good", on ? "Kill switch ON: all Jira writes paused" : "Writes resumed"); control.refresh(); }
    catch (e) { toast("bad", (e as Error).message); }
  };
  const fresh = Date.now() - dataPoll.updatedAt < 12000;
  const counts: Record<string, number | undefined> = {
    capture: onAir ? 1 : undefined,
    requirements: data.requirements.length || undefined,
    actions: data.intents.filter((i) => i.status === "awaiting_approval" || i.status === "held").length || undefined,
    meetings: data.meetings.filter((m) => m.status === "live").length || undefined,
    insights: (data.questions.filter((q) => q.status === "open").length + data.conflicts.filter((c) => c.status === "open").length) || undefined,
    system: data.jobs.filter((j) => j.status === "failed").length || undefined,
  };
  const paletteItems = [
    ...VIEWS.map((v) => ({ id: `v-${v.id}`, label: `Go to ${v.label}`, hint: "screen", icon: v.icon, run: () => goto(v.id) })),
    ...list.map((c) => ({ id: `c-${c.id}`, label: `Switch client: ${c.name}`, hint: "client", icon: "people", run: () => setClientId(c.id) })),
    ...data.requirements.map((r) => ({ id: `r-${r.id}`, label: r.title, hint: r.status.replace(/_/g, " "), icon: "list", run: () => setReqId(r.id) })),
    { id: "a-capture", label: "Start / view live capture", hint: "action", icon: "radio", run: () => goto("capture") },
    { id: "a-theme", label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme", hint: "action", icon: theme === "dark" ? "sun" : "moon", run: () => setTheme(theme === "dark" ? "light" : "dark") },
    { id: "a-refresh", label: "Refresh data now", hint: "action", icon: "refresh", run: dataPoll.refresh },
    { id: "a-kill", label: control.data?.kill_switch ? "Resume Jira writes" : "Pause all Jira writes (kill switch)", hint: "action", icon: "shield", run: () => setKill(!control.data?.kill_switch) },
  ];

  const current = VIEWS.find((v) => v.id === view) ?? VIEWS[0];
  const meeting = data.meetings.find((m) => m.id === meetingId) ?? null;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo"><Icon name="spark" size={19} /></div>
          <div>BA Platform<small>technical business analyst</small></div>
        </div>
        <div className="client-picker">
          <label htmlFor="client">Client</label>
          <select id="client" className="select" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {list.length === 0 && <option value="">No clients yet</option>}
            {list.map((c) => <option key={c.id} value={c.id}>{c.name}{c.meetings !== undefined ? ` (${c.meetings} calls, ${c.requirements ?? 0} reqs)` : ""}</option>)}
          </select>
          <button className="btn sm ghost" style={{ marginTop: 4 }} onClick={() => setNewClient(true)}><Icon name="plus" size={14} /> New client</button>
        </div>
        <div className="nav-label">Workspace</div>
        {VIEWS.map((v) => (
          <button key={v.id} className={`nav-item${current.id === v.id ? " active" : ""}`} onClick={() => goto(v.id)}>
            <Icon name={v.icon} /> {v.label}
            {counts[v.id] !== undefined && <span className={`count${v.id === "system" || (v.id === "actions" && counts[v.id]) ? " alert" : ""}`}>{counts[v.id]}</span>}
          </button>
        ))}
        <div className="spacer" />
        <button className="nav-item" onClick={() => setPalette(true)}><Icon name="command" /> Quick jump <span className="kbd right">Ctrl K</span></button>
        <button className="nav-item" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}><Icon name={theme === "dark" ? "sun" : "moon"} /> {theme === "dark" ? "Light" : "Dark"} theme</button>
        <button className="nav-item" onClick={async () => { try { await api.logout(); } catch { /* session may already be gone */ } onLogout(); }}><Icon name="logout" /> Sign out</button>
      </aside>

      <div className="main">
        <div className="topbar">
          <h1>{current.label}</h1>
          <span className={`live-dot${fresh ? "" : " stale"}`} title={fresh ? "Live: refreshing every 5 s" : "Not refreshing"} />
          <span className="muted" style={{ fontSize: 12 }}>{fresh ? "live" : "paused"}</span>
          <div className="grow" />
          {onAir && <button className="onair" onClick={() => goto("capture")}><span className="rec-dot" /> ON AIR</button>}
          {attention.length > 0 && <button className="btn sm" onClick={() => goto("overview")}><Icon name="alert" size={14} /> {attention.length} need attention</button>}
          <span className="badge info" style={{ textTransform: "capitalize" }}>{(control.data?.autonomy_mode ?? "...").replace(/_/g, " ")}</span>
          <div className={`killbar${control.data?.kill_switch ? " on" : ""}`}>
            <Icon name="shield" size={16} />
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{control.data?.kill_switch ? "WRITES PAUSED" : "Writes on"}</span>
            <Switch danger label="kill switch" on={control.data?.kill_switch ?? false} onChange={setKill} />
          </div>
          <button className="btn icon" onClick={dataPoll.refresh} aria-label="refresh"><Icon name="refresh" /></button>
          <button className="btn icon" onClick={() => setPalette(true)} aria-label="search"><Icon name="search" /></button>
        </div>
        <div className="content">
          {mismatch && (
            <div className="err" role="status" style={{ marginBottom: 14, display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ flex: 1 }}>The {capture.state === "ended" ? "last" : "current"} call ("{capture.title}") belongs to client <b>{mismatch.name}</b>, but you are viewing a different client.</span>
              <button className="btn sm" onClick={() => setClientId(mismatch.id)}>Switch to {mismatch.name}</button>
            </div>
          )}
          {data.errors.length > 0 && <div className="err" style={{ marginBottom: 14 }}>Some data could not load: {data.errors.join(" | ")}</div>}
          {current.id === "home" ? <HomeView clientId={clientId} goto={goto} onPickClient={(id, v) => { setClientId(id); if (v) goto(v); }} />
            : clientId === "" ? (
            <div className="page"><div className="card"><div className="empty"><div className="art"><Icon name="people" size={28} /></div><div style={{ color: "var(--text)", fontWeight: 600 }}>Create your first client</div><div style={{ margin: "6px 0 14px" }}>A client is a separate workspace for one company or product.</div><button className="btn primary" onClick={() => setNewClient(true)}><Icon name="plus" size={16} /> New client</button></div></div></div>
          ) : current.id === "overview" ? <OverviewView ctx={ctx} loading={dataPoll.loading} />
            : current.id === "requirements" ? <RequirementsView ctx={ctx} />
            : current.id === "actions" ? <ActionsView ctx={ctx} />
            : current.id === "meetings" ? <MeetingsView ctx={ctx} />
            : current.id === "capture" ? <CaptureView ctx={ctx} status={capture} onStatus={captureRaw.refresh} onDismiss={() => setDismissedRun(captureRaw.data?.run_id ?? null)} />
            : current.id === "conversations" ? <ConversationsView ctx={ctx} />
            : current.id === "insights" ? <InsightsView ctx={ctx} />
            : current.id === "people" ? <PeopleView ctx={ctx} />
            : <SystemView ctx={ctx} onKill={setKill} />}
        </div>
      </div>

      <RequirementDrawer ctx={ctx} id={reqId} onClose={() => setReqId(null)} />
      <MeetingDrawer ctx={ctx} meeting={meeting} onClose={() => setMeetingId(null)} />
      <Palette open={palette} onClose={() => setPalette(false)} items={paletteItems} />
      <Modal open={newClient} onClose={() => setNewClient(false)} title="New client">
        <div className="stack">
          <label className="field"><span>Client name</span><input className="input" autoFocus value={clientName} onChange={(e) => setClientName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && document.getElementById("create-client")?.click()} /></label>
          <div className="row">
            <button className="btn ghost" onClick={() => setNewClient(false)}>Cancel</button>
            <button id="create-client" className="btn primary right" disabled={!clientName.trim()} onClick={async () => {
              try { const c = await api.createClient(clientName.trim()); toast("good", `Client "${c.name}" created`); setClientName(""); setNewClient(false); clients.refresh(); setClientId(c.id); }
              catch (e) { toast("bad", (e as Error).message); }
            }}>Create</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    const t = (() => { try { return window.localStorage.getItem("ba.theme"); } catch { return null; } })();
    document.documentElement.dataset.theme = t ?? "dark";
  }, []);
  return <ToastProvider>{authed ? <Shell onLogout={() => setAuthed(false)} /> : <Login onDone={() => setAuthed(true)} />}</ToastProvider>;
}
