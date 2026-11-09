import { useState } from "react";
import { api, download } from "../../api";
import { useMe } from "../../App";
import { Button, Confirm, Empty, ErrorBox, fmtDate, inputCls, Loading, Notice, Panel, Status, useAction, useLoad } from "../../ui";

type Slot = { id: number; envelope: string; name: string; allowed_types: string[]; max_size_mb: string | null; max_count: number; naming: string | null;
  signature_required: boolean; mandatory: boolean; assignments: { type: string; id: number }[] };
type Check = { id: string; title: string; status: string; details: string[] };
type Pkg = { id: number; revision: number; status: string; package_hash: string | null; current: boolean; created_at: string; preflight: { checks: Check[] };
  entries: { filename: string; slot: string; envelope: string; size: number; pages: number | null; sha256: string; signature_status: string }[];
  receipts: { receipt_identifier: string; submitted_at: string }[] };

export default function Package({ tid, onChange }: { tid: number; onChange: () => void }) {
  const { can } = useMe();
  const env = useLoad<{ profile: { envelopes: string[]; zip_accepted: boolean | null; source_note: string | null; portal_name: string | null } | null; slots: Slot[] }>(() => api(`/tenders/${tid}/envelope`), [tid]);
  const pkgs = useLoad<Pkg[]>(() => api(`/tenders/${tid}/packages`), [tid]);
  const gens = useLoad<{ id: number; name: string; revision: number; envelope: string }[]>(() => api(`/tenders/${tid}/generated`), [tid]);
  const evid = useLoad<{ id: number; title: string; category: string; entity_name: string }[]>(() => api("/evidence"));
  const quotes = useLoad<{ id: number; version_no: number; status: string; export_key: string | null }[]>(() => can("price.read") ? api(`/tenders/${tid}/quotes`) : Promise.resolve([]), [tid]);
  const [pf, setPf] = useState<{ checks: Check[]; final_ready: boolean } | null>(null);
  const act = useAction();
  const reload = () => { env.reload(); pkgs.reload(); onChange(); };
  if (!env.data) return <Loading />;
  const p = env.data.profile;
  const label = (a: { type: string; id: number }) => a.type === "generated" ? (gens.data?.find((g) => g.id === a.id)?.name || `Document #${a.id}`) + " (generated)"
    : a.type === "evidence" ? (evid.data?.find((e) => e.id === a.id)?.title || `Vault #${a.id}`) : `Priced BOQ, quote v${quotes.data?.find((q) => q.id === a.id)?.version_no ?? a.id}`;

  return (
    <div className="space-y-5">
      <ErrorBox error={act.error} />
      <Notice>TenderPilot prepares upload-ready files and a portal guide. It never signs with a DSC, pays fees, solves OTP or CAPTCHA, or clicks final submission. Do those on the portal, then record the acknowledgement here.</Notice>
      <Profile tid={tid} p={p} reload={reload} canEdit={can("package.build")} />
      <Panel title="Upload slots" actions={can("package.build") && <NewSlot tid={tid} envelopes={p?.envelopes || ["technical", "financial"]} reload={reload} />}>
        {!env.data.slots.length ? <Empty>Add one slot per portal upload field, using the names and limits shown on the portal.</Empty> : (
          <div className="space-y-3">{env.data.slots.map((s) => (
            <div key={s.id} className="rounded-md border border-line p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><span className="font-medium">{s.name}</span> <span className="text-sm text-muted">{s.envelope} envelope, {s.allowed_types.join("/")}, up to {s.max_count} file(s){s.max_size_mb && `, ${s.max_size_mb} MB each`}{s.signature_required && ", signed"}{s.mandatory ? "" : ", optional"}</span></div>
                {can("package.build") && <Confirm label="Remove slot" text="Remove this slot?" onConfirm={() => act.run(() => api(`/tenders/${tid}/slots/${s.id}`, { method: "DELETE" }), reload)} />}
              </div>
              <ul className="mt-2 space-y-1 text-sm">{s.assignments.map((a, i) => (
                <li key={i} className="flex items-center gap-2">{label(a)}{can("package.build") && <button className="text-xs text-muted underline" onClick={() => act.run(() => api(`/tenders/${tid}/slots/${s.id}`, { method: "PUT", json: { ...s, assignments: s.assignments.filter((_, j) => j !== i) } }), reload)}>remove</button>}</li>))}</ul>
              {can("package.build") && (
                <select aria-label={`Add file to ${s.name}`} className={`${inputCls} mt-2 max-w-md`} value="" onChange={(e) => {
                  const [type, id] = e.target.value.split(":"); if (!type) return;
                  act.run(() => api(`/tenders/${tid}/slots/${s.id}`, { method: "PUT", json: { ...s, assignments: [...s.assignments, { type, id: Number(id) }] } }), reload);
                }}>
                  <option value="">Add a file to this slot…</option>
                  <optgroup label="Generated documents">{gens.data?.filter((g) => s.envelope === "financial" || g.envelope !== "financial").map((g) => <option key={g.id} value={`generated:${g.id}`}>{g.name} r{g.revision}</option>)}</optgroup>
                  <optgroup label="Company vault">{evid.data?.map((x) => <option key={x.id} value={`evidence:${x.id}`}>{x.title} ({x.entity_name})</option>)}</optgroup>
                  {s.envelope === "financial" && <optgroup label="Priced BOQ">{quotes.data?.filter((q) => q.export_key).map((q) => <option key={q.id} value={`quote_export:${q.id}`}>Quote v{q.version_no} workbook</option>)}</optgroup>}
                </select>
              )}
            </div>))}</div>
        )}
      </Panel>
      <Panel title="Preflight" actions={<Button onClick={() => act.run(async () => setPf(await api(`/tenders/${tid}/preflight`, { json: {} })))} disabled={act.busy}>Run preflight</Button>}>
        {!pf ? <Empty>Run preflight to check eligibility, checklist, slots, signatures, price leakage, quote approval and deadlines.</Empty> : (
          <>
            <p className={`mb-3 font-medium ${pf.final_ready ? "text-pass" : "text-fail"}`}>{pf.final_ready ? "All mandatory checks pass. A final package can be built." : "Final export is blocked until the failing checks are fixed. A draft can still be exported."}</p>
            <ul className="divide-y divide-[#ebe9e3] text-sm">{pf.checks.map((c) => (
              <li key={c.id} className="py-2"><div className="flex items-center gap-2"><Status s={c.status} /><span className="font-medium">{c.title}</span></div>
                {c.status !== "pass" && c.details.length > 0 && <ul className="ml-6 mt-1 list-disc text-muted">{c.details.slice(0, 12).map((d, i) => <li key={i}>{d}</li>)}</ul>}</li>))}</ul>
          </>
        )}
        {can("package.build") && <div className="mt-4 flex flex-wrap gap-2">
          <Button kind="secondary" onClick={() => act.run(() => api(`/tenders/${tid}/packages`, { json: { final: false } }), reload)}>Export draft package</Button>
          <Button disabled={!pf?.final_ready} onClick={() => act.run(() => api(`/tenders/${tid}/packages`, { json: { final: true } }), reload)}>Build final package</Button></div>}
      </Panel>
      <Panel title="Export history">
        {!pkgs.data?.length ? <Empty>No packages exported yet.</Empty> : pkgs.data.map((k) => <PackageCard key={k.id} tid={tid} k={k} reload={reload} />)}
      </Panel>
    </div>
  );
}

