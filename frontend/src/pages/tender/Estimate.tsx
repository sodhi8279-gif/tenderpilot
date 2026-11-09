import { useState } from "react";
import { api, download, inr } from "../../api";
import { useMe } from "../../App";
import { Button, Empty, ErrorBox, fmtDate, inputCls, Loading, Notice, Panel, Status, useAction, useLoad } from "../../ui";

type Item = { id: number; row_index: number; item_code: string; description: string; unit: string; quantity: string; rate_cell: string; trade: string;
  flags: string[]; rate?: string | null; amount?: string | null; warnings?: string[] };
type BOQ = { id: number; filename: string; sheet_name: string; mode: string; rounding_places: number; roundtrip_supported: boolean; roundtrip_issues: string[];
  items: Item[]; prices_visible: boolean };
type Src = { id: number; resource: string; unit: string; rate: string; source_type: string; supplier: string | null; source_url: string | null; quote_date: string | null;
  confidence: number; stale: boolean; gst_pct: string; gst_recoverable: boolean };
type Quote = { id: number; version_no: number; status: string; total: string; total_words: string; snapshot_hash: string; row_version: number; invalidated_reason: string | null;
  snapshot: { warnings?: string[] }; export_report: { ok?: boolean; error?: string; cells_written?: number; filename?: string; note?: string; manual_mapping?: { cell: string; item: string; rate: string }[] };
  export_key: string | null; approvals: { stage: string; user_id: number; decision: string; valid: boolean; comment: string | null; created_at: string }[] };

export default function Estimate({ tid }: { tid: number }) {
  const { can } = useMe();
  const boqs = useLoad<BOQ[]>(() => api(`/tenders/${tid}/boqs`), [tid]);
  const act = useAction();
  const [item, setItem] = useState<Item | null>(null);
  if (boqs.loading && !boqs.data) return <Loading />;
  const b = boqs.data?.[0];
  const imp = (f?: File) => { if (!f) return; const fd = new FormData(); fd.append("file", f); act.run(() => api(`/tenders/${tid}/boqs`, { form: fd }), boqs.reload); };
  return (
    <div className="space-y-5">
      <ErrorBox error={act.error} />
      {!b ? (
        <Panel title="Import the official BOQ">
          {can("price.write") ? <>
            <p className="mb-3 text-sm text-muted">Upload the department's BOQ workbook as issued. TenderPilot keeps the original untouched and writes rates only into a verified copy.</p>
            <input type="file" accept=".xlsx,.xlsm,.xls" aria-label="BOQ workbook" onChange={(e) => imp(e.target.files?.[0])} /></>
            : <Empty>Only estimators can import and price the BOQ.</Empty>}
        </Panel>
      ) : (
        <>
          {!b.roundtrip_supported && <Notice tone="warn">Rates cannot be written into this workbook safely: {b.roundtrip_issues.join(" ")} A cell-by-cell mapping is produced for manual entry instead.</Notice>}
          {!b.prices_visible && <Notice>Your role cannot see prices. Quantities and items are shown for reference.</Notice>}
          <Panel title={`BOQ: ${b.filename}, sheet ${b.sheet_name} (${b.items.length} items)`}>
            <div className="max-h-[60vh] overflow-auto"><table className="datagrid">
              <thead><tr><th>Item</th><th>Description</th><th>Unit</th><th className="text-right">Quantity</th>
                {b.prices_visible && <><th className="text-right">Rate</th><th className="text-right">Amount</th></>}<th>Checks</th><th></th></tr></thead>
              <tbody>{b.items.map((it) => (
                <tr key={it.id}>
                  <td>{it.item_code || it.row_index}</td><td className="max-w-md">{it.description}<div className="text-xs text-muted">{it.trade}, cell {it.rate_cell}</div></td>
                  <td>{it.unit}</td><td className="text-right tabular-nums">{it.quantity}</td>
                  {b.prices_visible && <><td className="text-right tabular-nums">{inr(it.rate)}</td><td className="text-right tabular-nums">{inr(it.amount)}</td></>}
                  <td className="max-w-xs text-xs">{it.flags.map((f) => <div key={f} className="text-gap">{f}</div>)}{it.warnings?.map((w) => <div key={w} className="text-ambig">{w}</div>)}</td>
                  <td>{can("price.write") && <Button kind="ghost" onClick={() => setItem(it)}>Rate analysis</Button>}</td>
                </tr>))}</tbody>
            </table></div>
          </Panel>
          {item && <Analysis tid={tid} item={item} close={() => { setItem(null); boqs.reload(); }} />}
          {can("price.read") && <Rates tid={tid} />}
          {can("price.read") && <Quotes tid={tid} boq={b} />}
        </>
      )}
    </div>
  );
}

