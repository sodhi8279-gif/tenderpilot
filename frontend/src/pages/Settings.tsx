import { useState } from "react";
import { api } from "../api";
import { useMe } from "../App";
import SoftwareUpdate from "./SoftwareUpdate";
import { Button, cx, Empty, ErrorBox, Field, fmtDate, inputCls, Loading, Notice, Panel, useAction, useLoad } from "../ui";

const ROLES = ["org_admin", "tender_manager", "technical_reviewer", "estimator", "document_coordinator", "finance_reviewer", "authorized_approver", "auditor"];

type Check = { id: string; label: string; status: "ok" | "warning" | "error" | "off"; detail: string; action: string | null; checked_at?: string; cached?: boolean };
type Est = { cost_usd_low: string; cost_usd_high: string };
type AiView = {
  provider: string; model: string; effort: string; monthly_budget_usd: string; max_pages_per_run: number;
  key: { stored: boolean; hint: string | null; source: string | null; error: string | null; updated_at: string | null; encryption: string };
  last_test: { at: string | null; ok: boolean | null; message: string | null };
  consent: boolean; live: boolean;
  usage_this_month: { requests: number; input_tokens: number; output_tokens: number; cost_usd: string };
  providers: { id: string; label: string; cost: string; description: string }[];
  models: { id: string; label: string; input_per_mtok: string; output_per_mtok: string; note: string; estimate_100_pages: Est }[];
  efforts: string[]; price_as_of: string; price_source: string;
};
type Usage = { id: number; created_at: string; model: string; pages: string; input_tokens: number; output_tokens: number; cost_usd: string | null; outcome: string; message: string | null; accepted: number; discarded: number };

const BADGE: Record<Check["status"], [string, string]> = {
  ok: ["Working", "bg-[#e5f2e8] text-pass"], warning: ["Needs attention", "bg-[#fbeedb] text-gap"],
  error: ["Not working", "bg-[#f9e3e1] text-fail"], off: ["Off", "bg-[#eeeff1] text-na"],
};
const EFFORT_HELP: Record<string, string> = {
  low: "Fastest and cheapest. Good for clean, well-structured documents.",
  medium: "Balanced. Recommended for most tenders.",
  high: "Reads dense or contradictory clauses more carefully. Costs more, because reasoning is billed as output tokens.",
};