function Profile({ tid, p, reload, canEdit }: { tid: number; p: { envelopes: string[]; zip_accepted: boolean | null; source_note: string | null; portal_name: string | null } | null; reload: () => void; canEdit: boolean }) {
  const [f, setF] = useState({ envelopes: (p?.envelopes || ["fee_emd", "technical", "financial"]).join(", "), zip: p?.zip_accepted === null || p?.zip_accepted === undefined ? "unknown" : p.zip_accepted ? "yes" : "no",
    source_note: p?.source_note || "", portal_name: p?.portal_name || "" });
  const act = useAction();
  return (
    <Panel title="Envelope profile">
      <ErrorBox error={act.error} />
      <div className="grid gap-3 text-sm sm:grid-cols-4">
        <label>Envelopes, in order<input className={inputCls} disabled={!canEdit} value={f.envelopes} onChange={(e) => setF({ ...f, envelopes: e.target.value })} /></label>
        <label>Portal accepts ZIP?<select className={inputCls} disabled={!canEdit} value={f.zip} onChange={(e) => setF({ ...f, zip: e.target.value })}>
          <option value="unknown">Not confirmed</option><option value="yes">Yes, per portal instructions</option><option value="no">No</option></select></label>
        <label>Portal<input className={inputCls} disabled={!canEdit} value={f.portal_name} onChange={(e) => setF({ ...f, portal_name: e.target.value })} placeholder="e.g. CPPP, state e-procurement" /></label>
        <label>Where this is stated<input className={inputCls} disabled={!canEdit} value={f.source_note} onChange={(e) => setF({ ...f, source_note: e.target.value })} placeholder="NIT clause / portal help page" /></label>
      </div>
      {canEdit && <Button className="mt-3" kind="secondary" onClick={() => act.run(() => api(`/tenders/${tid}/envelope`, { method: "PUT", json: {
        envelopes: f.envelopes.split(",").map((x) => x.trim()).filter(Boolean), zip_accepted: f.zip === "unknown" ? null : f.zip === "yes", source_note: f.source_note, portal_name: f.portal_name } }), reload)}>Save profile</Button>}
    </Panel>
  );
}