function Rates({ tid }: { tid: number }) {
  const { can } = useMe();
  const r = useLoad<Src[]>(() => api(`/tenders/${tid}/rates`), [tid]);
  const blank = { resource: "", resource_type: "material", unit: "", rate: "", source_type: "quotation", supplier: "", source_url: "", quote_date: "", valid_until: "", gst_pct: "18", gst_recoverable: true, location: "", specification: "" };
  const [f, setF] = useState(blank);
  const act = useAction();
  return (
    <Panel title="Basic rates and their sources">
      <Notice>Live market research is not connected. Enter quotations or public references with their source and date; assumptions are flagged and need approval comments.</Notice>
      <ErrorBox error={act.error} />
      {can("price.write") && (
        <form className="mb-4 grid gap-2 sm:grid-cols-4" onSubmit={(e) => { e.preventDefault(); act.run(() => api(`/tenders/${tid}/rates`, { json: { ...f, quote_date: f.quote_date || null, valid_until: f.valid_until || null } }), () => { setF(blank); r.reload(); }); }}>
          <input required aria-label="Resource" placeholder="Resource, e.g. Cement OPC 53" className={inputCls} value={f.resource} onChange={(e) => setF({ ...f, resource: e.target.value })} />
          <input aria-label="Specification" placeholder="Specification" className={inputCls} value={f.specification} onChange={(e) => setF({ ...f, specification: e.target.value })} />
          <input required aria-label="Unit" placeholder="Unit" className={inputCls} value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} />
          <input required aria-label="Rate" placeholder="Rate (₹, excl. GST)" inputMode="decimal" className={inputCls} value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} />
          <select aria-label="Source type" className={inputCls} value={f.source_type} onChange={(e) => setF({ ...f, source_type: e.target.value })}>
            <option value="quotation">Supplier quotation</option><option value="public_reference">Public reference (SOR, price list)</option><option value="user_input">Our own record</option><option value="assumption">Assumption</option></select>
          <input aria-label="Supplier" placeholder="Supplier" className={inputCls} value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} />
          <input aria-label="Source URL" placeholder="Source URL" className={inputCls} value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} />
          <input aria-label="Location" placeholder="Location / freight basis" className={inputCls} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} />
          <label className="text-xs">Quotation date<input type="date" className={inputCls} value={f.quote_date} onChange={(e) => setF({ ...f, quote_date: e.target.value })} /></label>
          <label className="text-xs">Valid until<input type="date" className={inputCls} value={f.valid_until} onChange={(e) => setF({ ...f, valid_until: e.target.value })} /></label>
          <label className="text-xs">GST %<input className={inputCls} value={f.gst_pct} onChange={(e) => setF({ ...f, gst_pct: e.target.value })} /></label>
          <label className="flex items-end gap-2 text-xs"><input type="checkbox" checked={f.gst_recoverable} onChange={(e) => setF({ ...f, gst_recoverable: e.target.checked })} /> Input GST recoverable</label>
          <Button className="sm:col-span-4 justify-self-start" disabled={act.busy}>Add rate</Button>
        </form>
      )}
      {!r.data?.length ? <Empty>No rates recorded.</Empty> : (
        <div className="max-h-80 overflow-auto"><table className="datagrid"><thead><tr><th>#</th><th>Resource</th><th>Rate</th><th>Source</th><th>Date</th><th>Confidence</th></tr></thead>
          <tbody>{r.data.map((s) => <tr key={s.id}><td>{s.id}</td><td>{s.resource}</td><td className="tabular-nums">{inr(s.rate)} / {s.unit}<div className="text-xs text-muted">GST {s.gst_pct}% {s.gst_recoverable ? "recoverable" : "is cost"}</div></td>
            <td className="text-sm">{s.source_type.replace("_", " ")}{s.supplier && `, ${s.supplier}`}{s.source_url && <div className="max-w-[14rem] truncate text-xs"><a className="text-teal underline" href={s.source_url} target="_blank" rel="noreferrer noopener">{s.source_url}</a></div>}</td>
            <td>{s.quote_date || "—"}{s.stale && <div className="text-xs text-fail">Expired</div>}</td><td>{Math.round(s.confidence * 100)}%</td></tr>)}</tbody></table></div>
      )}
    </Panel>
  );
}

