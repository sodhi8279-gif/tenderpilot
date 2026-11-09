import { useState } from "react";
import { api } from "../api";
import { useMe } from "../App";
import { Button, Empty, ErrorBox, Field, fmtDate, inputCls, Notice, Panel, useAction, useLoad } from "../ui";

const ROLES = ["org_admin", "tender_manager", "technical_reviewer", "estimator", "document_coordinator", "finance_reviewer", "authorized_approver", "auditor"];

export default function Settings() {
  const { me, can, refresh } = useMe();
  const sys = useLoad<{ ai: { provider: string; live: boolean; data_flow: string; model?: string }; ai_configured: boolean; ocr: { engine: string; available: boolean; note: string | null };
    database: string; queue: string; converter: { note: string } }>(() => api("/system"));
  const users = useLoad<{ id: number; name: string; email: string; roles: string[] }[]>(() => api("/users"));
  const audit = useLoad<{ id: number; created_at: string; user_name: string; action: string; subject_type: string; subject_id: number | null; tender_id: number | null; details: Record<string, unknown> }[]>(
    () => can("audit.read") ? api("/audit?limit=200") : Promise.resolve([]));
  const notes = useLoad<{ id: number; message: string; created_at: string; read: boolean }[]>(() => api("/notifications"));
  const [u, setU] = useState({ name: "", email: "", password: "", roles: [] as string[] });
  const act = useAction();
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">Settings and audit</h1>
      <ErrorBox error={act.error} />
      <Panel title="System status">
        {sys.data && <ul className="space-y-1.5 text-sm">
          <li><span className="font-medium">Extraction: </span>{sys.data.ai.live ? `Live AI (${sys.data.ai.provider}, ${sys.data.ai.model})` : "Deterministic rules only, no external AI"}. {sys.data.ai.data_flow}</li>
          <li><span className="font-medium">OCR: </span>{sys.data.ocr.available ? `${sys.data.ocr.engine} available` : sys.data.ocr.note}</li>
          <li><span className="font-medium">Office conversion: </span>{sys.data.converter.note}</li>
          <li><span className="font-medium">Storage: </span>{sys.data.database} database, {sys.data.queue}</li>
        </ul>}
        {can("org.admin") && (
          <div className="mt-4 border-t border-line pt-4">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={me.org.allow_external_ai} onChange={(e) => act.run(() => api("/org", { method: "PATCH", json: { allow_external_ai: e.target.checked } }), () => { refresh(); sys.reload(); })} />
              <span>Allow tender page text to be sent to the configured external AI provider{!sys.data?.ai_configured && " (no provider key configured on the server, so this has no effect yet)"}.
                <span className="block text-xs text-muted">Off by default. Company vault documents are never sent.</span></span>
            </label>
          </div>
        )}
      </Panel>
      <Panel title={`Notifications (${notes.data?.filter((n) => !n.read).length || 0} unread)`} actions={<Button kind="secondary" onClick={() => act.run(() => api("/notifications/read", { json: {} }), () => { notes.reload(); refresh(); })}>Mark all read</Button>}>
        {!notes.data?.length ? <Empty>No notifications.</Empty> : <ul className="max-h-60 space-y-1.5 overflow-auto text-sm">{notes.data.map((n) => <li key={n.id} className={n.read ? "text-muted" : ""}>{fmtDate(n.created_at)}: {n.message}</li>)}</ul>}
      </Panel>
      <Panel title="People and roles">
        {can("org.admin") && (
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
