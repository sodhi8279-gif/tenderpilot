import { ReactNode, useCallback, useEffect, useRef, useState } from "react";

export function cx(...c: (string | false | null | undefined)[]) { return c.filter(Boolean).join(" "); }

export function Button({ kind = "primary", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "secondary" | "ghost" | "danger" }) {
  const k = {
    primary: "bg-teal text-white hover:bg-[#0b605d] disabled:bg-[#9cc3c1]",
    secondary: "bg-panel border border-line text-text hover:bg-[#f7f6f2] disabled:text-muted",
    ghost: "text-teal hover:bg-teal-soft",
    danger: "bg-panel border border-fail text-fail hover:bg-[#fbeceb]",
  }[kind];
  return <button {...p} className={cx("inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed", k, className)} />;
}

const STATUS: Record<string, [string, string]> = {
  pass: ["Pass", "bg-[#e5f2e8] text-pass"], fail: ["Fail", "bg-[#f9e3e1] text-fail"],
  insufficient_evidence: ["Insufficient evidence", "bg-[#fbeedb] text-gap"], ambiguous: ["Ambiguous", "bg-[#efe9f8] text-ambig"],
  not_applicable: ["Not applicable", "bg-[#eeeff1] text-na"],
  processed: ["Processed", "bg-[#e5f2e8] text-pass"], unreadable: ["Unreadable", "bg-[#f9e3e1] text-fail"],
  failed: ["Failed", "bg-[#f9e3e1] text-fail"], duplicate: ["Duplicate", "bg-[#eeeff1] text-na"], excluded: ["Excluded", "bg-[#eeeff1] text-na"],
  pending: ["Pending", "bg-[#eeeff1] text-na"], queued: ["Queued", "bg-[#eeeff1] text-na"], running: ["Running", "bg-teal-soft text-teal"],
  succeeded: ["Done", "bg-[#e5f2e8] text-pass"], cancelled: ["Cancelled", "bg-[#eeeff1] text-na"],
  needs_password: ["Needs password", "bg-[#fbeedb] text-gap"], needs_classification: ["Choose type", "bg-[#fbeedb] text-gap"],
  needs_conversion: ["Needs conversion", "bg-[#fbeedb] text-gap"], container: ["ZIP expanded", "bg-[#eeeff1] text-na"],
  rejected: ["Rejected", "bg-[#f9e3e1] text-fail"], uploaded: ["Uploaded", "bg-[#eeeff1] text-na"], processing: ["Processing", "bg-teal-soft text-teal"],
  unreviewed: ["Unreviewed", "bg-[#fbeedb] text-gap"], needs_review: ["Needs review", "bg-[#fbeedb] text-gap"], approved: ["Approved", "bg-[#e5f2e8] text-pass"],
  proposed: ["Awaiting confirmation", "bg-[#fbeedb] text-gap"], superseded: ["Superseded", "bg-[#eeeff1] text-na"],
  verified: ["Verified", "bg-[#e5f2e8] text-pass"], unverified: ["Unverified", "bg-[#fbeedb] text-gap"],
  open: ["Open", "bg-[#fbeedb] text-gap"], in_progress: ["In progress", "bg-teal-soft text-teal"], submitted: ["Submitted", "bg-teal-soft text-teal"],
  accepted: ["Accepted", "bg-[#e5f2e8] text-pass"], draft: ["Draft", "bg-[#eeeff1] text-na"],
  awaiting_signature: ["Awaiting signature", "bg-[#fbeedb] text-gap"], awaiting_certification: ["Awaiting certification", "bg-[#fbeedb] text-gap"],
  signed_upload_received: ["Signed upload received", "bg-teal-soft text-teal"], estimator_reviewed: ["Estimator reviewed", "bg-teal-soft text-teal"],
  invalidated: ["Invalidated", "bg-[#f9e3e1] text-fail"], final_ready: ["Final-ready", "bg-[#e5f2e8] text-pass"], frozen: ["Frozen", "bg-ink text-white"],
  warn: ["Check", "bg-[#fbeedb] text-gap"], done: ["Done", "bg-[#e5f2e8] text-pass"], blocked: ["Blocked", "bg-[#f9e3e1] text-fail"],
  critical: ["Critical", "bg-[#f9e3e1] text-fail"], high: ["High", "bg-[#fbeedb] text-gap"], medium: ["Medium", "bg-[#eeeff1] text-na"],
  complies: ["Complies", "bg-[#e5f2e8] text-pass"], deviation: ["Deviation", "bg-[#fbeedb] text-gap"], exclusion: ["Exclusion", "bg-[#f9e3e1] text-fail"],
  qualifies: ["Qualifies", "bg-[#e5f2e8] text-pass"], qualifies_subject_to_confirmation: ["Qualifies, confirm rules", "bg-[#e5f2e8] text-pass"],
  gaps_to_close: ["Gaps to close", "bg-[#fbeedb] text-gap"], does_not_qualify: ["Does not qualify", "bg-[#f9e3e1] text-fail"],
  confirmed: ["Confirmed", "bg-[#e5f2e8] text-pass"], suggested: ["From JV clause", "bg-teal-soft text-teal"], assumed: ["Assumed", "bg-[#fbeedb] text-gap"],
};

