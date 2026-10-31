export interface ClientRow {
  id: string;
  name: string;
  meetings?: number;
  requirements?: number;
  messages?: number;
  jira_issues?: number;
  open_questions?: number;
  last_activity_at?: string;
}
export interface ProjectRow {
  id: string;
  name: string;
  jira_project_key: string | null;
  jira_provisioned?: boolean;
}
export interface Overview {
  totals: {
    clients: number;
    meetings: number;
    messages: number;
    requirements: number;
    jira_issues: number;
    open_questions: number;
    live_calls: number;
    failed_jobs: number;
    failed_actions: number;
    waiting_actions: number;
    open_contradictions: number;
  };
  requirements_by_status: Record<string, number>;
  clients: Required<ClientRow>[];
  recent_meetings: {
    meeting_id: string;
    client_id: string;
    client_name: string;
    title: string;
    status: string;
    mode: string;
    started_at: string;
    messages: number;
  }[];
  recent_actions: {
    client_id: string;
    client_name: string;
    kind: string;
    status: string;
    jira_key: string | null;
    summary: string | null;
    created_at: string;
  }[];
}
export interface JiraCapabilities {
  configured: boolean;
  can_create_projects: boolean;
  reason: string;
}
export interface JobRow {
  id: string;
  kind: string;
  status: "pending" | "running" | "succeeded" | "failed";
  attempts: number;
  last_error: string | null;
}
export interface RequirementRow {
  id: string;
  title: string;
  kind: string;
  status: string;
  explicit: boolean;
  version: number;
}
export interface IntentRow {
  id: string;
  kind: string;
  status: string;
  jira_key: string | null;
  project_key: string;
  summary: string | null;
  requirement_id: string | null;
  last_error: string | null;
  attempts: number;
  marker: string;
}
export interface MeetingRow {
  id: string;
  title: string;
  status: string;
  source: string;
  consent: string;
  failure_reason: string | null;
  degraded_reason?: string | null;
  transcript_purged: boolean;
}
export interface QuestionRow {
  id: string;
  question: string;
  why: string;
  blocking: boolean;
  status: string;
}
export interface ConflictRow {
  id: string;
  relation: string;
  status: string;
  similarity: number;
  explanation: string;
  requirement_id: string;
  other_requirement_id: string;
}
export interface StakeholderRow {
  id: string;
  name: string;
  role: string | null;
  has_decision_authority: boolean;
  identity_verified: boolean;
  verification_note: string | null;
}
export interface Trace {
  requirement: {
    id: string;
    title: string;
    statement: string;
    kind: string;
    status: string;
    version: number;
    explicit: boolean;
  };
  evidence: {
    quote: string;
    speaker_label: string | null;
    start_s: number | null;
    stakeholder_linked: boolean;
    attribution_confidence: number | null;
    expires_at: string;
  }[];
  versions: { version: number; title: string; statement: string; reason: string }[];
  conflicts: {
    id: string;
    relation: string;
    status: string;
    similarity: number;
    explanation: string;
    other_requirement_id: string;
  }[];
  drafts: {
    id: string;
    issue_type: string;
    summary: string;
    acceptance_criteria: string[];
    measurable: boolean;
    flags: string[];
  }[];
  policy_decisions: {
    outcome: string;
    rules: string[];
    reason: string;
    mode: string | null;
    disposition: string | null;
  }[];
  intents: {
    id: string;
    kind: string;
    status: string;
    jira_key: string | null;
    marker: string;
    last_error: string | null;
    attempts: number;
  }[];
  jira: { key: string; relation: string }[];
}
export interface Control {
  kill_switch: boolean;
  autonomy_mode: string;
  jira_base_url?: string;
  jira_allowlist?: string[];
}
export interface AutopilotReport {
  autopilot: boolean;
  status: string;
  report: {
    confirmed?: string[];
    not_confirmed?: { requirement: string; reason: string }[];
    delivered?: {
      requirement: string;
      kind: string;
      result: string;
      jira_key?: string | null;
      reasons?: string[];
      follow_up?: string;
      warning?: string;
    }[];
    note?: string;
    error?: string;
  };
}
export interface LiveState {
  status: string;
  mode: string;
  degraded_reason: string | null;
  turns: number;
  client_segments: number;
  covered: string[];
  remaining: string[];
  coverage: number;
  closed: boolean;
  pending_preview: string | null;
  reply_wait_s?: number | null;
}

export interface ConversationRow {
  meeting_id: string;
  title: string;
  status: string;
  source: string;
  mode: string;
  started_at: string;
  ended_at: string | null;
  messages: number;
  agent_messages: number;
  other_messages: number;
  first_at: string | null;
  last_at: string | null;
}
export interface ChatMessage {
  idx: number;
  role: "agent" | "client" | "operator" | "participant";
  speaker: string | null;
  text: string;
  channel: string | null;
  start_s: number | null;
  said_at: string;
}
export interface ConversationDetail {
  meeting: { id: string; title: string; status: string; source: string; mode: string; started_at: string; ended_at: string | null };
  total: number;
  messages: ChatMessage[];
}

