import { useState } from "react";
import { api, blobUrl, download } from "../../api";
import { useMe } from "../../App";
import PdfBoxes from "./PdfBoxes";
import { Button, Empty, ErrorBox, fmtDate, inputCls, Loading, Notice, Panel, Status, Tabs, useAction, useLoad } from "../../ui";

type Tpl = { id: number; name: string; kind: string; builtin_key: string | null; envelope: string; placeholders: string[] };
type Gen = { id: number; template_id: number; name: string; revision: number; status: string; missing_fields: string[]; qa_issues: { type: string; text?: string }[];
  stale: boolean; envelope: string; signing_required: boolean; certification_required: string | null; signed_sha256: string | null;
  signed_has_digital_signature: boolean; created_at: string; docx_key: string | null };
const SOURCE: Record<string, (n: number) => string> = { builtin: () => "Built-in", docx: (n) => `Tender form (DOCX), ${n} fields`,
  pdf_form: (n) => `Fillable PDF form, ${n} field${n === 1 ? "" : "s"}`, pdf_overlay: (n) => `Scanned / flat PDF form, ${n} box${n === 1 ? "" : "es"}` };

export default function Studio({ tid }: { tid: number }) {
  const [tab, setTab] = useState("forms");
  return (
    <div>
      <Tabs tabs={[["forms", "Forms and letters"], ["technical", "Technical compliance"]]} value={tab} onChange={setTab} />
      <div className="mt-4">{tab === "forms" ? <Forms tid={tid} /> : <Technical tid={tid} />}</div>
    </div>
  );
}

