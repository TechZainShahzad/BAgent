import {
  api,
  type ConflictRow,
  type Control,
  type IntentRow,
  type JobRow,
  type MeetingRow,
  type ProjectRow,
  type QuestionRow,
  type RequirementRow,
  type StakeholderRow,
} from "./api";

export interface ClientData {
  requirements: RequirementRow[];
  intents: IntentRow[];
  meetings: MeetingRow[];
  questions: QuestionRow[];
  conflicts: ConflictRow[];
  jobs: JobRow[];
  stakeholders: StakeholderRow[];
  projects: ProjectRow[];
  auditOk: boolean | null;
  errors: string[];
}

export interface Ctx {
  clientId: string;
  setClient: (id: string) => void;
  data: ClientData;
  control: Control | null;
  refresh: () => void;
  goto: (view: string) => void;
  openRequirement: (id: string) => void;
  openMeeting: (id: string) => void;
}

const settle = async <T,>(p: Promise<T>, fallback: T, errors: string[], label: string): Promise<T> => {
  try {
    return await p;
  } catch (e) {
    errors.push(`${label}: ${(e as Error).message}`);
    return fallback;
  }
};

export async function loadClientData(id: string): Promise<ClientData> {
  const errors: string[] = [];
  const [requirements, intents, meetings, questions, conflicts, jobs, stakeholders, projects, audit] =
    await Promise.all([
      settle(api.requirements(id), [], errors, "requirements"),
      settle(api.intents(id), [], errors, "actions"),
      settle(api.meetings(id), [], errors, "meetings"),
      settle(api.questions(id), [], errors, "questions"),
      settle(api.conflicts(id), [], errors, "conflicts"),
      settle(api.jobs(id), [], errors, "jobs"),
      settle(api.stakeholders(id), [], errors, "people"),
      settle(api.projects(id), [], errors, "projects"),
      settle(api.verifyAudit(id), null, errors, "audit"),
    ]);
  return {
    requirements,
    intents,
    meetings,
    questions,
    conflicts,
    jobs,
    stakeholders,
    projects,
    auditOk: audit ? audit.valid : null,
    errors,
  };
}

export const EMPTY_DATA: ClientData = {
  requirements: [],
  intents: [],
  meetings: [],
  questions: [],
  conflicts: [],
  jobs: [],
  stakeholders: [],
  projects: [],
  auditOk: null,
  errors: [],
};

/** Distinct Jira issues the platform has created/touched for this client. */
export function jiraKeys(intents: IntentRow[]): string[] {
  return [...new Set(intents.filter((i) => i.status === "succeeded" && i.jira_key).map((i) => i.jira_key as string))];
}