type Comp = { type: string; description: string; qty: string; rate: string; rate_source_id: string; wastage_pct: string };
const OH: [string, string][] = [["site_overhead", "Site overheads"], ["ho_overhead", "HO overheads"], ["contingency", "Contingency"], ["risk", "Risk"],
  ["finance_cost", "Finance / working capital"], ["insurance", "Insurance"], ["statutory", "Statutory costs"]];

function Analysis({ tid, item, close }: { tid: number; item: Item; close: () => void }) {
  const cur = useLoad<{ analysis: { components: Comp[]; overheads: Record<string, string>; margin_pct: string; margin_mode: string; output_qty: string; breakdown: Record<string, unknown>; computed_rate: string; warnings: string[] } | null }>(
    () => api(`/tenders/${tid}/boq-items/${item.id}/analysis`), [item.id]);
  const srcs = useLoad<Src[]>(() => api(`/tenders/${tid}/rates`), [tid]);
  const [form, setForm] = useState<null | { components: Comp[]; overheads: Record<string, string>; margin_pct: string; margin_mode: string; output_qty: string }>(null);
  const [result, setResult] = useState<null | { computed_rate: string; breakdown: Record<string, unknown>; warnings: string[] }>(null);
  const act = useAction();
  if (!cur.data || !srcs.data) return <Loading />;
  const a = cur.data.analysis;
  const f = form || { components: (a?.components || [{ type: "material", description: "", qty: "1", rate: "", rate_source_id: "", wastage_pct: "0" }]).map((c) => ({ ...c, rate_source_id: String(c.rate_source_id || ""), rate: c.rate || "", wastage_pct: c.wastage_pct || "0" })),
    overheads: a?.overheads || {}, margin_pct: String(a?.margin_pct || "0"), margin_mode: a?.margin_mode || "markup_on_cost", output_qty: String(a?.output_qty || "1") };
  const set = (p: Partial<typeof f>) => setForm({ ...f, ...p });
  const setC = (i: number, p: Partial<Comp>) => set({ components: f.components.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const save = () => act.run(async () => {
    const r = await api(`/tenders/${tid}/boq-items/${item.id}/analysis`, { method: "PUT", json: { ...f,
      components: f.components.map((c) => ({ ...c, rate_source_id: c.rate_source_id ? Number(c.rate_source_id) : null, rate: c.rate_source_id ? null : c.rate })) } });
    setResult(r);
  });
  const shown = result || (a ? { computed_rate: a.computed_rate, breakdown: a.breakdown, warnings: a.warnings } : null);
  const bd = shown?.breakdown as Record<string, string> | undefined;
  return (
    <Panel title={`Rate analysis: item ${item.item_code || item.row_index}, per ${item.unit}`} actions={<Button kind="ghost" onClick={close}>Close</Button>}>
      <p className="mb-3 text-sm">{item.description}</p>
      <ErrorBox error={act.error} />
      <table className="datagrid mb-3"><thead><tr><th>Type</th><th>Component</th><th>Qty per output</th><th>Wastage %</th><th>Rate source</th><th>Or rate</th><th></th></tr></thead>
        <tbody>{f.components.map((c, i) => (
          <tr key={i}>
            <td><select aria-label="Type" className={inputCls} value={c.type} onChange={(e) => setC(i, { type: e.target.value })}>
              {["material", "labour", "equipment", "transport", "subcontract", "fuel_power"].map((x) => <option key={x}>{x}</option>)}</select></td>
            <td><input aria-label="Component" className={inputCls} value={c.description} onChange={(e) => setC(i, { description: e.target.value })} /></td>
            <td><input aria-label="Quantity" className={`${inputCls} w-24`} value={c.qty} onChange={(e) => setC(i, { qty: e.target.value })} /></td>
            <td><input aria-label="Wastage" className={`${inputCls} w-20`} value={c.wastage_pct} onChange={(e) => setC(i, { wastage_pct: e.target.value })} /></td>
            <td><select aria-label="Rate source" className={inputCls} value={c.rate_source_id} onChange={(e) => setC(i, { rate_source_id: e.target.value })}>
              <option value="">Enter rate</option>{srcs.data!.map((s) => <option key={s.id} value={s.id}>#{s.id} {s.resource} {inr(s.rate)}/{s.unit}</option>)}</select></td>
            <td><input aria-label="Rate" disabled={!!c.rate_source_id} className={`${inputCls} w-28`} value={c.rate} onChange={(e) => setC(i, { rate: e.target.value })} /></td>
            <td><Button kind="ghost" onClick={() => set({ components: f.components.filter((_, j) => j !== i) })}>Remove</Button></td>
          </tr>))}</tbody></table>
      <Button kind="secondary" onClick={() => set({ components: [...f.components, { type: "labour", description: "", qty: "1", rate: "", rate_source_id: "", wastage_pct: "0" }] })}>Add component</Button>
      <div className="mt-4 grid gap-3 sm:grid-cols-4">
        <label className="text-xs">Analysis output quantity<input className={inputCls} value={f.output_qty} onChange={(e) => set({ output_qty: e.target.value })} /></label>
        {OH.map(([k, l]) => <label key={k} className="text-xs">{l} %<input className={inputCls} value={f.overheads[k] || ""} onChange={(e) => set({ overheads: { ...f.overheads, [k]: e.target.value } })} /></label>)}
        <label className="text-xs">Margin %<input className={inputCls} value={f.margin_pct} onChange={(e) => set({ margin_pct: e.target.value })} /></label>
        <label className="text-xs">Margin basis<select className={inputCls} value={f.margin_mode} onChange={(e) => set({ margin_mode: e.target.value })}>
          <option value="markup_on_cost">Markup on cost</option><option value="margin_on_sales">Margin on sales price</option></select></label>
      </div>
      <Button className="mt-4" onClick={save} disabled={act.busy}>Calculate and save rate</Button>
      {shown && bd && (
        <div className="mt-4 rounded-md bg-[#fafaf7] p-3 text-sm">
          <div className="text-lg font-semibold">{inr(shown.computed_rate)} per {item.unit}</div>
          <div className="text-xs text-muted">Direct cost {inr(bd.direct_per_unit, 4)}, overheads {bd.overhead_total_pct}% on direct cost, cost {inr(bd.cost_per_unit, 4)}; {bd.formula} with {bd.margin_pct}%. Unrounded {bd.unrounded_price}.</div>
          <div className="mt-1 text-xs text-muted">{bd.note}</div>
          {shown.warnings.map((w) => <div key={w} className="mt-1 text-xs text-ambig">{w}</div>)}
        </div>
      )}
    </Panel>
  );
}

function Quotes({ tid, boq }: { tid: number; boq: BOQ }) {
  const { can, me } = useMe();
  const qs = useLoad<Quote[]>(() => api(`/tenders/${tid}/quotes`), [tid]);
  const act = useAction();
  const [comment, setComment] = useState("");
  const [scen, setScen] = useState<null | { base: Record<string, string>; cases: { name: string; cost: string; margin: string; margin_pct_of_price: string; notes: string[] }[] }>(null);
  const decide = (q: Quote, stage: string, decision: string) => act.run(() => api(`/tenders/${tid}/quotes/${q.id}/${stage}`, { json: { decision, comment: comment || null, row_version: q.row_version } }), qs.reload);
  const runScen = (q: Quote) => act.run(async () => setScen(await api(`/tenders/${tid}/quotes/${q.id}/scenarios`, { json: { interest_pct_pa: "10", cases: [
    { name: "Steel and cement +8%", rate_change_pct: { civil: "8" } }, { name: "Pumps and panels +5%", rate_change_pct: { mechanical: "5", electrical: "5" } },
    { name: "3-month delay", delay_months: "3", monthly_time_cost: "450000" }, { name: "90-day payment cycle, 5% retention", payment_cycle_days: "90", retention_pct: "5", retention_months: "12" }] } })));
  const latest = qs.data?.[0];
  return (
    <Panel title="Quote versions and approval" actions={can("price.write") && <Button onClick={() => act.run(() => api(`/tenders/${tid}/boqs/${boq.id}/quotes`, { json: {} }), qs.reload)}>Freeze current rates as a quote</Button>}>
      <ErrorBox error={act.error} />
      {!qs.data?.length ? <Empty>No quote yet. Price every item, then freeze the rates as a quote version for review.</Empty> : (
        <div className="space-y-4">
          {qs.data.map((q) => (
            <div key={q.id} className={`rounded-md border p-3 ${q.id === latest?.id ? "border-teal" : "border-line opacity-80"}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div><span className="font-semibold">Quote v{q.version_no}</span> <Status s={q.status} /></div>
                <div className="text-right"><div className="text-lg font-semibold tabular-nums">{inr(q.total)}</div><div className="text-xs text-muted">{q.total_words}</div></div>
              </div>
              {q.invalidated_reason && <p className="text-sm text-fail">{q.invalidated_reason}</p>}
              {q.snapshot.warnings?.length ? <ul className="mt-1 text-xs text-ambig">{q.snapshot.warnings.map((w) => <li key={w}>{w}</li>)}</ul> : null}
              <div className="mt-2 text-xs text-muted">Snapshot {q.snapshot_hash.slice(0, 16)}… {q.approvals.map((a) => `${a.stage.replace("_", " ")} ${a.decision} ${fmtDate(a.created_at)}${a.valid ? "" : " (withdrawn)"}`).join("; ")}</div>
              {q.id === latest?.id && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {(q.status === "draft" && can("quote.estimator_review")) || (q.status === "estimator_reviewed" && can("quote.approve")) ?
                    <input aria-label="Approval comment" className={`${inputCls} max-w-md`} placeholder={q.snapshot.warnings?.length ? "Explain how each rate warning was resolved" : "Comment (optional)"} value={comment} onChange={(e) => setComment(e.target.value)} /> : null}
                  {q.status === "draft" && can("quote.estimator_review") && <><Button onClick={() => decide(q, "estimator-review", "approved")}>Estimator: reviewed</Button><Button kind="danger" onClick={() => decide(q, "estimator-review", "rejected")}>Reject</Button></>}
                  {q.status === "estimator_reviewed" && can("quote.approve") && (q.approvals.find((a) => a.stage === "estimator_review")?.user_id === me.user.id
                    ? <span className="text-sm text-muted">A different person must give final approval.</span>
                    : <><Button onClick={() => decide(q, "approve", "approved")}>Approve quote</Button><Button kind="danger" onClick={() => decide(q, "approve", "rejected")}>Reject</Button></>)}
                  {q.status === "approved" && can("price.write") && <Button kind="secondary" onClick={() => act.run(() => api(`/tenders/${tid}/quotes/${q.id}/export`, { json: {} }), qs.reload)}>Write rates into official workbook</Button>}
                  {q.export_key && <Button kind="secondary" onClick={() => act.run(() => download(`/tenders/${tid}/quotes/${q.id}/export`, q.export_report.filename || "BOQ_priced.xlsx"))}>Download priced workbook</Button>}
                  <Button kind="ghost" onClick={() => runScen(q)}>Scenarios</Button>
                </div>
              )}
              {q.export_report.ok && <p className="mt-2 text-xs text-pass">Verified: {q.export_report.cells_written} rate cells written; every other cell, formula, sheet and protection setting unchanged. {q.export_report.note}</p>}
              {q.export_report.error && <div className="mt-2 text-xs"><p className="text-fail">{q.export_report.error}</p>
                {q.export_report.manual_mapping && <details className="mt-1"><summary className="cursor-pointer">Manual entry mapping ({q.export_report.manual_mapping.length} cells)</summary>
                  <table className="datagrid mt-1"><tbody>{q.export_report.manual_mapping.map((m) => <tr key={m.cell}><td>{m.cell}</td><td>Item {m.item}</td><td>{m.rate}</td></tr>)}</tbody></table></details>}</div>}
            </div>
          ))}
        </div>
      )}
      {scen && (
        <div className="mt-4"><div className="mb-1 text-sm font-medium">Scenario comparison (assumptions shown; edit them in the API or a future release)</div>
          <table className="datagrid"><thead><tr><th>Case</th><th>Cost</th><th>Margin</th><th>Margin % of price</th><th>Working</th></tr></thead>
            <tbody><tr><td>Base</td><td>{inr(scen.base.cost, 0)}</td><td>{inr(scen.base.margin, 0)}</td><td></td><td></td></tr>
              {scen.cases.map((c) => <tr key={c.name}><td>{c.name}</td><td>{inr(c.cost, 0)}</td><td className={Number(c.margin) < 0 ? "text-fail" : ""}>{inr(c.margin, 0)}</td><td>{c.margin_pct_of_price}%</td><td className="text-xs text-muted">{c.notes.join("; ")}</td></tr>)}</tbody></table></div>
      )}
    </Panel>
  );
}
