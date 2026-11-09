import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Button, ErrorBox, Field, inputCls, Panel, useLoad } from "../ui";

export default function NewTender() {
  const nav = useNavigate();
  const ents = useLoad<{ id: number; legal_name: string }[]>(() => api("/entities"));
  const jvs = useLoad<{ id: number; name: string }[]>(() => api("/jvs"));
  const [f, setF] = useState({ title: "", tender_ref: "", department: "", portal_url: "", bidder_type: "entity", bidder: "", known_deadline: "" });
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    try {
      setProgress("Creating tender…");
      const t = await api("/tenders", { json: {
        title: f.title, tender_ref: f.tender_ref, department: f.department, portal_url: f.portal_url, bidder_type: f.bidder_type,
        bidder_entity_id: f.bidder_type === "entity" && f.bidder ? Number(f.bidder) : null,
        jv_id: f.bidder_type === "jv" && f.bidder ? Number(f.bidder) : null, known_deadline: f.known_deadline || null } });
      for (let i = 0; i < files.length; i++) {
        setProgress(`Uploading ${i + 1} of ${files.length}: ${files[i].name}`);
        const fd = new FormData(); fd.append("file", files[i]);
        await api(`/tenders/${t.id}/documents`, { form: fd });
      }
      nav(`/tenders/${t.id}/documents`);
    } catch (x) { setErr((x as Error).message); setProgress(null); }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">New tender</h1>
      <p className="mb-5 text-sm text-muted">Enter only what you already know. Dates, values and requirements are read from the documents for you to review.</p>
      <form onSubmit={submit}>
        <Panel>
          <ErrorBox error={err} />
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2"><Field label="Name of work"><input required minLength={3} className={inputCls} value={f.title} onChange={set("title")} /></Field></div>
            <Field label="Tender ID / NIT number"><input className={inputCls} value={f.tender_ref} onChange={set("tender_ref")} /></Field>
            <Field label="Department / employer"><input className={inputCls} value={f.department} onChange={set("department")} /></Field>
            <Field label="Portal URL" hint="Stored for reference only; TenderPilot never logs into the portal.">
              <input type="url" pattern="https?://.*" className={inputCls} value={f.portal_url} onChange={set("portal_url")} placeholder="https://" /></Field>
            <Field label="Known deadline (as printed)" hint="Optional. Compared with the NIT once extracted.">
              <input className={inputCls} value={f.known_deadline} onChange={set("known_deadline")} placeholder="25/11/2026 15:00" /></Field>
            <Field label="Bidding as">
              <select className={inputCls} value={f.bidder_type} onChange={(e) => setF({ ...f, bidder_type: e.target.value, bidder: "" })}>
                <option value="entity">Single legal entity</option><option value="jv">Joint venture / consortium</option>
              </select></Field>
            <Field label={f.bidder_type === "jv" ? "Joint venture" : "Legal entity"} hint="You can decide later; create records in the company vault.">
              <select className={inputCls} value={f.bidder} onChange={set("bidder")}>
                <option value="">Decide later</option>
                {(f.bidder_type === "jv" ? jvs.data?.map((j) => [j.id, j.name]) : ents.data?.map((x) => [x.id, x.legal_name]))?.map(([id, n]) =>
                  <option key={id} value={id}>{n}</option>)}
              </select></Field>
            <div className="sm:col-span-2">
              <Field label="Tender documents" hint="All volumes: NIT, ITB, BDS, GCC/SCC, specifications, BOQ, forms, corrigenda. PDF, DOCX, XLSX, CSV, images or ZIP.">
                <input type="file" multiple accept=".pdf,.docx,.xlsx,.xlsm,.xls,.csv,.zip,.png,.jpg,.jpeg,.tif,.tiff" className="text-sm"
                  onChange={(e) => setFiles(Array.from(e.target.files || []))} />
              </Field>
              {files.length > 0 && <ul className="mt-2 text-xs text-muted">{files.map((x) => <li key={x.name}>{x.name} ({(x.size / 1048576).toFixed(1)} MB)</li>)}</ul>}
            </div>
          </div>
          <div className="mt-5 flex items-center gap-3">
            <Button disabled={!!progress}>Create tender and upload</Button>
            {progress && <span className="text-sm text-muted" role="status">{progress}</span>}
          </div>
        </Panel>
      </form>
    </div>
  );
}
