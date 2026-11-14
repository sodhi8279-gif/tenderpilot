import { useState } from "react";
import { api, blobUrl } from "../api";
import { useMe } from "../App";
import { Button, Empty, ErrorBox, Field, fmtDay, inputCls, Loading, Notice, Panel, Status, Tabs, useAction, useLoad } from "../ui";

type Entity = { id: number; legal_name: string; entity_type: string; registered_address: string; pan: string | null; gstin: string | null;
  signatory_name: string | null; signatory_designation: string | null };
type Evid = { id: number; legal_entity_id: number; entity_name: string; category: string; title: string; issuer: string | null; subject_name_on_document: string | null;
  issue_date: string | null; expiry_date: string | null; fields: Record<string, string>; verification_state: string; issues: string[]; version: number; sha256: string | null;
  filename: string | null; name_mismatch: boolean };

// Suggested structured fields per category (used by the eligibility engine).
const FIELDS: Record<string, string[]> = {
  turnover_certificate: ["fy", "turnover"], audited_accounts: ["fy", "turnover", "net_worth"], net_worth: ["net_worth", "as_of"],
  solvency: ["amount"], existing_commitments: ["amount"],
  completion_certificate: ["work_name", "scope_of_work", "dimensions", "employer", "executed_value", "completion_date", "status", "completed_pct", "is_similar"],
  performance_certificate: ["work_name", "scope_of_work", "dimensions", "executed_value", "completion_date", "status", "is_similar"],
  personnel: ["person_name", "qualification", "discipline", "experience_years", "current_designation", "employment"],
  machinery: ["equipment_type", "quantity", "capacity", "capacity_unit", "ownership", "make_model", "registration_no"],
  executed_quantity: ["work_name", "item", "quantity", "unit", "fy", "dia_mm", "material"],
  registration: ["authority", "class", "registration_no"],
};
const HINT: Record<string, string> = { qualification: "e.g. B.E. Civil, Diploma Mechanical", experience_years: "total years, digits",
  employment: "on rolls / to be engaged", equipment_type: "e.g. Hydraulic excavator, Batching plant", capacity_unit: "MT / cum/hr / kVA / TPH",
  ownership: "owned / leased / hired", item: "e.g. DI pipe laying, Earthwork, RCC", unit: "km / m / cum / sqm / MT", dia_mm: "pipe diameter in mm",
  class: "as printed, e.g. I, I(A), Class-II", quantity: "digits only",
  scope_of_work: "as printed, e.g. RCC retaining wall with anchor bolts", dimensions: "sizes on the certificate, e.g. height 8 m; length 60 m",
  current_designation: "e.g. Site Engineer, Safety Officer", is_similar: "per tender: confirm in the eligibility matrix" };

export default function Vault() {
  const [tab, setTab] = useState("evidence");
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-2xl font-semibold tracking-tight">Company vault</h1>
      <p className="mb-4 text-sm text-muted">Verified records reused across tenders. Originals are stored unchanged with their hash; replacements keep the history.</p>
      <Tabs tabs={[["evidence", "Documents and credentials"], ["entities", "Legal entities"], ["jvs", "Joint ventures"]]} value={tab} onChange={setTab} />
      <div className="mt-4">{tab === "evidence" ? <Evidence /> : tab === "entities" ? <Entities /> : <JVs />}</div>
    </div>
  );
}

