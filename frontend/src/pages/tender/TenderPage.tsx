import { useState } from "react";
import { t as tr } from "../../i18n";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { useMe } from "../../App";
import { Button, cx, ErrorBox, fmtDate, inputCls, Loading, Panel, Status, Tabs, useAction, useLoad } from "../../ui";
import Documents from "./Documents";
import Clauses from "./Clauses";
import Eligibility from "./Eligibility";
import Qualify from "./Qualify";
import Checklist from "./Checklist";
import Studio from "./Studio";
import Estimate from "./Estimate";
import Package from "./Package";

export type Tender = {
  id: number; title: string; tender_ref: string; department: string; portal_url: string; bidder_type: string;
  bidder_entity_id: number | null; jv_id: number | null; bidder_name: string | null; deadline_at: string | null; deadline_tz: string;
  deadline_raw: string | null; deadline_source: string | null; qualification_cutoff: string | null; qualification_cutoff_source: string | null;
  current_version: number; row_version: number; status: string; step_state: Record<string, { status: string }>;
  readiness: { blockers: { type: string; severity: string; message: string }[]; eligible_ready: boolean; workload_percent: number; workload_note: string };
  next_action: string; versions: { number: number; reason: string; created_at: string }[];
};

// The guided journey from the brief; each step links to the tab where it is done.
const STEPS: [string, string, string][] = [
  ["create", "Create tender", "overview"], ["upload", "Upload all volumes", "documents"], ["verify_extraction", "Verify extraction", "documents"],
  ["review_clauses", "Review clauses and amendments", "clauses"], ["choose_bidder", "Choose bidder or JV", "qualify"],
  ["eligibility", "Assess eligibility", "eligibility"], ["collect_evidence", "Collect evidence", "checklist"],
  ["technical", "Prepare technical response", "studio"], ["estimate", "Estimate and approve prices", "estimate"],
  ["generate_forms", "Generate forms", "studio"], ["sign_certify", "Sign or certify externally", "studio"],
  ["verify_returned", "Verify returned documents", "studio"], ["assemble_envelopes", "Assemble envelopes", "package"],
  ["preflight", "Run preflight", "package"], ["export", "Export submission files", "package"], ["receipt", "Record portal receipt", "package"],
];

const TABS: [string, string][] = [["overview", "Command centre"], ["documents", "Documents"], ["clauses", "Clauses"], ["qualify", "How to qualify"], ["eligibility", "Eligibility"],
  ["checklist", "Checklist"], ["studio", "Document studio"], ["estimate", "BOQ and quote"], ["package", "Envelopes and export"]];

export default function TenderPage() {
  const { id, "*": rest } = useParams();
  const tid = Number(id);
  const nav = useNavigate();
  const tab = (rest || "").split("/")[0] || "overview";
  const t = useLoad<Tender>(() => api(`/tenders/${tid}`), [tid]);
  if (t.loading && !t.data) return <Loading />;
  if (!t.data) return <ErrorBox error={t.error} onRetry={t.reload} />;
  const d = t.data;
  return (
    <div className="mx-auto max-w-[1400px]">
      <div className="mb-1 text-sm"><Link to="/" className="text-muted hover:underline">Tenders</Link></div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[1.6rem] font-semibold leading-tight tracking-tight">{d.title}</h1>
          <div className="text-sm text-muted">{d.tender_ref || "Tender ID not set"}{d.department && `, ${d.department}`}{d.current_version > 1 && `. Version ${d.current_version} after amendments`}</div>
        </div>
        <div className="sm:text-right">
          <div className="text-xs text-muted">Bid submission deadline</div>
          <div className="text-lg font-semibold">{fmtDate(d.deadline_at)}</div>
          <div className="max-w-xs text-xs text-muted">{d.deadline_source || "Not yet confirmed"}</div>
        </div>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={(k) => nav(`/tenders/${tid}${k === "overview" ? "" : `/${k}`}`)} />
      <div className="mt-5">
        {tab === "overview" && <Overview t={d} reload={t.reload} />}
        {tab === "documents" && <Documents tid={tid} onChange={t.reload} />}
        {tab === "clauses" && <Clauses tid={tid} onChange={t.reload} />}
        {tab === "qualify" && <Qualify t={d} onChange={t.reload} />}
        {tab === "eligibility" && <Eligibility t={d} onChange={t.reload} />}
        {tab === "checklist" && <Checklist t={d} onChange={t.reload} />}
        {tab === "studio" && <Studio tid={tid} />}
        {tab === "estimate" && <Estimate tid={tid} />}
        {tab === "package" && <Package tid={tid} onChange={t.reload} />}
      </div>
    </div>
  );
}

