import { useState } from "react";
import { api } from "../../api";
import { useMe } from "../../App";
import { Button, Empty, ErrorBox, fmtDay, inputCls, Loading, Status, useAction, useLoad } from "../../ui";
import type { Tender } from "./TenderPage";
type Task = { id: number; title: string; question: string | null; group: string; action_type: string; mandatory: boolean; owner_id: number | null;
  due_date: string | null; evidence_category: string | null; evidence_id: number | null; signing_required: boolean; certification_required: string | null;
  envelope: string; status: string; stale: boolean; history: unknown[] };
type Evid = { id: number; title: string; category: string; verification_state: string; expiry_date: string | null; entity_name: string; legal_entity_id: number };

const ACTION: Record<string, string> = { upload_existing: "Upload existing document", draft_internal: "Draft internally", obtain_external: "Obtain from bank / CA / OEM / department",
  print_sign_scan: "Print, sign and scan", notarize: "Notarise", portal_action: "Portal action", upload_portal_workbook: "Upload original portal workbook" };
const GROUP: Record<string, string> = { bidder: "Bidder", jv_member: "JV members", specialist: "Specialist / OEM", financial_qualification: "Financial qualification",
  fee_security: "Fee and security", technical: "Technical envelope", financial: "Financial envelope", originals: "Physical originals", submission: "Submission" };

export default function Checklist({ t, onChange }: { t: Tender; onChange: () => void }) {
  const { can } = useMe();
  const tasks = useLoad<Task[]>(() => api(`/tenders/${t.id}/tasks`), [t.id]);
  const evid = useLoad<Evid[]>(() => api("/evidence"), []);
  const users = useLoad<{ id: number; name: string }[]>(() => api("/users"));
  const [show, setShow] = useState("open");
  const act = useAction();
  const reload = () => { tasks.reload(); onChange(); };
  const patch = (id: number, body: Record<string, unknown>) => act.run(() => api(`/tenders/${t.id}/tasks/${id}`, { method: "PATCH", json: body }), reload);
  if (tasks.loading && !tasks.data) return <Loading />;
  const rows = (tasks.data || []).filter((x) => show === "all" || (show === "open" ? !["accepted", "not_applicable"].includes(x.status) : x.stale));
  const groups = [...new Set(rows.map((r) => r.group))];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Show tasks" className={`${inputCls} w-48`} value={show} onChange={(e) => setShow(e.target.value)}>
          <option value="open">Open items</option><option value="stale">Affected by amendments</option><option value="all">All items</option></select>
        <span className="text-sm text-muted">{(tasks.data || []).filter((x) => x.status === "accepted").length} of {tasks.data?.length || 0} accepted</span>
      </div>
      <ErrorBox error={act.error} />
      {rows.length === 0 ? <Empty>{tasks.data?.length ? "Nothing in this view." : "The checklist is generated from the reviewed requirements."}</Empty> : groups.map((g) => (
        <section key={g} className="rounded-lg border border-line bg-panel">
          <h2 className="border-b border-line px-4 py-2.5 text-[0.95rem] font-semibold">{GROUP[g] || g}</h2>
          <ul className="divide-y divide-[#ebe9e3]">
            {rows.filter((r) => r.group === g).map((task) => {
              const options = (evid.data || []).filter((e) => !task.evidence_category || e.category === task.evidence_category);
              return (
                <li key={task.id} className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_300px]">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{task.title}</span><Status s={task.status} />
                      {task.mandatory ? <span className="text-xs text-muted">Mandatory</span> : <span className="text-xs text-muted">Conditional</span>}
                      {task.stale && <Status s="needs_review" />}
                    </div>
                    {task.question && <p className="mt-1 text-sm">{task.question}</p>}
                    <div className="mt-1 text-xs text-muted">{ACTION[task.action_type] || task.action_type}{task.certification_required && `, certified by ${task.certification_required}`}
                      {task.signing_required && ", needs authorised signature"}, {task.envelope} envelope{task.due_date && `, due ${fmtDay(task.due_date)}`}</div>
                  </div>
                  {can("tasks.write") && (
                    <div className="space-y-2 text-sm">
                      {task.evidence_category && (
                        <select aria-label="Link evidence" className={inputCls} value={task.evidence_id || ""} onChange={(e) => e.target.value && patch(task.id, { evidence_id: Number(e.target.value) })}>
                          <option value="">{options.length ? "Link a vault document…" : "No matching vault document: upload it first"}</option>
                          {options.map((e) => <option key={e.id} value={e.id}>{e.title} ({e.entity_name}, {e.verification_state}{e.expiry_date ? `, expires ${e.expiry_date}` : ""})</option>)}
                        </select>
                      )}
                      <div className="flex flex-wrap gap-2">
                        <select aria-label="Owner" className={`${inputCls} w-40`} value={task.owner_id || ""} onChange={(e) => patch(task.id, { owner_id: Number(e.target.value) })}>
                          <option value="">Unassigned</option>{users.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
                        <input type="date" aria-label="Due date" className={`${inputCls} w-36`} value={task.due_date || ""} onChange={(e) => patch(task.id, { due_date: e.target.value })} />
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {task.status !== "accepted" && <Button onClick={() => patch(task.id, { status: "accepted" })} disabled={act.busy}>Accept</Button>}
                        {task.status === "accepted" && <Button kind="secondary" onClick={() => patch(task.id, { status: "rejected" })}>Reject and replace</Button>}
                        {!task.mandatory && task.status !== "not_applicable" && <Button kind="ghost" onClick={() => patch(task.id, { status: "not_applicable", note: "Condition not met" })}>Not applicable</Button>}
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
