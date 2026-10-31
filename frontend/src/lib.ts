import { useCallback, useEffect, useRef, useState } from "react";
import type { RequirementRow } from "./api";

/** Pure helpers (unit tested) ------------------------------------------------ */

export type Tone = "good" | "warn" | "bad" | "info" | "violet" | "cyan" | "muted";

const REQ_TONE: Record<string, Tone> = {
  observed: "muted",
  extracted: "info",
  under_clarification: "warn",
  confirmed: "violet",
  ready_for_delivery: "cyan",
  synced: "good",
  rejected: "bad",
  blocked: "bad",
  cancelled: "muted",
  superseded: "muted",
};
const INTENT_TONE: Record<string, Tone> = {
  succeeded: "good",
  dry_run: "muted",
  awaiting_approval: "warn",
  held: "warn",
  approved: "info",
  executing: "info",
  unknown_outcome: "warn",
  failed: "bad",
  rejected: "bad",
  cancelled: "muted",
};
const MEETING_TONE: Record<string, Tone> = {
  live: "good",
  queued: "info",
  analyzing: "info",
  analyzed: "good",
  failed: "bad",
  degraded: "warn",
  created: "muted",
};

export function toneFor(kind: "req" | "intent" | "meeting", status: string): Tone {
  const table = kind === "req" ? REQ_TONE : kind === "intent" ? INTENT_TONE : MEETING_TONE;
  return table[status] ?? "muted";
}

export const pretty = (s: string) => s.replace(/_/g, " ");

export function filterRequirements(
  rows: RequirementRow[],
  query: string,
  statuses: Set<string>,
  kinds: Set<string>,
): RequirementRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (statuses.size === 0 || statuses.has(r.status)) &&
      (kinds.size === 0 || kinds.has(r.kind)) &&
      (q === "" || r.title.toLowerCase().includes(q) || r.kind.includes(q) || r.status.includes(q)),
  );
}

export function countBy<T>(rows: T[], key: (r: T) => string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[key(r)] = (out[key(r)] ?? 0) + 1;
  return out;
}

export function fmtTime(s: number | null | undefined): string {
  if (s === null || s === undefined) return "";
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

/** Minimal, safe markdown -> blocks for the minutes viewer (no HTML injection). */
export type MdBlock =
  | { type: "h1" | "h2" | "quote" | "p"; text: string }
  | { type: "li"; text: string; depth: number };

export function parseMarkdown(md: string): MdBlock[] {
  const out: MdBlock[] = [];
  for (const raw of md.split("\n")) {
    if (!raw.trim()) continue;
    if (raw.startsWith("## ")) out.push({ type: "h2", text: raw.slice(3) });
    else if (raw.startsWith("# ")) out.push({ type: "h1", text: raw.slice(2) });
    else if (raw.startsWith("> ")) out.push({ type: "quote", text: raw.slice(2) });
    else if (/^\s*- /.test(raw)) {
      const depth = Math.floor((raw.length - raw.trimStart().length) / 2);
      out.push({ type: "li", text: raw.trim().slice(2), depth });
    } else out.push({ type: "p", text: raw.trim() });
  }
  return out;
}

export interface Attention {
  level: "bad" | "warn";
  text: string;
  go: string;
}

/** What needs a human right now, derived from live data. Order = severity. */
export function attentionItems(d: {
  jobs: { status: string }[];
  intents: { status: string }[];
  meetings: { status: string; failure_reason: string | null }[];
  questions: { blocking: boolean; status: string }[];
  conflicts: { status: string; relation: string }[];
  auditOk: boolean | null;
}): Attention[] {
  const out: Attention[] = [];
  if (d.auditOk === false)
    out.push({ level: "bad", text: "Audit chain verification FAILED", go: "system" });
  const failedJobs = d.jobs.filter((j) => j.status === "failed").length;
  if (failedJobs) out.push({ level: "bad", text: `${failedJobs} failed job(s)`, go: "system" });
  const failedIntents = d.intents.filter((i) => i.status === "failed").length;
  if (failedIntents)
    out.push({ level: "bad", text: `${failedIntents} Jira action(s) failed`, go: "actions" });
  const badMeetings = d.meetings.filter((m) => m.status === "failed" || m.status === "degraded");
  if (badMeetings.length)
    out.push({ level: "bad", text: `${badMeetings.length} meeting(s) failed or degraded`, go: "meetings" });
  const waiting = d.intents.filter((i) => i.status === "awaiting_approval").length;
  if (waiting) out.push({ level: "warn", text: `${waiting} action(s) waiting for approval`, go: "actions" });
  const held = d.intents.filter((i) => i.status === "held").length;
  if (held) out.push({ level: "warn", text: `${held} action(s) held (conflict/duplicate/kill switch)`, go: "actions" });
  const blocking = d.questions.filter((q) => q.blocking && q.status !== "answered" && q.status !== "dropped").length;
  if (blocking) out.push({ level: "warn", text: `${blocking} blocking question(s) open`, go: "insights" });
  const conflicts = d.conflicts.filter((c) => c.status === "open" && c.relation === "contradiction").length;
  if (conflicts) out.push({ level: "warn", text: `${conflicts} open contradiction(s)`, go: "insights" });
  return out;
}

/** Hooks --------------------------------------------------------------------- */

export function usePolling<T>(
  fn: () => Promise<T>,
  deps: unknown[],
  intervalMs: number,
  enabled = true,
): { data: T | null; error: string; loading: boolean; refresh: () => void; updatedAt: number } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const run = useCallback(async () => {
    try {
      const v = await fnRef.current();
      setData(v);
      setError("");
      setUpdatedAt(Date.now());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    setLoading(true);
    void run();
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void run();
    }, intervalMs);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, run, ...deps]);
  return { data, error, loading, refresh: () => void run(), updatedAt };
}