function Evidence() {
  const { can } = useMe();
  const ents = useLoad<Entity[]>(() => api("/entities"));
  const cats = useLoad<string[]>(() => api("/evidence/categories"));
  const [filter, setFilter] = useState({ entity: "", category: "" });
  const ev = useLoad<Evid[]>(() => api(`/evidence?${new URLSearchParams({ ...(filter.entity && { entity_id: filter.entity }), ...(filter.category && { category: filter.category }) })}`), [filter.entity, filter.category]);
  const [preview, setPreview] = useState<string | null>(null);
  const [replace, setReplace] = useState<Evid | null>(null);
  const act = useAction();
  if (!ents.data || !cats.data) return <Loading />;
  if (!ents.data.length) return <Empty>Add a legal entity first; every document belongs to exactly one entity.</Empty>;
  return (
    <div className="space-y-5">
      {can("vault.write") && <Upload ents={ents.data} cats={cats.data} replace={replace} done={() => { setReplace(null); ev.reload(); }} />}
      <ErrorBox error={act.error} />
      <Panel title="Records" actions={<>
        <select aria-label="Filter by entity" className={`${inputCls} w-56`} value={filter.entity} onChange={(e) => setFilter({ ...filter, entity: e.target.value })}>
          <option value="">All entities</option>{ents.data.map((x) => <option key={x.id} value={x.id}>{x.legal_name}</option>)}</select>
        <select aria-label="Filter by category" className={`${inputCls} w-48`} value={filter.category} onChange={(e) => setFilter({ ...filter, category: e.target.value })}>
          <option value="">All categories</option>{cats.data.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select></>}>
        {!ev.data?.length ? <Empty>No records in this view.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Document</th><th>Entity</th><th>Validity</th><th>Values</th><th>Status</th><th></th></tr></thead>
            <tbody>{ev.data.map((r) => (
              <tr key={r.id}>
                <td className="max-w-xs">{r.title}<div className="text-xs text-muted">{r.category.replace(/_/g, " ")}, v{r.version}{r.issuer && `, ${r.issuer}`}</div>
                  {r.issues.map((i) => <div key={i} className="text-xs text-gap">{i}</div>)}</td>
                <td className="text-sm">{r.entity_name}{r.name_mismatch && <div className="text-xs text-fail">Name on document: {r.subject_name_on_document}</div>}</td>
                <td className="text-sm">{r.expiry_date ? `Expires ${fmtDay(r.expiry_date)}` : "No expiry"}</td>
                <td className="max-w-xs text-xs">{Object.entries(r.fields).map(([k, v]) => <div key={k}>{k.replace(/_/g, " ")}: {String(v)}</div>)}</td>
                <td><Status s={r.verification_state} /></td>
                <td className="whitespace-nowrap">
                  {r.sha256 && <Button kind="ghost" onClick={() => act.run(async () => setPreview(await blobUrl(`/evidence/${r.id}/file`)))}>View</Button>}
                  {can("vault.verify") && r.verification_state !== "verified" && r.sha256 && <Button kind="ghost" onClick={() => act.run(() => api(`/evidence/${r.id}/verify`, { json: { state: "verified" } }), ev.reload)}>Verify</Button>}
                  {can("vault.verify") && r.verification_state === "unverified" && <Button kind="ghost" onClick={() => act.run(() => api(`/evidence/${r.id}/verify`, { json: { state: "rejected" } }), ev.reload)}>Reject</Button>}
                  {can("vault.write") && <Button kind="ghost" onClick={() => { setReplace(r); window.scrollTo({ top: 0 }); }}>Replace</Button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      {preview && <Panel title="Original document" actions={<Button kind="ghost" onClick={() => { URL.revokeObjectURL(preview); setPreview(null); }}>Close</Button>}>
        <iframe title="Evidence document" src={preview} className="h-[70vh] w-full rounded border border-line" /></Panel>}
    </div>
  );
}

function Upload({ ents, cats, replace, done }: { ents: Entity[]; cats: string[]; replace: Evid | null; done: () => void }) {
  const init = () => ({ legal_entity_id: String(replace?.legal_entity_id || ents[0].id), category: replace?.category || "completion_certificate", title: replace?.title || "",
    issuer: replace?.issuer || "", subject_name_on_document: "", issue_date: "", expiry_date: "", fields: { ...(replace?.fields || {}) } as Record<string, string> });
  const [f, setF] = useState(init);
  const [key, setKey] = useState(replace?.id);
  const [file, setFile] = useState<File | null>(null);
  const act = useAction();
  if (key !== replace?.id) { setKey(replace?.id); setF(init()); }
  const fieldKeys = FIELDS[f.category] || [];
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const fd = new FormData();
    Object.entries(f).forEach(([k, v]) => { if (k !== "fields" && v) fd.append(k, String(v)); });
    const fields = Object.fromEntries(Object.entries(f.fields).filter(([, v]) => v !== "").map(([k, v]) => [k, k === "is_similar" ? v === "yes" : v]));
    fd.append("fields", JSON.stringify(fields));
    if (replace) fd.append("replaces_id", String(replace.id));
    if (file) fd.append("file", file);
    act.run(() => api("/evidence", { form: fd }), () => { setF(init()); setFile(null); done(); });
  };
  return (
    <Panel title={replace ? `Replace: ${replace.title}` : "Add a document"}>
      <ErrorBox error={act.error} />
      <Notice>Enter values exactly as printed on the document. TenderPilot does not create or alter certificates; records stay unverified until a reviewer checks them against the original.</Notice>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-3">
        <Field label="Legal entity"><select className={inputCls} value={f.legal_entity_id} onChange={(e) => setF({ ...f, legal_entity_id: e.target.value })}>{ents.map((x) => <option key={x.id} value={x.id}>{x.legal_name}</option>)}</select></Field>
        <Field label="Category"><select className={inputCls} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{cats.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select></Field>
        <Field label="Title"><input required className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Issued by"><input className={inputCls} value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} /></Field>
        <Field label="Name printed on document" hint="Leave blank if identical to the entity's legal name."><input className={inputCls} value={f.subject_name_on_document} onChange={(e) => setF({ ...f, subject_name_on_document: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-2"><Field label="Issue date"><input type="date" className={inputCls} value={f.issue_date} onChange={(e) => setF({ ...f, issue_date: e.target.value })} /></Field>
          <Field label="Expiry date"><input type="date" className={inputCls} value={f.expiry_date} onChange={(e) => setF({ ...f, expiry_date: e.target.value })} /></Field></div>
        {fieldKeys.map((k) => (
          <Field key={k} label={k.replace(/_/g, " ")} hint={HINT[k] || (k.includes("date") || k === "as_of" ? "YYYY-MM-DD" : k === "fy" ? "e.g. 2025-26" : k === "status" ? "completed / ongoing / substantially_completed" : k.includes("value") || k === "turnover" || k === "amount" || k === "net_worth" ? "Rupees, digits only" : undefined)}>
            {k === "is_similar" ? <select className={inputCls} value={f.fields[k] || ""} onChange={(e) => setF({ ...f, fields: { ...f.fields, [k]: e.target.value } })}>
              <option value="">Not assessed</option><option value="yes">Yes, matches the tender's similar-work definition</option><option value="no">No</option></select>
              : <input className={inputCls} value={f.fields[k] || ""} onChange={(e) => setF({ ...f, fields: { ...f.fields, [k]: e.target.value } })} />}
          </Field>
        ))}
        <div className="sm:col-span-3 flex flex-wrap items-center gap-4">
          <label className="text-sm">File <input type="file" accept="application/pdf,image/*,.docx,.xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
          <label className="text-sm sm:hidden">Use camera <input type="file" accept="image/*" capture="environment" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
          <Button disabled={act.busy}>{replace ? "Save replacement" : "Add document"}</Button>
        </div>
      </form>
    </Panel>
  );
}

function Entities() {
  const { can } = useMe();
  const ents = useLoad<Entity[]>(() => api("/entities"));
  const blank = { legal_name: "", entity_type: "company", registered_address: "", pan: "", gstin: "", signatory_name: "", signatory_designation: "" };
  const [f, setF] = useState(blank);
  const [warn, setWarn] = useState<string[]>([]);
  const act = useAction();
  return (
    <div className="space-y-5">
      {can("vault.write") && (
        <Panel title="Add legal entity">
          <ErrorBox error={act.error} />
          {warn.length > 0 && <Notice tone="warn">Similar name already exists: {warn.join(", ")}. Records are never shared between entities without approved attribution.</Notice>}
          <form className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); act.run(async () => { const r = await api("/entities", { json: { ...f, pan: f.pan || null, gstin: f.gstin || null } }); setWarn(r.similar_names_warning || []); }, () => { setF(blank); ents.reload(); }); }}>
            <Field label="Legal name (as registered)"><input required className={inputCls} value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} /></Field>
            <Field label="Constitution"><select className={inputCls} value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })}>
              {["company", "llp", "partnership", "proprietorship", "individual", "public_sector"].map((x) => <option key={x}>{x}</option>)}</select></Field>
            <Field label="Registered address"><input className={inputCls} value={f.registered_address} onChange={(e) => setF({ ...f, registered_address: e.target.value })} /></Field>
            <Field label="PAN"><input className={inputCls} value={f.pan} onChange={(e) => setF({ ...f, pan: e.target.value.toUpperCase() })} /></Field>
            <Field label="GSTIN"><input className={inputCls} value={f.gstin} onChange={(e) => setF({ ...f, gstin: e.target.value.toUpperCase() })} /></Field>
            <Field label="Authorised signatory"><input className={inputCls} value={f.signatory_name} onChange={(e) => setF({ ...f, signatory_name: e.target.value })} /></Field>
            <Field label="Signatory designation"><input className={inputCls} value={f.signatory_designation} onChange={(e) => setF({ ...f, signatory_designation: e.target.value })} /></Field>
            <div className="flex items-end"><Button disabled={act.busy}>Add entity</Button></div>
          </form>
        </Panel>
      )}
      <Panel title="Entities">
        {!ents.data?.length ? <Empty>No legal entities yet.</Empty> : (
          <table className="datagrid"><thead><tr><th>Legal name</th><th>Constitution</th><th>PAN</th><th>GSTIN</th><th>Signatory</th></tr></thead>
            <tbody>{ents.data.map((x) => <tr key={x.id}><td>{x.legal_name}<div className="text-xs text-muted">{x.registered_address}</div></td><td>{x.entity_type}</td><td>{x.pan || "—"}</td><td>{x.gstin || "—"}</td>
              <td>{x.signatory_name ? `${x.signatory_name}, ${x.signatory_designation || ""}` : <span className="text-gap">Not recorded</span>}</td></tr>)}</tbody></table>
        )}
        <p className="mt-2 text-xs text-muted">PAN and GSTIN are masked in general views.</p>
      </Panel>
    </div>
  );
}

function JVs() {
  const { can } = useMe();
  const ents = useLoad<Entity[]>(() => api("/entities"));
  const jvs = useLoad<{ id: number; name: string; share_total: string; members: { legal_entity_id: number; legal_name: string; role: string; share_pct: string }[] }[]>(() => api("/jvs"));
  const [name, setName] = useState("");
  const [members, setMembers] = useState([{ legal_entity_id: "", role: "lead", share_pct: "" }]);
  const act = useAction();
  const total = members.reduce((s, m) => s + (Number(m.share_pct) || 0), 0);
  return (
    <div className="space-y-5">
      {can("vault.write") && ents.data && (
        <Panel title="Create joint venture">
          <ErrorBox error={act.error} />
          <input aria-label="JV name" placeholder="JV name" className={`${inputCls} mb-3 max-w-md`} value={name} onChange={(e) => setName(e.target.value)} />
          {members.map((m, i) => (
            <div key={i} className="mb-2 flex flex-wrap gap-2">
              <select aria-label="Member" className={`${inputCls} max-w-xs`} value={m.legal_entity_id} onChange={(e) => setMembers(members.map((x, j) => j === i ? { ...x, legal_entity_id: e.target.value } : x))}>
                <option value="">Choose entity</option>{ents.data!.map((x) => <option key={x.id} value={x.id}>{x.legal_name}</option>)}</select>
              <select aria-label="Role" className={`${inputCls} w-32`} value={m.role} onChange={(e) => setMembers(members.map((x, j) => j === i ? { ...x, role: e.target.value } : x))}><option value="lead">Lead</option><option value="member">Member</option></select>
              <input aria-label="Share %" placeholder="Share %" className={`${inputCls} w-24`} value={m.share_pct} onChange={(e) => setMembers(members.map((x, j) => j === i ? { ...x, share_pct: e.target.value } : x))} />
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-3">
            <Button kind="secondary" onClick={() => setMembers([...members, { legal_entity_id: "", role: "member", share_pct: "" }])}>Add member</Button>
            <span className={`text-sm ${total === 100 ? "text-pass" : "text-gap"}`}>Shares total {total}%</span>
            <Button disabled={!name || total !== 100 || act.busy} onClick={() => act.run(() => api("/jvs", { json: { name, members: members.map((m) => ({ ...m, legal_entity_id: Number(m.legal_entity_id) })) } }), () => { setName(""); jvs.reload(); })}>Create JV</Button>
          </div>
          <p className="mt-2 text-xs text-muted">Whether member credentials can be combined is decided per criterion from the tender's JV clause, never assumed.</p>
        </Panel>
      )}
      <Panel title="Joint ventures">
        {!jvs.data?.length ? <Empty>No joint ventures yet.</Empty> : jvs.data.map((j) => (
          <div key={j.id} className="mb-3"><div className="font-medium">{j.name} <span className="text-sm text-muted">({j.share_total}% allocated)</span></div>
            <ul className="text-sm">{j.members.map((m) => <li key={m.legal_entity_id}>{m.legal_name}: {m.role}, {m.share_pct}%</li>)}</ul></div>))}
      </Panel>
    </div>
  );
}