function NewSlot({ tid, envelopes, reload }: { tid: number; envelopes: string[]; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ envelope: envelopes[0] || "technical", name: "", allowed_types: "pdf", max_size_mb: "", max_count: "1", naming: "", signature_required: false, mandatory: true });
  const act = useAction();
  if (!open) return <Button kind="secondary" onClick={() => setOpen(true)}>Add slot</Button>;
  return (
    <form className="flex w-full flex-wrap items-end gap-2 text-xs" onSubmit={(e) => { e.preventDefault(); act.run(() => api(`/tenders/${tid}/slots`, { json: { ...f,
      allowed_types: f.allowed_types.split(",").map((x) => x.trim().toLowerCase()), max_size_mb: f.max_size_mb || null, max_count: Number(f.max_count), naming: f.naming || null, assignments: [] } }), () => { setOpen(false); reload(); }); }}>
      <label>Envelope<select className={inputCls} value={f.envelope} onChange={(e) => setF({ ...f, envelope: e.target.value })}>{envelopes.map((x) => <option key={x}>{x}</option>)}</select></label>
      <label>Slot name on portal<input required className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>File types<input className={`${inputCls} w-24`} value={f.allowed_types} onChange={(e) => setF({ ...f, allowed_types: e.target.value })} /></label>
      <label>Max MB<input className={`${inputCls} w-20`} value={f.max_size_mb} onChange={(e) => setF({ ...f, max_size_mb: e.target.value })} /></label>
      <label>Max files<input className={`${inputCls} w-16`} value={f.max_count} onChange={(e) => setF({ ...f, max_count: e.target.value })} /></label>
      <label className="flex items-center gap-1"><input type="checkbox" checked={f.signature_required} onChange={(e) => setF({ ...f, signature_required: e.target.checked })} />Signed</label>
      <label className="flex items-center gap-1"><input type="checkbox" checked={f.mandatory} onChange={(e) => setF({ ...f, mandatory: e.target.checked })} />Mandatory</label>
      <Button disabled={act.busy}>Add</Button><Button kind="ghost" type="button" onClick={() => setOpen(false)}>Cancel</Button>
      {act.error && <span className="text-fail">{act.error}</span>}
    </form>
  );
}