export function useLocalStorage(key: string, initial: string): [string, (v: string) => void] {
  const [v, setV] = useState(() => {
    try {
      return window.localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (next: string) => {
      setV(next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        /* storage unavailable: still works for this session */
      }
    },
    [key],
  );
  return [v, set];
}


/** Chat helpers (unit tested) -------------------------------------------------- */
import type { ChatMessage } from "./api";

/** The AI business analyst speaks on the right (like "me" in a chat app); everyone else on the left. */
export const bubbleSide = (role: string): "right" | "left" => (role === "agent" ? "right" : "left");

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const a = parts[0]?.[0] ?? "?";
  const b = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (a + b).toUpperCase();
}

export function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const diff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

export const clockTime = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

export interface ChatGroup {
  key: string;
  label: string;
  items: { msg: ChatMessage; firstOfRun: boolean }[];
}

const MERGE_GAP_MS = 8000;

/** Speech is cut at every pause; show one sentence as one bubble. The BA's turns are never merged. */
export function mergeFragments(messages: ChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    const prev = out[out.length - 1];
    const gap = prev ? new Date(m.said_at).getTime() - new Date(prev.said_at).getTime() : Infinity;
    if (prev && m.role !== "agent" && prev.role === m.role && prev.speaker === m.speaker && gap <= MERGE_GAP_MS && !/[.!?]$/.test(prev.text)) {
      out[out.length - 1] = { ...prev, text: `${prev.text} ${m.text}`, said_at: m.said_at };
    } else {
      out.push(m);
    }
  }
  return out;
}

/** Group messages by calendar day; mark the first message of each consecutive same-sender run. */
export function groupChat(rawMessages: ChatMessage[], now: Date = new Date()): ChatGroup[] {
  const messages = mergeFragments(rawMessages);
  const groups: ChatGroup[] = [];
  let prev: ChatMessage | null = null;
  for (const msg of messages) {
    const key = dayKey(msg.said_at);
    let g = groups[groups.length - 1];
    if (!g || g.key !== key) {
      g = { key, label: dayLabel(msg.said_at, now), items: [] };
      groups.push(g);
      prev = null;
    }
    const sameSender = prev !== null && prev.role === msg.role && prev.speaker === msg.speaker;
    g.items.push({ msg, firstOfRun: !sameSender });
    prev = msg;
  }
  return groups;
}

/** Split text so matches of `query` can be highlighted without injecting HTML. */
export function splitHighlight(text: string, query: string): { text: string; hit: boolean }[] {
  const q = query.trim();
  if (!q) return [{ text, hit: false }];
  const out: { text: string; hit: boolean }[] = [];
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  let i = 0;
  while (i < text.length) {
    const j = lower.indexOf(needle, i);
    if (j < 0) {
      out.push({ text: text.slice(i), hit: false });
      break;
    }
    if (j > i) out.push({ text: text.slice(i, j), hit: false });
    out.push({ text: text.slice(j, j + needle.length), hit: true });
    i = j + needle.length;
  }
  return out;
}

export function toTranscriptText(title: string, startedAt: string, messages: ChatMessage[]): string {
  const NL = "\n";
  const head = [title, new Date(startedAt).toLocaleString(), "-".repeat(40)].join(NL) + NL;
  const body = messages
    .map((m) => `[${clockTime(m.said_at)}] ${m.role === "agent" ? "BA assistant" : (m.speaker ?? "Client")}: ${m.text}`)
    .join(NL);
  return head + body + NL;
}
