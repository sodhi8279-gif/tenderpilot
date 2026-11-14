import { Fragment, useState } from "react";
import { api } from "../../api";
import { useMe } from "../../App";
import { Button, Empty, ErrorBox, Loading, Notice, Panel, Status, useAction, useLoad } from "../../ui";
import type { Tender } from "./TenderPage";

type Cell = { result: string; explanation: string; missing: string[]; remedy: string | null; measure: string | null; threshold: string | null };
type Criterion = { key: string; label: string; text: string; status: string; source: { clause_no: string | null; page_index: number | null } | null;
  jv_rule: string; jv_rule_source: string; jv_rule_basis: string | null; cells: Record<string, Cell>; met_alone_by: string[] };
type Row = { criterion: string; result: string; explanation: string; contributions: { entity: string; result: string; explanation: string; measure: string | null }[];
  missing: string[]; remedies: string[]; jv_rule_source: string | null };
type Option = { kind: "single" | "jv"; title: string; status: string; counts: Record<string, number>; assumed_jv_rules: number; todo: string[];
  members: { legal_entity_id: number; legal_name: string; role: string; share_pct: string }[]; rows: Row[] };
type Plan = { cutoff?: string; headline?: string; message?: string; assumptions: string[]; entities: { id: number; legal_name: string }[];
  criteria: Criterion[]; options: Option[]; options_evaluated?: number; disclaimer?: string;
  jv: { permitted?: boolean | null; max_members?: number | null; lead_min_pct?: string | null; note?: string | null; split?: Record<string, unknown> | null } };