function Forms({ tid }: { tid: number }) {
  const { can } = useMe();
  const tpls = useLoad<{ templates: Tpl[]; fields: Record<string, string> }>(() => api(`/tenders/${tid}/templates`), [tid]);
  const gens = useLoad<Gen[]>(() => api(`/tenders/${tid}/generated`), [tid]);
  const [map, setMap] = useState<number | null>(null);
  const [boxesFor, setBoxesFor] = useState<number | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const act = useAction();
  const generate = (id: number) => act.run(() => api(`/tenders/${tid}/templates/${id}/generate`, { json: {} }), gens.reload);
  const uploadTpl = (f: File | undefined) => {
    if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("name", f.name.replace(/\.(docx|pdf)$/i, "")); fd.append("envelope", "technical");
    act.run(async () => {
      const r = await api(`/tenders/${tid}/templates`, { form: fd });
      if (r.kind === "pdf_overlay") { setMap(null); setBoxesFor(r.id); } else { setBoxesFor(null); setMap(r.id); }
    }, tpls.reload);
  };
  if (!tpls.data) return <Loading />;
  return (
    <div className="space-y-5">
      <ErrorBox error={act.error} />
      <Notice>Use the tender's own prescribed forms first: upload them as DOCX with {"{{placeholders}}"}, as a fillable PDF, or as a scanned PDF (then click where each value goes), and confirm each mapping. Built-in letters are fallbacks. Nothing is ever marked signed until the signed copy is uploaded.</Notice>
      <Panel title="Templates" actions={can("docs.generate") && <label className="cursor-pointer text-sm font-medium text-teal underline">Upload tender form (DOCX or PDF)
        <input type="file" accept=".docx,.pdf" className="sr-only" onChange={(e) => { uploadTpl(e.target.files?.[0]); e.target.value = ""; }} /></label>}>
        <div className="overflow-x-auto"><table className="datagrid">
          <thead><tr><th>Template</th><th>Envelope</th><th>Source</th><th></th></tr></thead>
          <tbody>{tpls.data.templates.map((x) => (
            <tr key={x.id}><td>{x.name}</td><td>{x.envelope}</td><td className="text-sm text-muted">{(SOURCE[x.kind] || SOURCE.docx)(x.placeholders.length)}</td>
              <td className="whitespace-nowrap">{x.kind === "pdf_overlay" && can("docs.generate") && <Button kind="ghost" onClick={() => { setMap(null); setBoxesFor(boxesFor === x.id ? null : x.id); }}>Place boxes</Button>}
                {x.kind !== "builtin" && <Button kind="ghost" onClick={() => { setBoxesFor(null); setMap(map === x.id ? null : x.id); }}>Map fields</Button>}
                {can("docs.generate") && <Button kind="secondary" disabled={act.busy} onClick={() => generate(x.id)}>Generate</Button>}</td></tr>))}</tbody>
        </table></div>
        {boxesFor && <PdfBoxes key={boxesFor} tid={tid} templateId={boxesFor} fields={tpls.data.fields} onSaved={() => { const id = boxesFor; setBoxesFor(null); setMap(id); tpls.reload(); }} />}
        {map && <Mapping key={map} tid={tid} templateId={map} fields={tpls.data.fields} />}
      </Panel>
      <Panel title="Generated documents">
        {!gens.data?.length ? <Empty>Nothing generated yet.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Document</th><th>Status</th><th>Checks</th><th>Files</th><th>Signed copy</th></tr></thead>
            <tbody>{gens.data.map((g) => (
              <tr key={g.id}>
                <td>{g.name} <span className="text-muted">r{g.revision}</span><div className="text-xs text-muted">{g.envelope} envelope, {fmtDate(g.created_at)}</div></td>
                <td><Status s={g.status} />{g.stale && <div className="mt-1 text-xs text-fail">Stale: inputs changed, generate again</div>}</td>
                <td className="max-w-xs text-xs">{g.missing_fields.length ? <span className="text-fail">Missing: {g.missing_fields.join(", ")}</span> : <span className="text-pass">All fields filled</span>}
                  {g.qa_issues.filter((q) => q.type !== "missing_field").length > 0 && <div className="text-gap">{g.qa_issues.filter((q) => q.type !== "missing_field").length} layout warning(s)</div>}</td>
                <td className="whitespace-nowrap">
                  <Button kind="ghost" onClick={() => act.run(async () => setPreview(await blobUrl(`/tenders/${tid}/generated/${g.id}/pdf`)))}>Preview</Button>
                  {g.docx_key ? <Button kind="ghost" onClick={() => act.run(() => download(`/tenders/${tid}/generated/${g.id}/docx`, `${g.name} r${g.revision}.docx`))}>DOCX</Button>
                    : <Button kind="ghost" onClick={() => act.run(() => download(`/tenders/${tid}/generated/${g.id}/pdf`, `${g.name} r${g.revision}.pdf`))}>PDF</Button>}
                </td>
                <td className="text-sm">
                  {g.signed_sha256 ? <div className="text-xs">{g.signed_has_digital_signature ? "Digital signature present" : "Signed scan received"}<br />SHA-256 {g.signed_sha256.slice(0, 10)}…</div>
                    : g.signing_required ? <span className="text-xs text-gap">Awaiting signature{g.certification_required && ` and ${g.certification_required}`}</span> : <span className="text-xs text-muted">Not required</span>}
                  {can("docs.generate") && <label className="mt-1 block cursor-pointer text-xs font-medium text-teal underline">Upload signed PDF
                    <input type="file" accept="application/pdf" className="sr-only" onChange={(e) => {
                      const f = e.target.files?.[0]; if (!f) return; const fd = new FormData(); fd.append("file", f);
                      act.run(() => api(`/tenders/${tid}/generated/${g.id}/signed`, { form: fd }), gens.reload); e.target.value = ""; }} /></label>}
                  {g.signed_sha256 && g.status !== "verified" && can("vault.verify") && <Button kind="ghost" onClick={() => act.run(() => api(`/tenders/${tid}/generated/${g.id}/status`, { json: { status: "verified" } }), gens.reload)}>Mark verified</Button>}
                  {!g.signed_sha256 && g.status === "draft" && !g.missing_fields.length && <Button kind="ghost" onClick={() => act.run(() => api(`/tenders/${tid}/generated/${g.id}/status`, { json: { status: g.certification_required ? "awaiting_certification" : "awaiting_signature" } }), gens.reload)}>Send for signature</Button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      {preview && (
        <Panel title="Preview (draft watermark)" actions={<Button kind="ghost" onClick={() => { URL.revokeObjectURL(preview); setPreview(null); }}>Close</Button>}>
          <iframe title="Document preview" src={preview} className="h-[75vh] w-full rounded border border-line" />
        </Panel>
      )}
    </div>
  );
}

function Mapping({ tid, templateId, fields }: { tid: number; templateId: number; fields: Record<string, string> }) {
  const m = useLoad<{ placeholder: string; source_path: string | null; manual_value: string | null; confirmed: boolean }[]>(
    () => api(`/tenders/${tid}/templates/${templateId}/mappings`), [templateId]);
  const act = useAction();
  if (!m.data) return <Loading />;
  const save = () => act.run(() => api(`/tenders/${tid}/templates/${templateId}/mappings`, { method: "PUT",
    json: m.data!.map((x) => ({ placeholder: x.placeholder, source_path: x.source_path || null, manual_value: x.manual_value || null, confirmed: true })) }), m.reload);
  return (
    <div className="mt-4 rounded-md border border-line p-3">
      <ErrorBox error={act.error} />
      <div className="mb-2 text-sm font-medium">Field mapping: confirm every field before generating</div>
      {m.data.length === 0 ? <Empty>No fields found in this form yet. For a scanned PDF, place boxes first.</Empty> : (
        <table className="datagrid"><thead><tr><th>Field in form</th><th>Fill from</th><th>Or type a value</th><th>Confirmed</th></tr></thead>
          <tbody>{m.data.map((x, i) => (
            <tr key={x.placeholder}><td>{x.placeholder}</td>
              <td><select aria-label={`Source for ${x.placeholder}`} className={inputCls} value={x.source_path || ""} onChange={(e) => { const d = [...m.data!]; d[i] = { ...x, source_path: e.target.value }; m.setData(d); }}>
                <option value="">Manual value</option>{Object.entries(fields).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td><input aria-label={`Value for ${x.placeholder}`} className={inputCls} value={x.manual_value || ""} onChange={(e) => { const d = [...m.data!]; d[i] = { ...x, manual_value: e.target.value }; m.setData(d); }} /></td>
              <td>{x.confirmed ? "Yes" : <span className="text-gap">No</span>}</td></tr>))}</tbody></table>
      )}
      <Button className="mt-3" onClick={save} disabled={act.busy}>Confirm mapping</Button>
    </div>
  );
}

type Tech = { id: number; clause_ref: string; requirement_text: string; response: string; compliance: string; deviation_note: string | null;
  make_model: string | null; capacity: string | null; unit: string | null; needs_engineering_review: boolean; reviewed: boolean; requirement_id: number | null; evidence_id: number | null };

function Technical({ tid }: { tid: number }) {
  const { can } = useMe();
  const rows = useLoad<Tech[]>(() => api(`/tenders/${tid}/technical`), [tid]);
  const cons = useLoad<{ findings: { severity: string; message: string }[] }>(() => api(`/tenders/${tid}/technical/consistency`), [tid]);
  const act = useAction();
  const save = (r: Tech) => act.run(() => api(`/tenders/${tid}/technical/${r.id}`, { method: "PUT", json: r }), () => { rows.reload(); cons.reload(); });
  if (!rows.data) return <Loading />;
  return (
    <div className="space-y-5">
      <ErrorBox error={act.error} />
      <Panel title="Compliance matrix" actions={can("technical.write") && <>
        <Button kind="secondary" onClick={() => act.run(() => api(`/tenders/${tid}/technical/seed`, { json: {} }), rows.reload)}>Add rows from requirements</Button>
        <Button kind="secondary" onClick={() => act.run(() => api(`/tenders/${tid}/technical`, { json: { clause_ref: "", requirement_text: "New item" } }), rows.reload)}>Add row</Button></>}>
        {!rows.data.length ? <Empty>No rows yet. Add them from the extracted requirements.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Clause</th><th>Requirement</th><th>Our response</th><th>Make / model, capacity</th><th>Compliance</th><th>Engineering review</th></tr></thead>
            <tbody>{rows.data.map((r, i) => {
              const upd = (p: Partial<Tech>) => { const d = [...rows.data!]; d[i] = { ...r, ...p }; rows.setData(d); };
              return (
                <tr key={r.id}>
                  <td className="w-20"><input aria-label="Clause" className={inputCls} value={r.clause_ref} onChange={(e) => upd({ clause_ref: e.target.value })} onBlur={() => save(rows.data![i])} /></td>
                  <td className="max-w-xs text-xs">{r.requirement_text}</td>
                  <td><textarea aria-label="Response" rows={2} className={inputCls} value={r.response} onChange={(e) => upd({ response: e.target.value })} onBlur={() => save(rows.data![i])} /></td>
                  <td className="space-y-1"><input aria-label="Make and model" placeholder="Make / model" className={inputCls} value={r.make_model || ""} onChange={(e) => upd({ make_model: e.target.value })} onBlur={() => save(rows.data![i])} />
                    <div className="flex gap-1"><input aria-label="Capacity" placeholder="Capacity" className={inputCls} value={r.capacity || ""} onChange={(e) => upd({ capacity: e.target.value })} onBlur={() => save(rows.data![i])} />
                      <input aria-label="Unit" placeholder="Unit" className={`${inputCls} w-20`} value={r.unit || ""} onChange={(e) => upd({ unit: e.target.value })} onBlur={() => save(rows.data![i])} /></div></td>
                  <td><select aria-label="Compliance" className={inputCls} value={r.compliance} onChange={(e) => { const n = { ...r, compliance: e.target.value }; upd(n); if (!["deviation", "exclusion"].includes(n.compliance)) save(n); }}>
                    <option value="pending">Not stated</option><option value="complies">Complies</option><option value="deviation">Deviation</option><option value="exclusion">Exclusion</option><option value="not_applicable">Not applicable</option></select>
                    {["deviation", "exclusion"].includes(r.compliance) && <textarea aria-label="Deviation note" rows={2} placeholder="Describe the deviation" className={`${inputCls} mt-1`} value={r.deviation_note || ""}
                      onChange={(e) => upd({ deviation_note: e.target.value })} onBlur={() => save(rows.data![i])} />}</td>
                  <td className="text-sm"><label className="flex items-center gap-1.5"><input type="checkbox" checked={r.needs_engineering_review} onChange={(e) => save({ ...r, needs_engineering_review: e.target.checked })} /> Needed</label>
                    {r.needs_engineering_review && <label className="flex items-center gap-1.5"><input type="checkbox" checked={r.reviewed} onChange={(e) => save({ ...r, reviewed: e.target.checked })} /> Reviewed</label>}</td>
                </tr>);
            })}</tbody>
          </table></div>
        )}
      </Panel>
      <Panel title="Cross-document consistency" actions={<Button kind="secondary" onClick={cons.reload}>Check again</Button>}>
        {!cons.data?.findings.length ? <p className="text-sm text-pass">No inconsistencies found in capacities, makes, tender ID or document freshness.</p> :
          <ul className="space-y-1.5 text-sm">{cons.data.findings.map((f, i) => <li key={i}><Status s={f.severity} /> {f.message}</li>)}</ul>}
      </Panel>
    </div>
  );
}
