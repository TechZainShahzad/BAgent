import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { pretty, type Tone } from "./lib";

/* Icons ------------------------------------------------------------------- */
const PATHS: Record<string, string> = {
  dashboard: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  bolt: "M13 2 3 14h8l-1 8 10-12h-8z",
  mic: "M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v1a7 7 0 0 1-14 0v-1M12 18v4",
  help: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01",
  people: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  plus: "M12 5v14M5 12h14",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8",
  chevron: "M9 18l6-6-6-6",
  command: "M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 0 0 0-6z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  spark: "M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z",
  layers: "M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
  radio: "M4.9 19.1a10 10 0 0 1 0-14.2M7.8 16.2a6 6 0 0 1 0-8.4M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM16.2 7.8a6 6 0 0 1 0 8.4M19.1 4.9a10 10 0 0 1 0 14.2",
  chat: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  user: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS["spark"]} />
    </svg>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/* Small components -------------------------------------------------------- */
export function Badge({ tone, children, pulse }: { tone: Tone; children: ReactNode; pulse?: boolean }) {
  return <span className={`badge ${tone}${pulse ? " pulse" : ""}`}>{children}</span>;
}

export function StatusBadge({ status, tone }: { status: string; tone: Tone }) {
  return (
    <Badge tone={tone} pulse={status === "live"}>
      {cap(pretty(status))}
    </Badge>
  );
}

export function JiraKey({ k, base }: { k: string; base?: string }) {
  const href = base ? `${base.replace(/\/$/, "")}/browse/${k}` : undefined;
  return href ? (
    <a className="jira-key" href={href} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
      {k}
    </a>
  ) : (
    <span className="jira-key">{k}</span>
  );
}

export function Card({
  title,
  icon,
  right,
  children,
  flush,
  className = "",
}: {
  title?: ReactNode;
  icon?: string;
  right?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card${flush ? " flush" : ""} ${className}`}>
      {title && (
        <h3 style={flush ? { padding: "16px 18px 0", marginBottom: 8 } : undefined}>
          {icon && <Icon name={icon} size={15} />}
          {title}
          {right && <span className="right">{right}</span>}
        </h3>
      )}
      {children}
    </section>
  );
}

export function Empty({ icon = "spark", title, hint }: { icon?: string; title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="art">
        <Icon name={icon} size={28} />
      </div>
      <div style={{ color: "var(--text)", fontWeight: 600 }}>{title}</div>
      {hint && <div style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function Skeleton({ h = 18, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}

/** Number that counts up when it changes. */
export function CountUp({ value }: { value: number }) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const start = from.current;
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / 700);
      const eased = 1 - Math.pow(1 - p, 3);
      setShown(Math.round(start + (value - start) * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{shown}</>;
}

export function Stat({ label, value, hint, icon }: { label: string; value: number; hint?: string; icon: string }) {
  return (
    <div className="card stat">
      <div className="label">
        <Icon name={icon} size={15} /> {label}
      </div>
      <div className="value">
        <CountUp value={value} />
      </div>
      {hint && <div className="hint">{hint}</div>}
      <div className="glyph">
        <Icon name={icon} size={30} />
      </div>
    </div>
  );
}

export const TONE_COLOR: Record<Tone, string> = {
  good: "var(--good)",
  warn: "var(--warn)",
  bad: "var(--bad)",
  info: "var(--info)",
  violet: "var(--violet)",
  cyan: "var(--cyan)",
  muted: "var(--faint)",
};

export function Donut({
  parts,
  center,
  sub,
}: {
  parts: { label: string; value: number; tone: Tone }[];
  center: string | number;
  sub: string;
}) {
  const [hot, setHot] = useState<string | null>(null);
  const total = parts.reduce((a, p) => a + p.value, 0);
  const r = 52;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="row" style={{ gap: 26, flexWrap: "wrap" }}>
      <svg width="150" height="150" viewBox="0 0 140 140" role="img" aria-label="status distribution">
        <circle cx="70" cy="70" r={r} fill="none" stroke="var(--panel-2)" strokeWidth="16" />
        {total > 0 &&
          parts
            .filter((p) => p.value > 0)
            .map((p) => {
              const len = (p.value / total) * c;
              const el = (
                <circle
                  key={p.label}
                  cx="70"
                  cy="70"
                  r={r}
                  fill="none"
                  stroke={TONE_COLOR[p.tone]}
                  strokeWidth={hot === p.label ? 20 : 16}
                  strokeDasharray={`${Math.max(0, len - 2)} ${c}`}
                  strokeDashoffset={-offset}
                  transform="rotate(-90 70 70)"
                  style={{ transition: "stroke-width .15s, stroke-dasharray .6s" }}
                  onMouseEnter={() => setHot(p.label)}
                  onMouseLeave={() => setHot(null)}
                />
              );
              offset += len;
              return el;
            })}
        <text x="70" y="68" textAnchor="middle" fill="var(--text)" fontSize="26" fontWeight="750">
          {hot ? (parts.find((p) => p.label === hot)?.value ?? center) : center}
        </text>
        <text x="70" y="86" textAnchor="middle" fill="var(--muted)" fontSize="9.5">
          {hot ? pretty(hot) : sub}
        </text>
      </svg>
      <div className="legend" style={{ flex: 1, minWidth: 150 }}>
        {parts.map((p) => (
          <div
            key={p.label}
            className={`item${hot === p.label ? " hot" : ""}`}
            onMouseEnter={() => setHot(p.label)}
            onMouseLeave={() => setHot(null)}
          >
            <span className="dot" style={{ background: TONE_COLOR[p.tone] }} />
            <span>{cap(pretty(p.label))}</span>
            <span className="muted right">{p.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Ring({ value, size = 92, label }: { value: number; size?: number; label?: string }) {
  const r = size / 2 - 8;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label ?? "progress"}>
      <defs>
        <linearGradient id="ringgrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#7c5cff" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--panel-2)" strokeWidth="8" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="url(#ringgrad)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeDasharray={`${c * Math.min(1, value)} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray .7s" }}
      />
      <text x="50%" y="52%" textAnchor="middle" fill="var(--text)" fontSize={size / 4.2} fontWeight="700">
        {Math.round(value * 100)}%
      </text>
    </svg>
  );
}

export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: string; label: ReactNode }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          className={`tab${value === t.id ? " on" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="details">
        <header>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{title}</div>
            {subtitle && <div style={{ marginTop: 6 }}>{subtitle}</div>}
          </div>
          <button className="btn icon ghost" onClick={onClose} aria-label="close">
            <Icon name="x" />
          </button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </aside>
    </>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" style={{ zIndex: 50 }} onClick={onClose} />
      <div className="modal" role="dialog" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </>
  );
}

/* Toasts ------------------------------------------------------------------ */
type ToastKind = "good" | "bad" | "info";
interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}
const ToastCtx = createContext<(kind: ToastKind, text: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = (kind: ToastKind, text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, kind, text }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "bad" ? 7000 : 3800);
  };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role="status">
            <Icon name={t.kind === "good" ? "check" : t.kind === "bad" ? "alert" : "spark"} />
            <div>{t.text}</div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function Switch({
  on,
  onChange,
  label,
  danger,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label: string;
  danger?: boolean;
}) {
  return (
    <button className={`switch${on ? " on" : ""}${danger ? " danger" : ""}`} role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} />
  );
}