export interface CaptureOptions {
  available: boolean;
  reason: string;
  microphones: string[];
  loopback: string[];
  speakers: string[];
  voices: string[];
  default_speaker: string | null;
  cable_found: boolean;
}
export interface CaptureForm {
  client_id: string;
  project_id: string | null;
  title: string;
  consent_basis: string;
  mode: "interview" | "copilot";
  autopilot: boolean;
  remote_name: string;
  remote_role: string;
  remote_authority: boolean;
  use_loopback: boolean;
  loopback_device: string;
  use_mic: boolean;
  mic_device: string;
  agent_output_device: string;
  agent_voice: string;
  duration_min: number;
  model: string;
}
export interface CaptureStatus {
  state: "idle" | "running" | "stopping" | "ended";
  run_id?: string;
  client_id?: string;
  project_id?: string | null;
  title?: string;
  mode?: string;
  autopilot?: boolean;
  started_at?: string;
  ended_at?: string | null;
  started_by?: string;
  meeting_id?: string | null;
  exit_code?: number | null;
  log?: string[];
}

let csrf = "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (!(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const res = await fetch(`/api${path}`, { credentials: "same-origin", ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  async login(email: string, password: string): Promise<void> {
    const r = await post<{ csrf_token: string }>("/auth/login", { email, password });
    csrf = r.csrf_token;
  },
  async logout(): Promise<void> {
    await post("/auth/logout");
    csrf = "";
  },
  clients: () => request<ClientRow[]>("/clients"),
  createClient: (name: string) => post<ClientRow>("/clients", { name }),
  projects: (id: string) => request<ProjectRow[]>(`/clients/${id}/projects`),
  createProject: (id: string, name: string, jira_project_key: string | null) =>
    post<ProjectRow>(`/clients/${id}/projects`, { name, jira_project_key }),
  overview: () => request<Overview>("/overview"),
  jiraCapabilities: () => request<JiraCapabilities>("/jira/capabilities"),
  provisionProject: (id: string, name: string, jira_key: string | null, create_in_jira: boolean) =>
    post<ProjectRow>(`/clients/${id}/projects/provision`, { name, jira_key, create_in_jira }),
  jobs: (id: string) => request<JobRow[]>(`/clients/${id}/jobs`),
  verifyAudit: (id: string) =>
    request<{ valid: boolean; first_bad_index: number | null }>(`/clients/${id}/audit/verify`),
  health: () => request<{ status: string }>("/health"),
  requirements: (id: string) => request<RequirementRow[]>(`/clients/${id}/requirements`),
  trace: (id: string, rid: string) => request<Trace>(`/clients/${id}/requirements/${rid}`),
  transition: (id: string, rid: string, target: string, reason: string) =>
    post<{ status: string }>(`/clients/${id}/requirements/${rid}/transition`, { target, reason }),
  correct: (id: string, rid: string, title: string, statement: string, reason: string) =>
    post<{ status: string; version: number }>(`/clients/${id}/requirements/${rid}/correct`, {
      title,
      statement,
      reason,
    }),
  intents: (id: string) => request<IntentRow[]>(`/clients/${id}/intents`),
  approve: (id: string, iid: string) =>
    post<{ status: string }>(`/clients/${id}/intents/${iid}/approve`),
  reject: (id: string, iid: string, reason: string) =>
    post<{ status: string }>(`/clients/${id}/intents/${iid}/reject`, { reason }),
  meetings: (id: string) => request<MeetingRow[]>(`/clients/${id}/meetings`),
  minutes: (id: string, mid: string) =>
    request<{ markdown: string }>(`/clients/${id}/meetings/${mid}/minutes`),
  autopilot: (id: string, mid: string) =>
    request<AutopilotReport>(`/clients/${id}/meetings/${mid}/autopilot`),
  liveState: (id: string, mid: string) =>
    request<LiveState>(`/clients/${id}/live-meetings/${mid}/state`),
  importTranscript: (
    id: string,
    file: File,
    title: string,
    consentBasis: string,
    projectId?: string,
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    fd.append("consent_basis", consentBasis);
    if (projectId) fd.append("project_id", projectId);
    return request<{ id: string; status: string }>(`/clients/${id}/meetings/import`, {
      method: "POST",
      body: fd,
    });
  },
  conversations: (id: string) => request<ConversationRow[]>(`/clients/${id}/conversations`),
  conversation: (id: string, mid: string, after = -1) =>
    request<ConversationDetail>(`/clients/${id}/meetings/${mid}/conversation?after=${after}`),
  questions: (id: string) => request<QuestionRow[]>(`/clients/${id}/questions`),
  conflicts: (id: string) => request<ConflictRow[]>(`/clients/${id}/conflicts`),
  stakeholders: (id: string) => request<StakeholderRow[]>(`/clients/${id}/stakeholders`),
  upsertStakeholder: (
    id: string,
    s: {
      name: string;
      role?: string | null;
      has_decision_authority: boolean;
      identity_verified: boolean;
      verification_note?: string | null;
    },
  ) => post<{ id: string }>(`/clients/${id}/stakeholders`, s),
  captureOptions: () => request<CaptureOptions>("/capture/options"),
  captureStatus: () => request<CaptureStatus>("/capture/status"),
  captureStart: (f: CaptureForm) => post<{ run_id: string }>("/capture/start", f),
  captureStop: () => post<{ stopping: boolean }>("/capture/stop"),
  testVoice: (device: string, voice: string) => post<{ ok: boolean }>("/capture/test-voice", { device, voice }),
  testAudio: (device: string) => post<{ peak: number; hearing_sound: boolean }>("/capture/test-audio", { device }),
  control: () => request<Control>("/control"),
  setKillSwitch: (on: boolean) => post<{ kill_switch: boolean }>("/control/kill-switch", { on }),
};
