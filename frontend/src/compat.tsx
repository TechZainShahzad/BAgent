import type { JobRow } from "./api";
import { Badge, Card } from "./ui";

/** Compact, standalone jobs list: failed jobs are always surfaced, never hidden. */
export function JobPanel({ jobs }: { jobs: JobRow[] }) {
  const failed = jobs.filter((j) => j.status === "failed");
  return (
    <Card title="Jobs">
      {failed.length > 0 && (
        <div role="alert" className="err" style={{ marginBottom: 10 }}>
          {failed.length} failed job(s). Failures are never hidden.
        </div>
      )}
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {jobs.map((j) => (
          <li key={j.id}>
            {j.kind} <Badge tone={j.status === "failed" ? "bad" : j.status === "succeeded" ? "good" : "info"}>{j.status}</Badge> (attempts {j.attempts})
            {j.last_error ? <em> {j.last_error}</em> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