function PackageCard({ tid, k, reload }: { tid: number; k: Pkg; reload: () => void }) {
  const { can } = useMe();
  const act = useAction();
  const [rc, setRc] = useState({ id: "", at: "", file: null as File | null });
  return (
    <div className="mb-3 rounded-md border border-line p-3">
      <ErrorBox error={act.error} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><span className="font-semibold">Revision {k.revision}</span> <Status s={k.status} />{!k.current && k.status !== "superseded" && <span className="ml-2 text-xs text-fail">Inputs changed since this was built</span>}
          <div className="text-xs text-muted">{fmtDate(k.created_at)}, {k.entries.length} files{k.package_hash && `, hash ${k.package_hash.slice(0, 16)}…`}</div></div>
        <div className="flex flex-wrap gap-2">
          {can("package.download") && <>
            <Button kind="secondary" onClick={() => act.run(() => download(`/tenders/${tid}/packages/${k.id}/zip`, `tender${tid}_rev${k.revision}${k.status === "draft" ? "_DRAFT" : ""}.zip`))}>Local copy (ZIP)</Button>
            <Button kind="secondary" onClick={() => act.run(() => download(`/tenders/${tid}/packages/${k.id}/guide`, `portal_guide_rev${k.revision}.txt`))}>Portal guide</Button>
            <Button kind="ghost" onClick={() => act.run(() => download(`/tenders/${tid}/packages/${k.id}/manifest`, `INTERNAL_manifest_rev${k.revision}.json`))}>Internal manifest</Button></>}
          {k.status === "final_ready" && can("package.approve") && <Button onClick={() => act.run(() => api(`/tenders/${tid}/packages/${k.id}/freeze`, { json: {} }), reload)}>Approve and freeze</Button>}
        </div>
      </div>
      <details className="mt-2 text-sm"><summary className="cursor-pointer text-muted">Files by upload slot</summary>
        <table className="datagrid mt-2"><thead><tr><th>Envelope</th><th>Slot</th><th>File</th><th>Size</th><th>Pages</th><th>Signature</th><th>SHA-256</th></tr></thead>
          <tbody>{k.entries.map((e) => <tr key={e.sha256 + e.filename}><td>{e.envelope}</td><td>{e.slot}</td><td>{e.filename}</td><td>{(e.size / 1024).toFixed(0)} KB</td><td>{e.pages ?? "—"}</td><td>{e.signature_status.replace(/_/g, " ")}</td><td className="text-xs">{e.sha256.slice(0, 12)}…</td></tr>)}</tbody></table></details>
      {k.receipts.map((r) => <p key={r.receipt_identifier} className="mt-2 text-sm text-pass">Portal acknowledgement {r.receipt_identifier}, submitted {fmtDate(r.submitted_at)}</p>)}
      {k.status === "frozen" && can("receipt.record") && !k.receipts.length && (
        <form className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3 text-xs" onSubmit={(e) => { e.preventDefault(); if (!rc.file) return;
          const fd = new FormData(); fd.append("receipt_identifier", rc.id); fd.append("submitted_at", rc.at); fd.append("file", rc.file);
          act.run(() => api(`/tenders/${tid}/packages/${k.id}/receipt`, { form: fd }), reload); }}>
          <span className="w-full text-sm">After submitting on the portal, record the acknowledgement:</span>
          <label>Acknowledgement / receipt number<input required className={inputCls} value={rc.id} onChange={(e) => setRc({ ...rc, id: e.target.value })} /></label>
          <label>Submitted at (IST)<input required type="datetime-local" className={inputCls} value={rc.at} onChange={(e) => setRc({ ...rc, at: e.target.value })} /></label>
          <label>Receipt file<input required type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setRc({ ...rc, file: e.target.files?.[0] || null })} /></label>
          <Button disabled={act.busy}>Record receipt</Button>
        </form>
      )}
    </div>
  );
}
