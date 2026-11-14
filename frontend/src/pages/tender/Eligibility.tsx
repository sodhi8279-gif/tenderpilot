import { Fragment, useState } from "react";
import { api, inr } from "../../api";
import { useMe } from "../../App";
import { Button, Empty, ErrorBox, inputCls, Loading, Notice, Panel, Status, useAction, useLoad } from "../../ui";
import type { Tender } from "./TenderPage";

type Rule = { id: number; rule_type: string; params: Record<string, unknown>; status: string; version: number; explanation: string;
  low_confidence: boolean; material: boolean; requirement: { key: string; title: string; excerpt: string | null; clause_no: string | null; page_index: number | null; confidence: number | null } | null };
type Ev = { id: number; rule_id: number; subject: string; result: string; explanation: string; calculation: Record<string, unknown>;
  missing_inputs: string[]; remedial_action: string | null; override_result: string | null; override_rationale: string | null; rule: Rule };

const EDITABLE: Record<string, string[]> = { avg_turnover: ["threshold", "years"],
  similar_work: ["count", "percent_of_estimate", "amount", "period_years", "work_nature", "keywords", "dimensions", "allow_ongoing", "substantial_threshold_pct"],
  specific_experience: ["count", "work", "keywords", "dimensions", "period_years", "percent_of_estimate", "amount", "allow_ongoing"],
  net_worth: ["min"], solvency: ["min"], bid_capacity: ["M", "years_for_A"], jv_max_members: ["max_members"], jv_lead_share: ["min_percent"],
  personnel: ["count", "min_qualification", "disciplines", "min_experience_years"], equipment: ["count", "min_capacity", "capacity_unit", "ownership"],
  physical_quantity: ["min_quantity", "unit", "basis", "period_years", "min_dia_mm", "material"], registration_class: ["accepted_classes", "authority"] };
const HINTS: Record<string, string> = { min_qualification: "iti / diploma / graduate / postgraduate", disciplines: "e.g. civil, mechanical",
  ownership: "owned / owned_or_leased", basis: "any_one_year / cumulative", accepted_classes: "comma-separated, e.g. I, I(A)", material: "DI / MS / HDPE…",
  dimensions: "e.g. height 7.5 m; length 10 m", keywords: "words a similar work must mention, comma-separated", allow_ongoing: "yes / no",
  work_nature: "kind of work, as in the clause", work: "kind of work, as in the clause" };
const LISTS = ["disciplines", "accepted_classes", "keywords"];
const showParam = (k: string, v: unknown) => k === "dimensions" && Array.isArray(v)
  ? (v as { name: string; min: string; unit?: string }[]).map((d) => `${d.name} ${d.min} ${d.unit || "m"}`).join("; ")
  : typeof v === "boolean" ? (v ? "yes" : "no") : Array.isArray(v) ? v.join(", ") : String(v ?? "");
const INTS = ["years", "count", "period_years", "max_members", "years_for_A", "min_experience_years", "min_dia_mm"];