function TenderRoute({ t, reload }: { t: Tender; reload: () => void }) {
  const { can } = useMe();
  const act = useAction();
  const nav = useNavigate();
  const set = (step: string, status: string) => act.run(() => api(`/tenders/${t.id}/steps/${step}`, { method: "PUT", json: { status } }), reload);
  const current = STEPS.find(([k]) => (t.step_state[k]?.status || "pending") !== "done")?.[0];
  return (
    <Panel title="Tender route" actions={<span className="text-xs text-muted">Saved automatically. The highlighted step is next.</span>}>
      <ErrorBox error={act.error} />
      <ol className="grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map(([k, label, where], i) => {
          const st = t.step_state[k]?.status || "pending";
          return (
            <li key={k} className={cx("flex flex-col gap-1 p-3", k === current ? "bg-teal-soft" : "bg-panel")} aria-current={k === current ? "step" : undefined}>
              <div className="flex items-center justify-between gap-2">
                <span className={cx("text-xs tabular-nums", st === "done" ? "text-pass" : "text-muted")}>Step {i + 1}</span>
                <Status s={st} />
              </div>
              <button className="text-left text-sm font-medium hover:underline" onClick={() => nav(`/tenders/${t.id}${where === "overview" ? "" : `/${where}`}`)}>{tr(label)}</button>
              {can("tasks.write") && (
                <div className="mt-auto flex gap-3 pt-1 text-xs">
                  {st !== "done" ? <button className="text-teal underline" onClick={() => set(k, "done")}>Mark done</button>
                    : <button className="text-muted underline" onClick={() => set(k, "in_progress")}>Reopen</button>}
                  {st !== "blocked" && st !== "done" && <button className="text-muted underline" onClick={() => set(k, "blocked")}>Mark blocked</button>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

function Overview({ t, reload }: { t: Tender; reload: () => void }) {
  const { can } = useMe();
  const r = t.readiness;
  const order = { critical: 0, high: 1, medium: 2 } as Record<string, number>;
  const blockers = [...r.blockers].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        <div className="rounded-lg border-l-4 border-teal bg-panel px-4 py-3">
          <div className="text-xs text-muted">Next action</div>
          <div className="text-[1.05rem] font-medium">{t.next_action}</div>
        </div>
        <TenderRoute t={t} reload={reload} />
        <Panel title={`Blocking issues (${r.blockers.length})`}>
          {blockers.length === 0 ? <p className="text-sm text-pass">No blocking issues in the reviewed rules and checklist.</p> : (
            <ul className="divide-y divide-[#ebe9e3] text-sm">
              {blockers.slice(0, 50).map((b, i) => <li key={i} className="flex items-start gap-3 py-2"><Status s={b.severity} /><span>{b.message}</span></li>)}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted">Checklist closed: {r.workload_percent}%. {r.workload_note}</p>
        </Panel>
      </div>
      <div className="space-y-5">
        {can("tender.write") && <BidderAndCutoff t={t} reload={reload} />}
        <Panel title="Amendment history">
          <ol className="space-y-2 text-sm">
            {t.versions.map((v) => <li key={v.number}><span className="font-medium">Version {v.number}</span>: {v.reason}<div className="text-xs text-muted">{fmtDate(v.created_at)}</div></li>)}
          </ol>
        </Panel>
      </div>
    </div>
  );
}

function BidderAndCutoff({ t, reload }: { t: Tender; reload: () => void }) {
  const ents = useLoad<{ id: number; legal_name: string }[]>(() => api("/entities"));
  const jvs = useLoad<{ id: number; name: string }[]>(() => api("/jvs"));
  const [f, setF] = useState({ bidder_type: t.bidder_type, bidder: String((t.bidder_type === "jv" ? t.jv_id : t.bidder_entity_id) || ""),
    cutoff: t.qualification_cutoff || "", source: t.qualification_cutoff_source || "" });
  const act = useAction();
  const save = () => act.run(() => api(`/tenders/${t.id}`, { method: "PATCH", json: {
    row_version: t.row_version, bidder_type: f.bidder_type,
    bidder_entity_id: f.bidder_type === "entity" && f.bidder ? Number(f.bidder) : null,
    jv_id: f.bidder_type === "jv" && f.bidder ? Number(f.bidder) : null,
    ...(f.cutoff ? { qualification_cutoff: f.cutoff, qualification_cutoff_source: f.source } : {}) } }), reload);
  return (
    <Panel title="Bidder and qualification date">
      <ErrorBox error={act.error} />
      <div className="space-y-3 text-sm">
        <select aria-label="Bidder type" className={inputCls} value={f.bidder_type} onChange={(e) => setF({ ...f, bidder_type: e.target.value, bidder: "" })}>
          <option value="entity">Single legal entity</option><option value="jv">Joint venture</option>
        </select>
        <select aria-label="Bidder" className={inputCls} value={f.bidder} onChange={(e) => setF({ ...f, bidder: e.target.value })}>
          <option value="">Not chosen</option>
          {(f.bidder_type === "jv" ? jvs.data?.map((j) => [j.id, j.name]) : ents.data?.map((x) => [x.id, x.legal_name]))?.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
        </select>
        <label className="block"><span className="mb-1 block font-medium">Qualification cutoff date</span>
          <input type="date" className={inputCls} value={f.cutoff} onChange={(e) => setF({ ...f, cutoff: e.target.value })} /></label>
        <input aria-label="Clause defining the cutoff" placeholder="Clause that defines it, e.g. 10.5" className={inputCls}
          value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} />
        <p className="text-xs text-muted">Certificates are tested against this date, never today's date. Take it from the tender clause.</p>
        <Button onClick={save} disabled={act.busy || (!!f.cutoff && !f.source)}>Save bidder and date</Button>
      </div>
    </Panel>
  );
}