/** Status always carries its text; colour is secondary. */
export function Status({ s }: { s: string | null | undefined }) {
  if (!s) return null;
  const [label, cls] = STATUS[s] || [s.replace(/_/g, " "), "bg-[#eeeff1] text-na"];
  return <span className={cx("inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium", cls)}>{label}</span>;
}

export function Panel({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-[0.95rem] font-semibold">{title}</h2>
          <div className="flex flex-wrap gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "tp-input rounded-md border border-line bg-white px-2.5 py-1.5 text-sm focus:border-teal";

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{children}</div>;
}

export function Loading() { return <div className="py-8 text-center text-sm text-muted" role="status">Loading…</div>; }

export function ErrorBox({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="mb-3 flex items-start justify-between gap-3 rounded-md border border-[#e9b8b3] bg-[#fbeceb] px-3 py-2 text-sm text-fail">
      <span>{error}</span>
      {onRetry && <button className="font-medium underline" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return <div className={cx("mb-3 rounded-md px-3 py-2 text-sm", tone === "warn" ? "bg-[#fbeedb] text-[#6b3e00]" : "bg-teal-soft text-[#0b4f4c]")}>{children}</div>;
}

/** Load data with loading/error state and a reload function. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    setLoading(true);
    try { setData(await fnRef.current()); setError(null); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, deps);
  return { data, error, loading, reload, setData };
}

/** Run an action, surface its error, then refresh. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, after?: () => unknown) => {
    setBusy(true); setError(null);
    try { await fn(); await after?.(); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally { setBusy(false); }
  };
  return { busy, error, run, setError };
}

export function Confirm({ text, onConfirm, label, kind = "danger" }: { text: string; onConfirm: () => void; label: string; kind?: "danger" | "primary" }) {
  const [open, setOpen] = useState(false);
  return open ? (
    <span className="inline-flex items-center gap-2 text-sm">
      <span>{text}</span>
      <Button kind={kind} onClick={() => { setOpen(false); onConfirm(); }}>Yes, {label.toLowerCase()}</Button>
      <Button kind="secondary" onClick={() => setOpen(false)}>Cancel</Button>
    </span>
  ) : <Button kind={kind === "danger" ? "danger" : "secondary"} onClick={() => setOpen(true)}>{label}</Button>;
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b border-line">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}
          className={cx("-mb-px border-b-2 px-3 py-2 text-sm", value === k ? "border-teal font-semibold text-text" : "border-transparent text-muted hover:text-text")}>
          {label}
        </button>
      ))}
    </div>
  );
}

export const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }) + " IST" : "—");
export const fmtDay = (s?: string | null) => (s ? new Date(s + (s.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "—");