export default function Eligibility({ t, onChange }: { t: Tender; onChange: () => void }) {
  const { can } = useMe();
  const rules = useLoad<Rule[]>(() => api(`/tenders/${t.id}/rules`), [t.id]);
  const evs = useLoad<{ evaluations: Ev[]; stale_count: number }>(() => api(`/tenders/${t.id}/evaluations`), [t.id]);
  const [report, setReport] = useState<null | { rows: { criterion: string; subject: string; result: string; explanation: string; remedy: string | null }[]; recommendation: string; review_status: string; disclaimer: string; summary: Record<string, number> }>(null);
  const act = useAction();
  const reload = () => { rules.reload(); evs.reload(); onChange(); };
  const evaluate = () => act.run(() => api(`/tenders/${t.id}/evaluate`, { json: {} }), reload);
  if (rules.loading && !rules.data) return <Loading />;
  const proposed = (rules.data || []).filter((r) => r.status === "proposed");
  return (
    <div className="space-y-5">
      <ErrorBox error={act.error} />
      {!t.qualification_cutoff && <Notice tone="warn">Set the qualification cutoff date in the command centre. Until then results stay ambiguous rather than using today's date.</Notice>}
      {(evs.data?.stale_count || 0) > 0 && <Notice tone="warn">{evs.data!.stale_count} earlier result(s) became stale after an amendment, a rule edit or new evidence. Evaluate again.</Notice>}
      <Panel title={`Rules from the tender (${rules.data?.length || 0})`} actions={proposed.length > 0 && <span className="text-sm text-gap">{proposed.length} awaiting confirmation</span>}>
        {!rules.data?.length ? <Empty>No rules yet. Rules are proposed from the extracted qualification clauses.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Rule</th><th>Source</th><th>Status</th><th></th></tr></thead>
            <tbody>{rules.data.map((r) => <RuleRow key={r.id} t={t} r={r} reload={reload} canApprove={can("rules.approve")} />)}</tbody>
          </table></div>
        )}
      </Panel>
      <Panel title="Eligibility matrix" actions={<>
        <Button onClick={evaluate} disabled={act.busy}>Evaluate now</Button>
        <Button kind="secondary" onClick={() => act.run(async () => setReport(await api(`/tenders/${t.id}/bid-no-bid`)))}>Bid / no-bid report</Button></>}>
        {!evs.data?.evaluations.length ? <Empty>Not evaluated yet. Confirm the rules, add evidence in the company vault, then evaluate.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Requirement</th><th>Applies to</th><th>Result</th><th>Calculation and evidence</th><th>Missing / next step</th></tr></thead>
            <tbody>{evs.data.evaluations.map((e) => <EvRow key={e.id} e={e} tid={t.id} reload={reload} canOverride={can("eligibility.override")} canJudge={can("rules.approve")} />)}</tbody>
          </table></div>
        )}
      </Panel>
      {report && (
        <Panel title="Bid / no-bid report" actions={<><Button kind="secondary" onClick={() => window.print()}>Print</Button><Button kind="ghost" onClick={() => setReport(null)}>Close</Button></>}>
          <p className="font-medium">{report.recommendation}</p>
          <p className="text-sm text-muted">{report.review_status}. {report.summary.pass} pass, {report.summary.fail} fail, {report.summary.gaps} gaps.</p>
          <ul className="mt-3 space-y-2 text-sm">{report.rows.map((r, i) => <li key={i}><Status s={r.result} /> {r.criterion} <span className="text-muted">({r.subject})</span>: {r.explanation}{r.remedy && <span className="text-gap"> {r.remedy}</span>}</li>)}</ul>
          <p className="mt-3 text-xs text-muted">{report.disclaimer}</p>
        </Panel>
      )}
    </div>
  );
}

