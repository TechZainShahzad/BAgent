import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { JobPanel } from "./App";
import type { RequirementRow } from "./api";
import { attentionItems, countBy, filterRequirements, fmtTime, parseMarkdown, toneFor } from "./lib";
import { Donut, Drawer, Switch, ToastProvider, useToast } from "./ui";

test("a failed job is shown with its error and an alert", () => {
  render(
    <JobPanel
      jobs={[
        { id: "1", kind: "ingest", status: "failed", attempts: 3, last_error: "RuntimeError: provider down" },
        { id: "2", kind: "ingest", status: "succeeded", attempts: 1, last_error: null },
      ]}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("1 failed job");
  expect(screen.getByText(/provider down/)).toBeInTheDocument();
});

test("no alert when nothing failed", () => {
  render(<JobPanel jobs={[{ id: "2", kind: "x", status: "succeeded", attempts: 1, last_error: null }]} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

const R = (id: string, title: string, status: string, kind = "functional"): RequirementRow => ({ id, title, status, kind, explicit: true, version: 1 });
const rows = [R("1", "Parents see grades", "confirmed"), R("2", "Budget is modest", "extracted", "constraint"), R("3", "Pay fees online", "ready_for_delivery")];

test("requirement filtering combines search, status and kind", () => {
  expect(filterRequirements(rows, "", new Set(), new Set())).toHaveLength(3);
  expect(filterRequirements(rows, "fees", new Set(), new Set()).map((r) => r.id)).toEqual(["3"]);
  expect(filterRequirements(rows, "", new Set(["extracted"]), new Set()).map((r) => r.id)).toEqual(["2"]);
  expect(filterRequirements(rows, "", new Set(), new Set(["constraint"])).map((r) => r.id)).toEqual(["2"]);
  expect(filterRequirements(rows, "grades", new Set(["extracted"]), new Set())).toHaveLength(0);
  expect(countBy(rows, (r) => r.status)).toEqual({ confirmed: 1, extracted: 1, ready_for_delivery: 1 });
});

test("status colours: failures are red, success green, waiting amber", () => {
  expect(toneFor("intent", "failed")).toBe("bad");
  expect(toneFor("intent", "succeeded")).toBe("good");
  expect(toneFor("intent", "awaiting_approval")).toBe("warn");
  expect(toneFor("meeting", "degraded")).toBe("warn");
  expect(toneFor("req", "confirmed")).toBe("violet");
  expect(toneFor("req", "nonsense")).toBe("muted");
});

test("attention list ranks failures first and never hides a broken audit chain", () => {
  const items = attentionItems({
    jobs: [{ status: "failed" }],
    intents: [{ status: "awaiting_approval" }, { status: "failed" }],
    meetings: [{ status: "degraded", failure_reason: null }],
    questions: [{ blocking: true, status: "open" }],
    conflicts: [{ status: "open", relation: "contradiction" }],
    auditOk: false,
  });
  expect(items[0]?.text).toMatch(/Audit chain/);
  expect(items.filter((i) => i.level === "bad").length).toBeGreaterThanOrEqual(3);
  expect(items.some((i) => /waiting for approval/.test(i.text))).toBe(true);
  expect(attentionItems({ jobs: [], intents: [], meetings: [], questions: [], conflicts: [], auditOk: true })).toEqual([]);
});

test("markdown for minutes is parsed to inert blocks (no HTML injection)", () => {
  const blocks = parseMarkdown("# T\n> note\n## H\n- a\n  - b\n<script>alert(1)</script>");
  expect(blocks.map((b) => b.type)).toEqual(["h1", "quote", "h2", "li", "li", "p"]);
  expect(blocks[4]).toMatchObject({ type: "li", depth: 1 });
  expect(fmtTime(65)).toBe("1:05");
  expect(fmtTime(null)).toBe("");
});

test("drawer shows content, closes on Escape and on the close button", () => {
  const onClose = vi.fn();
  render(<Drawer open onClose={onClose} title="Reset by email" subtitle="confirmed"><p>evidence here</p></Drawer>);
  expect(screen.getByText("evidence here")).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByLabelText("close"));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test("kill-switch style toggle is an accessible switch", () => {
  const onChange = vi.fn();
  render(<Switch on={false} onChange={onChange} label="kill switch" />);
  const sw = screen.getByRole("switch", { name: "kill switch" });
  expect(sw).toHaveAttribute("aria-checked", "false");
  fireEvent.click(sw);
  expect(onChange).toHaveBeenCalledWith(true);
});

test("donut shows the total and lists every status with its count", () => {
  render(<Donut center={5} sub="requirements" parts={[{ label: "confirmed", value: 3, tone: "violet" }, { label: "extracted", value: 2, tone: "info" }]} />);
  expect(screen.getAllByText("5").length).toBeGreaterThan(0);
  expect(screen.getByText("Confirmed")).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
});

function Pusher() {
  const toast = useToast();
  return <button onClick={() => toast("bad", "Refused: needs a verified stakeholder")}>go</button>;
}

test("a refusal surfaces as a toast with the exact reason", () => {
  render(<ToastProvider><Pusher /></ToastProvider>);
  fireEvent.click(screen.getByText("go"));
  expect(screen.getByRole("status")).toHaveTextContent("Refused: needs a verified stakeholder");
});

import type { ChatMessage } from "./api";
import { bubbleSide, dayLabel, groupChat, initials, splitHighlight, toTranscriptText } from "./lib";

const M = (idx: number, role: ChatMessage["role"], speaker: string | null, text: string, said_at: string): ChatMessage => ({
  idx, role, speaker, text, channel: role === "agent" ? "agent" : "loopback", start_s: null, said_at,
});

test("chat: the BA is on the right, everyone else on the left", () => {
  expect(bubbleSide("agent")).toBe("right");
  expect(bubbleSide("client")).toBe("left");
  expect(bubbleSide("participant")).toBe("left");
  expect(initials("Mrs Okafor")).toBe("MO");
  expect(initials("Zain")).toBe("Z");
  expect(initials(null)).toBe("?");
});

test("chat: messages group by day and consecutive same-sender messages share a header", () => {
  const msgs = [
    M(0, "agent", "BA assistant", "Hello", "2026-05-04T09:30:00"),
    M(1, "client", "Zain", "Hi", "2026-05-04T09:30:20"),
    M(2, "client", "Zain", "I want a portal", "2026-05-04T09:30:40"),
    M(3, "agent", "BA assistant", "Tell me more", "2026-05-05T10:00:00"),
  ];
  const groups = groupChat(msgs, new Date("2026-05-05T12:00:00"));
  expect(groups.map((g) => g.label)).toEqual(["Yesterday", "Today"]);
  expect(groups[0]?.items.map((i) => i.firstOfRun)).toEqual([true, true, false]);
  expect(groups[1]?.items[0]?.firstOfRun).toBe(true); // new day restarts the run
  expect(dayLabel("2026-05-04T09:00:00", new Date("2026-05-05T12:00:00"))).toBe("Yesterday");
});

test("chat: search highlighting is inert (no HTML) and case-insensitive", () => {
  expect(splitHighlight("Parents see Grades", "grades")).toEqual([{ text: "Parents see ", hit: false }, { text: "Grades", hit: true }]);
  expect(splitHighlight("<b>x</b>", "")).toEqual([{ text: "<b>x</b>", hit: false }]);
  expect(splitHighlight("aaa", "a").filter((p) => p.hit)).toHaveLength(3);
});

test("chat: the downloadable transcript lists who said what", () => {
  const txt = toTranscriptText("Kickoff", "2026-05-04T09:30:00", [M(0, "agent", "BA assistant", "Hello", "2026-05-04T09:30:00"), M(1, "client", "Zain", "Hi there", "2026-05-04T09:30:20")]);
  expect(txt).toContain("Kickoff");
  expect(txt).toMatch(/BA assistant: Hello/);
  expect(txt).toMatch(/Zain: Hi there/);
});

import { mergeFragments } from "./lib";

test("chat: sentence fragments from one speaker merge, the BA's turns and long gaps do not", () => {
  const frag = [
    M(1, "client", "Zain", "Well, we would like a website where parents can see how their children are", "2026-05-04T09:30:00"),
    M(2, "client", "Zain", "doing.", "2026-05-04T09:30:03"),
    M(3, "client", "Zain", "Right now it is all paper.", "2026-05-04T09:30:05"),
    M(4, "agent", "BA assistant", "Got it.", "2026-05-04T09:30:06"),
    M(5, "agent", "BA assistant", "Second agent turn.", "2026-05-04T09:30:07"),
    M(6, "client", "Zain", "And", "2026-05-04T09:31:00"),
    M(7, "client", "Zain", "fees too", "2026-05-04T09:31:30"),
  ];
  const merged = mergeFragments(frag);
  expect(merged.map((m) => m.text)).toEqual([
    "Well, we would like a website where parents can see how their children are doing.",
    "Right now it is all paper.", // previous sentence ended, so a new bubble
    "Got it.",
    "Second agent turn.",
    "And",
    "fees too", // gap too long to merge
  ]);
});

import type { CaptureForm } from "./api";
import { elapsed, suggestKey, validKey, validateForm } from "./capture";

const FORM: CaptureForm = {
  client_id: "c1", project_id: "p1", title: "Kickoff", consent_basis: "Operator consents", mode: "interview",
  autopilot: false, remote_name: "", remote_role: "", remote_authority: true, use_loopback: true, loopback_device: "",
  use_mic: false, mic_device: "", agent_output_device: "", agent_voice: "", duration_min: 0, model: "small.en",
};

test("capture form: a complete form has no problems", () => {
  expect(validateForm(FORM, true)).toEqual([]);
});

test("capture form: missing consent, title, client and audio source are each reported", () => {
  const msgs = validateForm({ ...FORM, client_id: "", title: " ", consent_basis: "", use_loopback: false }, true);
  expect(msgs).toHaveLength(4);
  expect(msgs.join(" ")).toMatch(/client/i);
  expect(msgs.join(" ")).toMatch(/consent/i);
  expect(msgs.join(" ")).toMatch(/audio source/i);
});

test("capture form: autopilot needs interview mode, a project and a confirmable named speaker", () => {
  expect(validateForm({ ...FORM, autopilot: true }, true).join(" ")).toMatch(/remote speaker/);
  expect(validateForm({ ...FORM, autopilot: true, remote_name: "Z", remote_authority: true }, false).join(" ")).toMatch(/project/);
  expect(validateForm({ ...FORM, autopilot: true, mode: "copilot", remote_name: "Z" }, true).join(" ")).toMatch(/interview/);
  expect(validateForm({ ...FORM, autopilot: true, remote_name: "Z", remote_authority: true }, true)).toEqual([]);
});

test("capture: the on-air timer formats minutes and hours", () => {
  const t0 = "2026-05-04T09:00:00Z";
  const at = (s: number) => new Date(t0).getTime() + s * 1000;
  expect(elapsed(t0, at(0))).toBe("0:00");
  expect(elapsed(t0, at(75))).toBe("1:15");
  expect(elapsed(t0, at(3725))).toBe("1:02:05");
  expect(elapsed(undefined, at(5))).toBe("0:00");
});

it("suggests a valid Jira key from a project name", () => {
  expect(suggestKey("School Portal")).toBe("SP");
  expect(suggestKey("9 lives")).toBe("P9L");
  expect(validKey(suggestKey("Hospital Booking App"))).toBe(true);
  expect(validKey("1AB")).toBe(false);
});