function SystemChecks({ admin }: { admin: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const checks = useLoad<{ checks: Check[]; generated_at: string }>(() => api(`/system/checks${refresh ? "?refresh=true" : ""}`), [refresh]);
  return (
    <Panel title="System checks" actions={admin && <Button kind="secondary" disabled={checks.loading} onClick={() => setRefresh((n) => n + 1)}>{checks.loading ? "Checking…" : "Run checks again"}</Button>}>
      <ErrorBox error={checks.error} onRetry={checks.reload} />
      {checks.loading && !checks.data ? <Loading /> : checks.data && (
        <>
          <ul className="divide-y divide-line">
            {checks.data.checks.map((c) => (
              <li key={c.id} className="grid gap-1 py-3 sm:grid-cols-[13rem_8.5rem_1fr]">
                <span className="font-medium">{c.label}</span>
                <span><span className={cx("inline-block rounded px-1.5 py-0.5 text-xs font-medium", BADGE[c.status][1])}>{BADGE[c.status][0]}</span></span>
                <div className="text-sm">
                  <p className="break-words">{c.detail}</p>
                  {c.action && <p className={cx("mt-1", c.status === "error" ? "text-fail" : "text-gap")}><span className="font-medium">What to do: </span>{c.action}</p>}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">OCR and Office conversion are checked by actually reading a test image and converting a test file. Results are kept for 10 minutes. Checked {fmtDate(checks.data.generated_at)}.</p>
        </>
      )}
    </Panel>
  );
}

function AiProvider({ admin, onChange }: { admin: boolean; onChange: () => void }) {
  const ai = useLoad<AiView>(() => api("/ai/settings"));
  const usage = useLoad<Usage[]>(() => api("/ai/usage?limit=10"));
  const [key, setKey] = useState("");
  const [form, setForm] = useState<Partial<AiView> | null>(null);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const act = useAction();
  if (ai.loading && !ai.data) return <Panel title="AI provider"><Loading /></Panel>;
  if (!ai.data) return <Panel title="AI provider"><ErrorBox error={ai.error} onRetry={ai.reload} /></Panel>;
  const v = ai.data;
  const f = { ...v, ...(form || {}) };
  const dirty = form !== null;
  const done = (r?: unknown) => { setForm(null); ai.reload(); usage.reload(); onChange(); return r; };
  const save = () => act.run(() => api("/ai/settings", { method: "PUT", json: { provider: f.provider, model: f.model, effort: f.effort, monthly_budget_usd: f.monthly_budget_usd, max_pages_per_run: Number(f.max_pages_per_run) } }), done);
  const sel = v.models.find((m) => m.id === f.model);
  const keyLabel = v.key.source === "settings" ? `Stored, ends in …${v.key.hint || "????"}, encrypted with ${v.key.encryption}`
    : v.key.source === "environment" ? "Taken from the server's ANTHROPIC_API_KEY environment variable" : "No key stored";
  const canConsent = v.provider === "anthropic" && !!v.key.source && v.last_test.ok === true;

  return (
    <Panel title="AI provider">
      <ErrorBox error={act.error} />
      <p className="max-w-3xl text-sm leading-relaxed">AI is optional. When enabled it reads tender pages and suggests extra requirements, each with a plain-language interpretation and an exact quote from the page. Quotes that do not match the page word for word are thrown away. Every suggestion stays unreviewed until a person approves it. <strong>Eligibility thresholds, dates and money are always worked out by TenderPilot's own rules, never by AI.</strong></p>

      <fieldset className="mt-4" disabled={!admin}>
        <legend className="mb-2 text-sm font-semibold">1. Provider</legend>
        <div className="grid gap-3 md:grid-cols-2">
          {v.providers.map((p) => (
            <label key={p.id} className={cx("flex cursor-pointer gap-3 rounded-md border p-3 text-sm", f.provider === p.id ? "border-teal bg-teal-soft" : "border-line bg-white")}>
              <input type="radio" name="provider" className="mt-1" checked={f.provider === p.id} onChange={() => setForm({ ...form, provider: p.id })} />
              <span><span className="font-medium">{p.label}</span> <span className="text-muted">({p.cost})</span><span className="mt-1 block text-muted">{p.description}</span></span>
            </label>
          ))}
        </div>
      </fieldset>

      {f.provider === "anthropic" && (
        <>
          <fieldset className="mt-5" disabled={!admin}>
            <legend className="mb-2 text-sm font-semibold">2. Model and cost</legend>
            <div className="overflow-x-auto"><table className="datagrid">
              <thead><tr><th></th><th>Model</th><th>Input, USD per million tokens</th><th>Output, USD per million tokens</th><th>Estimate for 100 pages at {f.effort} effort</th><th>Notes</th></tr></thead>
              <tbody>{v.models.map((m) => (
                <tr key={m.id}>
                  <td><input type="radio" name="model" aria-label={m.label} checked={f.model === m.id} onChange={() => setForm({ ...form, model: m.id })} /></td>
                  <td className="whitespace-nowrap font-medium">{m.label}</td><td>{m.input_per_mtok}</td><td>{m.output_per_mtok}</td>
                  <td className="whitespace-nowrap">USD {m.estimate_100_pages.cost_usd_low} to {m.estimate_100_pages.cost_usd_high}</td>
                  <td className="text-xs text-muted">{m.note}</td>
                </tr>))}</tbody>
            </table></div>
            <p className="mt-2 text-xs text-muted">Anthropic's list prices as of {v.price_as_of}; check <a className="underline" href={v.price_source} target="_blank" rel="noreferrer">{v.price_source}</a> before relying on them. Anthropic bills your account directly. Estimates assume about 3,000 characters per page. The real cost of each request is measured and shown under Usage below.</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Field label="Effort" hint={EFFORT_HELP[f.effort]}>
                <select className={inputCls} value={f.effort} onChange={(e) => setForm({ ...form, effort: e.target.value })}>{v.efforts.map((x) => <option key={x} value={x}>{x}</option>)}</select>
              </Field>
              <Field label="Monthly budget (USD)" hint="AI requests stop when this is reached. Rule-based extraction carries on.">
                <input className={inputCls} inputMode="decimal" value={f.monthly_budget_usd} onChange={(e) => setForm({ ...form, monthly_budget_usd: e.target.value })} />
              </Field>
              <Field label="Most pages per AI review" hint="Larger documents are sent only up to this limit, and you are told which pages were left out.">
                <input className={inputCls} type="number" min={1} max={5000} value={f.max_pages_per_run} onChange={(e) => setForm({ ...form, max_pages_per_run: Number(e.target.value) })} />
              </Field>
            </div>
          </fieldset>
        </>
      )}
      {admin && dirty && <div className="mt-3 flex gap-2"><Button onClick={save} disabled={act.busy}>Save provider settings</Button><Button kind="secondary" onClick={() => setForm(null)}>Discard changes</Button></div>}

      {v.provider === "anthropic" && !dirty && (
        <>
          <div className="mt-5">
            <h3 className="mb-2 text-sm font-semibold">3. API key</h3>
            <p className="text-sm">{keyLabel}{v.key.updated_at && `, saved ${fmtDate(v.key.updated_at)}`}.</p>
            {v.key.error && <p className="mt-1 text-sm text-fail">{v.key.error}</p>}
            {admin && (
              <form className="mt-2 flex max-w-2xl flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); act.run(() => api("/ai/key", { method: "PUT", json: { api_key: key } }), () => { setKey(""); setTest(null); done(); }); }}>
                <label className="min-w-64 flex-1 text-sm">{v.key.stored ? "Replace key" : "Paste your Anthropic API key"}
                  <input className={`${inputCls} mt-1 font-mono`} type="password" autoComplete="off" spellCheck={false} placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} />
                </label>
                <Button disabled={!key || act.busy}>Save key</Button>
                {v.key.stored && <Button type="button" kind="danger" onClick={() => act.run(() => api("/ai/key", { method: "DELETE" }), () => { setTest(null); done(); })}>Remove key</Button>}
              </form>
            )}
            <p className="mt-1 text-xs text-muted">Create a key in the Claude Console (console.anthropic.com, API keys). Enter it only here: never in chat, email, documents or source code. It is encrypted on this computer and is never shown again, logged or sent anywhere except Anthropic.</p>
          </div>

          <div className="mt-5">
            <h3 className="mb-2 text-sm font-semibold">4. Connection test</h3>
            <p className="text-sm">{v.last_test.at ? <>Last test {fmtDate(v.last_test.at)}: <span className={v.last_test.ok ? "text-pass" : "text-fail"}>{v.last_test.ok ? "passed" : "failed"}</span>. {v.last_test.message}</> : v.last_test.message || "Not tested yet."}</p>
            {admin && <Button className="mt-2" kind="secondary" disabled={!v.key.source || act.busy} onClick={() => act.run(() => api<{ ok: boolean; message: string }>("/ai/test", { json: {} }).then((r) => { setTest(r); return r; }), done)}>Test connection</Button>}
            {test && <p className={cx("mt-2 text-sm", test.ok ? "text-pass" : "text-fail")} role="status">{test.message}</p>}
            <p className="mt-1 text-xs text-muted">The test checks the key, the network and access to the chosen model. It sends no document text and costs nothing.</p>
          </div>

          <div className="mt-5 border-t border-line pt-4">
            <h3 className="mb-2 text-sm font-semibold">5. Permission to send tender text</h3>
            <label className={cx("flex items-start gap-2 text-sm", !canConsent && !v.consent && "opacity-60")}>
              <input type="checkbox" className="mt-0.5" disabled={!admin || (!canConsent && !v.consent)} checked={v.consent}
                onChange={(e) => act.run(() => api("/org", { method: "PATCH", json: { allow_external_ai: e.target.checked } }), done)} />
              <span>Send the text of processed tender pages to Anthropic (api.anthropic.com) for AI review, charged to our Anthropic account.
                <span className="block text-xs text-muted">Company vault documents (certificates, balance sheets, IDs) are never sent. New tender documents are reviewed automatically after rule-based extraction; you can also start a review per document. {!canConsent && !v.consent && "Available once a key is saved and the connection test has passed."}</span></span>
            </label>
            {v.live && <Notice>AI review is on: {sel?.label}, {v.effort} effort, budget USD {v.monthly_budget_usd} a month.</Notice>}
          </div>
        </>
      )}

      <div className="mt-5 border-t border-line pt-4">
        <h3 className="mb-2 text-sm font-semibold">Usage this month</h3>
        <p className="text-sm">{v.usage_this_month.requests} request(s), {v.usage_this_month.input_tokens.toLocaleString("en-IN")} input and {v.usage_this_month.output_tokens.toLocaleString("en-IN")} output tokens, about <strong>USD {v.usage_this_month.cost_usd}</strong> at list price.</p>
        {!!usage.data?.length && (
          <div className="mt-2 overflow-x-auto"><table className="datagrid">
            <thead><tr><th>When</th><th>Model</th><th>Pages</th><th>Tokens in / out</th><th>Cost (USD)</th><th>Result</th></tr></thead>
            <tbody>{usage.data.map((u) => (
              <tr key={u.id}><td className="whitespace-nowrap text-xs">{fmtDate(u.created_at)}</td><td className="text-xs">{u.model}</td>
                <td className="text-xs">{u.pages.split(",").filter(Boolean).map((p) => Number(p) + 1).join(", ")}</td>
                <td className="text-xs">{u.input_tokens} / {u.output_tokens}</td><td className="text-xs">{u.cost_usd ?? "—"}</td>
                <td className="text-xs">{u.outcome === "ok" ? `${u.accepted} kept, ${u.discarded} discarded` : u.outcome.replace(/_/g, " ")}{u.message && <div className="text-muted">{u.message}</div>}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </Panel>
  );
}

export default function Settings() {
  const { can, refresh } = useMe();
  const users = useLoad<{ id: number; name: string; email: string; roles: string[] }[]>(() => api("/users"));
  const audit = useLoad<{ id: number; created_at: string; user_name: string; action: string; subject_type: string; subject_id: number | null; tender_id: number | null; details: Record<string, unknown> }[]>(
    () => can("audit.read") ? api("/audit?limit=200") : Promise.resolve([]));
  const notes = useLoad<{ id: number; message: string; created_at: string; read: boolean }[]>(() => api("/notifications"));
  const [u, setU] = useState({ name: "", email: "", password: "", roles: [] as string[] });
  const act = useAction();
  const admin = can("org.admin");
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">Settings and audit</h1>
      <ErrorBox error={act.error} />
      {admin && <SoftwareUpdate />}
      <SystemChecks admin={admin} />
      <AiProvider admin={admin} onChange={() => { refresh(); audit.reload(); }} />
      <Panel title={`Notifications (${notes.data?.filter((n) => !n.read).length || 0} unread)`} actions={<Button kind="secondary" onClick={() => act.run(() => api("/notifications/read", { json: {} }), () => { notes.reload(); refresh(); })}>Mark all read</Button>}>
        {!notes.data?.length ? <Empty>No notifications.</Empty> : <ul className="max-h-60 space-y-1.5 overflow-auto text-sm">{notes.data.map((n) => <li key={n.id} className={n.read ? "text-muted" : ""}>{fmtDate(n.created_at)}: {n.message}</li>)}</ul>}
      </Panel>
      <Panel title="People and roles">
        {admin && (
          <form className="mb-4 grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); act.run(() => api("/users", { json: u }), () => { setU({ name: "", email: "", password: "", roles: [] }); users.reload(); }); }}>
            <Field label="Name"><input required className={inputCls} value={u.name} onChange={(e) => setU({ ...u, name: e.target.value })} /></Field>
            <Field label="Email"><input required type="email" className={inputCls} value={u.email} onChange={(e) => setU({ ...u, email: e.target.value })} /></Field>
            <Field label="Initial password" hint="At least 10 characters; share it privately."><input required minLength={10} type="password" autoComplete="new-password" className={inputCls} value={u.password} onChange={(e) => setU({ ...u, password: e.target.value })} /></Field>
            <fieldset className="sm:col-span-3"><legend className="mb-1 text-sm font-medium">Roles</legend>
              <div className="flex flex-wrap gap-3 text-sm">{ROLES.map((r) => <label key={r} className="flex items-center gap-1.5"><input type="checkbox" checked={u.roles.includes(r)}
                onChange={(e) => setU({ ...u, roles: e.target.checked ? [...u.roles, r] : u.roles.filter((x) => x !== r) })} />{r.replace(/_/g, " ")}</label>)}</div></fieldset>
            <div><Button disabled={!u.roles.length || act.busy}>Add person</Button></div>
          </form>
        )}
        <table className="datagrid"><thead><tr><th>Name</th><th>Email</th><th>Roles</th></tr></thead>
          <tbody>{users.data?.map((x) => <tr key={x.id}><td>{x.name}</td><td>{x.email}</td><td>{x.roles.map((r) => r.replace(/_/g, " ")).join(", ")}</td></tr>)}</tbody></table>
        <Notice>Prices are visible only to estimators, finance reviewers, approvers, tender managers, admins and auditors. Permissions are enforced on the server.</Notice>
      </Panel>
      {can("audit.read") && (
        <Panel title="Audit trail (latest 200)">
          <div className="max-h-[50vh] overflow-auto"><table className="datagrid"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Details</th></tr></thead>
            <tbody>{audit.data?.map((e) => <tr key={e.id}><td className="whitespace-nowrap text-xs">{fmtDate(e.created_at)}</td><td>{e.user_name}</td><td>{e.action}</td>
              <td className="text-xs">{e.subject_type} {e.subject_id}{e.tender_id && `, tender ${e.tender_id}`}</td><td className="max-w-md truncate text-xs text-muted">{JSON.stringify(e.details)}</td></tr>)}</tbody></table></div>
        </Panel>
      )}
    </div>
  );
}