function RuleRow({ t, r, reload, canApprove }: { t: Tender; r: Rule; reload: () => void; canApprove: boolean }) {
  const [open, setOpen] = useState(false);
  const keys = EDITABLE[r.rule_type] || [];
  const [vals, setVals] = useState<Record<string, string>>(Object.fromEntries(keys.map((k) => [k, r.low_confidence ? "" : showParam(k, r.params[k])])));
  const sugg = (r.params.jv_suggested || null) as Record<string, string> | null;
  const jv0 = (r.params.jv || sugg || {}) as Record<string, string>;
  const [jv, setJv] = useState({ mode: jv0.mode || "", lead_min_pct: jv0.lead_min_pct || "", member_min_pct: jv0.member_min_pct || "" });
  const act = useAction();
  const clean = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== "").map(([k, v]) =>
    [k, LISTS.includes(k) ? v.split(",").map((x) => x.trim()).filter(Boolean) : /^\d+$/.test(v) && INTS.includes(k) ? Number(v) : v]));
  const approve = () => act.run(() => api(`/tenders/${t.id}/rules/${r.id}/approve`, { json: { params: { ...clean(vals), ...(jv.mode ? { jv: clean(jv) } : {}) }, confirm_low_confidence: r.low_confidence } }), reload);
  const saveJv = () => act.run(() => api(`/tenders/${t.id}/rules/${r.id}`, { method: "PUT", json: { params: { jv: clean(jv) } } }), reload);
  const isJvBid = t.bidder_type === "jv" && !r.rule_type.startsWith("jv_");
  return (
    <>
      <tr>
        <td className="max-w-md">{r.explanation}{typeof r.params.similar_definition === "string" && <div className="text-xs text-muted">Similar work means: {r.params.similar_definition}</div>}{r.low_confidence && <div className="text-xs text-gap">Low-confidence reading: re-enter the values from the source before confirming.</div>}</td>
        <td className="max-w-sm text-xs text-muted">{r.requirement ? <>Clause {r.requirement.clause_no || "?"}, page {(r.requirement.page_index ?? 0) + 1}<div className="line-clamp-2">{r.requirement.excerpt}</div></> : "Manual"}</td>
        <td><Status s={r.status} /><div className="text-xs text-muted">v{r.version}</div></td>
        <td>{canApprove && <Button kind="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>{r.status === "approved" ? "Edit" : "Review"}</Button>}</td>
      </tr>
      {open && (
        <tr><td colSpan={4} className="bg-[#fafaf7]">
          <ErrorBox error={act.error} />
          <div className="flex flex-wrap items-end gap-3">
            {keys.map((k) => (
              <label key={k} className="text-xs">{k.replace(/_/g, " ")}
                <input className={`${inputCls} mt-1 ${["dimensions", "keywords", "work", "work_nature"].includes(k) ? "w-72" : "w-40"}`} value={vals[k]} placeholder={r.low_confidence ? "re-enter" : HINTS[k] || ""} title={HINTS[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
                {k === "threshold" || k === "min" || k === "amount" ? <span className="block text-muted">{vals[k] && inr(vals[k], 0)}</span> : null}
              </label>
            ))}
          </div>
          {isJvBid && (
            <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-line pt-3">
              <label className="text-xs">How JV members combine (from the JV clause)
                <select className={`${inputCls} mt-1 w-64`} value={jv.mode} onChange={(e) => setJv({ ...jv, mode: e.target.value })}>
                  <option value="">Not stated: result stays ambiguous</option>
                  <option value="aggregate_sum">Sum of members (with individual minima)</option>
                  <option value="lead_only">Lead member alone must meet it</option>
                  <option value="each_member">Every member must meet it</option>
                  <option value="any_member">Any one member must meet it</option>
                  {r.rule_type === "similar_work" && <option value="member_scaled">Each member meets its % share of the value</option>}
                </select>
                {sugg && !r.params.jv && <span className="mt-1 block text-teal">Pre-filled from the tender's JV clause ({sugg.basis}). Confirm or change.</span>}</label>
              {(jv.mode === "aggregate_sum" || jv.mode === "member_scaled") && <>
                <label className="text-xs">Lead minimum % of threshold<input className={`${inputCls} mt-1 w-28`} value={jv.lead_min_pct} onChange={(e) => setJv({ ...jv, lead_min_pct: e.target.value })} /></label>
                <label className="text-xs">Each other member minimum %<input className={`${inputCls} mt-1 w-28`} value={jv.member_min_pct} onChange={(e) => setJv({ ...jv, member_min_pct: e.target.value })} /></label>
              </>}
              {r.status === "approved" && <Button kind="secondary" onClick={saveJv}>Save as new rule version</Button>}
            </div>
          )}
          {r.status === "proposed" && <div className="mt-3 flex gap-2"><Button onClick={approve} disabled={act.busy}>Confirm rule</Button>
            <Button kind="danger" onClick={() => act.run(() => api(`/tenders/${t.id}/rules/${r.id}/reject`, { json: {} }), reload)}>Not applicable to this tender</Button></div>}
        </td></tr>
      )}
    </>
  );
}

type Work = { evidence_id: number; entity?: string; work: string; value: string | null; completion_date: string | null; status?: string; result: string;
  similarity?: string; similarity_note?: string; dimensions?: Record<string, string> };

function EvRow({ e, tid, reload, canOverride, canJudge }: { e: Ev; tid: number; reload: () => void; canOverride: boolean; canJudge: boolean }) {
  const [open, setOpen] = useState(false);
  const [ov, setOv] = useState({ result: "pass", rationale: "" });
  const act = useAction();
  const calc = e.calculation as Record<string, unknown>;
  const works = (calc.works || []) as Work[];
  const simKey = calc.similarity_key as string | undefined;
  const judge = (w: Work, similar: boolean | null) => act.run(() => api(`/tenders/${tid}/similarity`, { json: { evidence_id: w.evidence_id, criterion_key: simKey, similar } }), reload);
  const dims = (calc.dimensions || []) as { name: string; min: string }[];
  return (
    <Fragment>
      <tr>
        <td className="max-w-sm">{e.rule?.explanation}</td>
        <td className="text-sm">{e.subject === "bidder" ? "Bidder" : e.subject === "jv:aggregate" ? "JV combined" : e.subject.startsWith("entity:") ? `Member #${e.subject.split(":")[1]}` : e.subject}</td>
        <td><Status s={e.result} />{e.override_result && <div className="mt-1 text-xs text-ambig">Override recorded: {e.override_result} (underlying result kept)</div>}</td>
        <td className="max-w-md text-sm">{e.explanation}
          {(works.length > 0 || calc.formula || calc.values) ? <button className="ml-2 text-xs text-teal underline" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} working</button> : null}</td>
        <td className="max-w-xs text-sm">{e.missing_inputs.map((m) => <div key={m} className="text-gap">{m}</div>)}{e.remedial_action && <div className="text-muted">{e.remedial_action}</div>}</td>
      </tr>
      {open && (
        <tr><td colSpan={5} className="bg-[#fafaf7] text-sm">
          {typeof calc.work_nature === "string" && <p className="mb-2">Kind of work required: <span className="font-medium">{calc.work_nature}</span>
            {dims.length > 0 && <> · sizes: {dims.map((d) => `${d.name} ≥ ${d.min} m`).join(", ")}</>}
            {simKey && <span className="block text-xs text-muted">Whether a work is similar is decided per tender. Your decision applies to this criterion of this tender only.</span>}</p>}
          <ErrorBox error={act.error} />
          {works.length > 0 && <table className="datagrid mb-3"><thead><tr><th>Work</th><th>Value</th><th>Completed</th><th>Sizes on record</th><th>Assessment</th>{simKey && canJudge && <th>Similar for this tender?</th>}</tr></thead>
            <tbody>{works.map((w, i) => <tr key={i}>
              <td>{w.work}{w.entity && <div className="text-xs text-muted">{w.entity}</div>}</td><td>{inr(w.value, 0)}</td>
              <td>{w.completion_date}{w.status && w.status !== "completed" && <div className="text-xs text-muted">{w.status.replace(/_/g, " ")}</div>}</td>
              <td className="text-xs">{w.dimensions ? Object.entries(w.dimensions).map(([k, v]) => `${k} ${v} m`).join(", ") || "none recorded" : ""}</td>
              <td>{w.result}{w.similarity_note && w.result !== w.similarity_note && !w.result.includes(w.similarity_note) && <div className="text-xs text-muted">{w.similarity_note}</div>}</td>
              {simKey && canJudge && <td className="whitespace-nowrap">
                {w.similarity === "confirmed" || w.similarity === "rejected"
                  ? <><span className="text-xs">{w.similarity === "confirmed" ? "Similar" : "Not similar"}</span> <Button kind="ghost" onClick={() => judge(w, null)} disabled={act.busy}>Undo</Button></>
                  : <><Button kind="secondary" onClick={() => judge(w, true)} disabled={act.busy}>Similar</Button> <Button kind="ghost" onClick={() => judge(w, false)} disabled={act.busy}>Not similar</Button></>}
              </td>}
            </tr>)}</tbody></table>}
          <pre className="whitespace-pre-wrap break-words text-xs text-muted">{JSON.stringify(Object.fromEntries(Object.entries(calc).filter(([k]) => !["works", "dimensions", "work_nature", "similarity_key"].includes(k))), null, 2)}</pre>
          {canOverride && e.result !== "pass" && (
            <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3">
              <select aria-label="Override result" className={`${inputCls} w-40`} value={ov.result} onChange={(x) => setOv({ ...ov, result: x.target.value })}>
                <option value="pass">Pass</option><option value="not_applicable">Not applicable</option></select>
              <input aria-label="Rationale" className={`${inputCls} max-w-md`} placeholder="Rationale (at least 20 characters, e.g. department clarification ref.)" value={ov.rationale} onChange={(x) => setOv({ ...ov, rationale: x.target.value })} />
              <Button kind="secondary" disabled={ov.rationale.length < 20} onClick={() => act.run(() => api(`/tenders/${tid}/evaluations/${e.id}/override`, { json: ov }), reload)}>Record override</Button>
              <span className="text-xs text-muted">The computed result stays visible and still blocks readiness.</span>
            </div>
          )}
        </td></tr>
      )}
    </Fragment>
  );
}