export default function Qualify({ t, onChange }: { t: Tender; onChange: () => void }) {
  const plan = useLoad<Plan>(() => api(`/tenders/${t.id}/qualification-plan`), [t.id]);
  if (plan.loading && !plan.data) return <Loading />;
  if (plan.error) return <ErrorBox error={plan.error} onRetry={plan.reload} />;
  const p = plan.data!;
  if (!p.criteria.length) return <Panel title="How to qualify"><Empty>{p.message || "No qualification rules yet."}</Empty></Panel>;
  const crit = Object.fromEntries(p.criteria.map((c) => [c.key, c]));
  return (
    <div className="space-y-5">
      <Panel title="How to qualify: one company or JV partners" actions={<Button kind="secondary" onClick={plan.reload}>Recalculate</Button>}>
        <p className="text-base font-medium">{p.headline}</p>
        <p className="mt-1 text-sm text-muted">Every criterion read from the tender is tested against each company in the vault on its own, then against every
          JV combination the tender allows ({p.options_evaluated} options compared, records valid on {p.cutoff}).</p>
        <JvSummary jv={p.jv} />
        {p.assumptions.length > 0 && <div className="mt-3"><Notice tone="warn"><ul className="list-disc pl-4">{p.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></Notice></div>}
        {p.entities.length < 2 && <div className="mt-3"><Notice>To test a JV, add the prospective partner under Company vault › Legal entities, with its verified turnover, works, CVs and machinery.</Notice></div>}
      </Panel>
      <Panel title="Options, best first">
        <div className="space-y-3">{p.options.map((o, i) => <OptionCard key={i} o={o} crit={crit} t={t} best={i === 0} onApplied={() => { plan.reload(); onChange(); }} />)}</div>
        <p className="mt-3 text-xs text-muted">{p.disclaimer}</p>
      </Panel>
      <Panel title="Who meets what on its own">
        <div className="overflow-x-auto"><table className="datagrid">
          <thead><tr><th>Criterion</th><th>How JV members combine</th>{p.entities.map((e) => <th key={e.id} className="min-w-40">{e.legal_name}</th>)}</tr></thead>
          <tbody>{p.criteria.map((c) => (
            <tr key={c.key}>
              <td className="max-w-md"><div className="text-xs uppercase tracking-wide text-muted">{c.label}{c.source?.clause_no && ` · clause ${c.source.clause_no}`}{c.status !== "approved" && " · awaiting confirmation"}</div>{c.text}</td>
              <td className="max-w-xs text-sm">{c.jv_rule} <Status s={c.jv_rule_source} />{c.jv_rule_basis && <div className="text-xs text-muted">{c.jv_rule_basis}</div>}</td>
              {p.entities.map((e) => { const cell = c.cells[String(e.id)]; return (
                <td key={e.id} className="max-w-xs text-xs"><Status s={cell?.result} /><div className="mt-1 text-muted">{cell?.explanation}</div></td>); })}
            </tr>))}</tbody>
        </table></div>
        <p className="mt-2 text-xs text-muted">"From JV clause" and "Assumed" sharing rules are used for planning only. Confirm them on the Eligibility tab (Review › How JV members combine) before the eligibility matrix relies on them.</p>
      </Panel>
    </div>
  );
}

function JvSummary({ jv }: { jv: Plan["jv"] }) {
  const s = (jv.split || {}) as Record<string, string | boolean>;
  const parts = [
    jv.permitted === true ? "JV permitted" : jv.permitted === false ? "JV not permitted" : "JV permission not found",
    jv.max_members ? `up to ${jv.max_members} members` : null,
    jv.lead_min_pct ? `lead share ≥ ${jv.lead_min_pct}%` : null,
    s.lead_min_pct ? `lead meets ≥ ${s.lead_min_pct}% of ${s.applies_to || "the"} criteria` : null,
    s.member_min_pct ? `each other member ≥ ${s.member_min_pct}%` : null,
    s.similar_work_mode === "lead_only" ? "similar work by the lead alone" : s.similar_work_mode === "any_member" ? "similar work by any member" : null,
    s.general_mode === "aggregate_sum" ? "partners' general experience added together" : null,
    s.specific_mode === "any_member" ? "specific experience from any one partner" : s.specific_mode === "lead_only" ? "specific experience by the lead alone" : null,
    s.applies_clause ? `(lead's share applies to clause ${s.applies_clause})` : null,
    s.net_worth_mode === "each_member" ? "each member's net worth" : null,
  ].filter(Boolean);
  return <p className="mt-2 text-sm"><span className="font-medium">JV clause: </span>{parts.join(" · ")}{jv.note && <span className="text-gap"> {jv.note}</span>}</p>;
}

function OptionCard({ o, crit, t, best, onApplied }: { o: Option; crit: Record<string, Criterion>; t: Tender; best: boolean; onApplied: () => void }) {
  const { can } = useMe();
  const [open, setOpen] = useState(best);
  const act = useAction();
  const current = o.kind === "single" && t.bidder_type === "entity" && t.bidder_entity_id === o.members[0].legal_entity_id;
  const apply = () => act.run(async () => {
    if (o.kind === "single") {
      await api(`/tenders/${t.id}`, { method: "PATCH", json: { row_version: t.row_version, bidder_type: "entity", bidder_entity_id: o.members[0].legal_entity_id, jv_id: null } });
    } else {
      const jv = await api<{ id: number }>("/jvs", { json: { name: `${o.members.map((m) => m.legal_name.replace(/\s*\(SYNTHETIC\)/, "")).join(" – ")} JV`, tender_id: t.id,
        members: o.members.map((m) => ({ legal_entity_id: m.legal_entity_id, role: m.role, share_pct: m.share_pct })) } });
      await api(`/tenders/${t.id}`, { method: "PATCH", json: { row_version: t.row_version, bidder_type: "jv", jv_id: jv.id, bidder_entity_id: null } });
    }
  }, onApplied);
  const canApply = can("tender.write") && (o.kind === "single" || can("vault.write"));
  return (
    <div className={best ? "rounded-md border-2 border-teal p-3" : "rounded-md border border-line p-3"}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="font-medium">{best && <span className="mr-2 text-xs uppercase tracking-wide text-teal">Best option</span>}{o.title}</div>
          <div className="text-sm text-muted">{o.kind === "jv" ? o.members.map((m) => `${m.legal_name}: ${m.role}, ${Number(m.share_pct)}% share`).join(" · ") : "Single bidder"}
            {" · "}{o.counts.pass} met, {o.counts.fail} failing, {o.counts.insufficient_evidence + o.counts.ambiguous} open{o.assumed_jv_rules > 0 && `, ${o.assumed_jv_rules} JV sharing rule(s) not yet confirmed`}</div>
        </div>
        <div className="flex items-center gap-2"><Status s={o.status} />
          <Button kind="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Criterion by criterion"}</Button>
          {canApply && !current && o.status !== "does_not_qualify" && <Button kind="secondary" disabled={act.busy} onClick={apply}>Bid this way</Button>}
          {current && <span className="text-xs text-muted">Current bidder</span>}
        </div>
      </div>
      <ErrorBox error={act.error} />
      {o.todo.length > 0 && <div className="mt-2 text-sm"><span className="font-medium">Still needed: </span><span className="text-gap">{o.todo.slice(0, 6).join("; ")}</span></div>}
      {open && (
        <div className="mt-3 overflow-x-auto"><table className="datagrid">
          <thead><tr><th>Criterion</th><th>Result</th><th>How it is met</th>{o.kind === "jv" && <th>Each partner's part</th>}</tr></thead>
          <tbody>{o.rows.map((r, i) => (
            <Fragment key={i}><tr>
              <td className="max-w-sm text-sm">{crit[r.criterion]?.text || "JV condition"}</td>
              <td><Status s={r.result} />{r.jv_rule_source && r.jv_rule_source !== "confirmed" && <div className="mt-1"><Status s={r.jv_rule_source} /></div>}</td>
              <td className="max-w-md text-sm">{r.explanation}{r.result !== "pass" && r.remedies.map((x) => <div key={x} className="text-muted">{x}</div>)}</td>
              {o.kind === "jv" && <td className="max-w-md text-xs">{r.contributions.map((c, j) => <div key={j} className="mb-1"><Status s={c.result} /> {c.explanation}</div>)}</td>}
            </tr></Fragment>))}</tbody>
        </table></div>
      )}
    </div>
  );
}
