import { Fragment, useEffect, useState } from "react";
import { api } from "../../api";
import { useMe } from "../../App";
import { Button, Empty, ErrorBox, inputCls, Loading, Notice, Panel, Status, useAction, useLoad } from "../../ui";

const CLASSES = ["nit", "itb", "bds", "sbd", "gcc", "scc", "specification", "schedule", "drawing", "qualification", "boq", "forms",
  "addendum", "corrigendum", "prebid_reply", "other"];
const CLASS_LABEL: Record<string, string> = { nit: "NIT", itb: "ITB", bds: "BDS / TDS", sbd: "SBD / SPD", gcc: "GCC", scc: "SCC",
  prebid_reply: "Pre-bid reply", boq: "BOQ", unclassified: "Unclassified" };
const lbl = (c: string) => CLASS_LABEL[c] || c.charAt(0).toUpperCase() + c.slice(1);

type Doc = { id: number; filename: string; kind: string; doc_class: string; status: string; status_reason: string | null; page_count: number;
  size: number; sha256: string; is_amendment: boolean; parent_id: number | null; page_status: Record<string, number> };

export default function Documents({ tid, onChange }: { tid: number; onChange: () => void }) {
  const { can } = useMe();
  const docs = useLoad<Doc[]>(() => api(`/tenders/${tid}/documents`), [tid]);
  const jobs = useLoad<{ id: number; kind: string; status: string; filename: string; progress_done: number; progress_total: number; error: string | null; attempts: number }[]>(
    () => api(`/tenders/${tid}/jobs`), [tid]);
  const comp = useLoad<{ total_pages: number; pages: Record<string, number>; possibly_missing_volumes: string[]; note: string }>(() => api(`/tenders/${tid}/completeness`), [tid]);
  const sys = useLoad<{ ocr: { available: boolean; note: string | null }; ai: { provider: string; live: boolean; data_flow: string } }>(() => api("/system"));
  const [open, setOpen] = useState<number | null>(null);
  const [cls, setCls] = useState("unclassified");
  const act = useAction();
  const active = (jobs.data || []).some((j) => j.status === "queued" || j.status === "running");

  useEffect(() => {
    if (!active) return;
    const h = setInterval(() => { jobs.reload(); docs.reload(); comp.reload(); }, 2000);
    return () => { clearInterval(h); onChange(); };
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (files: FileList | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      const fd = new FormData(); fd.append("file", f); fd.append("doc_class", cls);
      const ok = await act.run(() => api(`/tenders/${tid}/documents`, { form: fd }));
      if (!ok) break;
    }
    docs.reload(); jobs.reload();
  };
  const refresh = () => { docs.reload(); jobs.reload(); comp.reload(); onChange(); };

  return (
    <div className="space-y-5">
      {sys.data && !sys.data.ocr.available && <Notice tone="warn">{sys.data.ocr.note}</Notice>}
      {sys.data && <Notice>Extraction: {sys.data.ai.live ? `AI provider ${sys.data.ai.provider} plus deterministic rules` : "deterministic rules on this server (no external AI)"}. {sys.data.ai.data_flow}</Notice>}
      {can("documents.write") && (
        <Panel title="Upload tender volumes">
          <ErrorBox error={act.error} />
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <label>Document type
              <select className={`${inputCls} mt-1 w-56`} value={cls} onChange={(e) => setCls(e.target.value)}>
                <option value="unclassified">Detect automatically</option>
                {CLASSES.map((c) => <option key={c} value={c}>{lbl(c)}</option>)}
              </select>
            </label>
            <input type="file" multiple aria-label="Choose files" accept=".pdf,.docx,.xlsx,.xlsm,.xls,.csv,.zip,.png,.jpg,.jpeg,.tif,.tiff"
              onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
            {act.busy && <span role="status" className="text-muted">Uploading…</span>}
          </div>
          <p className="mt-2 text-xs text-muted">Corrigenda and addenda change only the clauses they mention; earlier values are kept in history.</p>
        </Panel>
      )}
      {comp.data && comp.data.total_pages > 0 && (
        <Panel title="Page completeness">
          <div className="flex flex-wrap gap-4 text-sm">
            <span><strong>{comp.data.total_pages}</strong> pages tracked</span>
            {Object.entries(comp.data.pages).map(([s, n]) => <span key={s} className="flex items-center gap-1.5"><Status s={s} /> {n}</span>)}
          </div>
          {comp.data.possibly_missing_volumes.length > 0 && <p className="mt-2 text-sm text-gap">Not seen yet: {comp.data.possibly_missing_volumes.map(lbl).join(", ")}. {comp.data.note}</p>}
        </Panel>
      )}
      <Panel title="Document register" actions={<Button kind="secondary" onClick={refresh}>Refresh</Button>}>
        <ErrorBox error={docs.error} onRetry={docs.reload} />
        {docs.loading && !docs.data ? <Loading /> : !docs.data?.length ? <Empty>No documents uploaded yet.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>File</th><th>Type</th><th>Status</th><th>Pages</th><th>Integrity</th><th></th></tr></thead>
            <tbody>{docs.data.map((d) => (
              <Fragment key={d.id}>
                <tr>
                  <td className="max-w-xs break-words">{d.parent_id && <span className="text-muted">↳ </span>}{d.filename}{d.is_amendment && <span className="ml-2 text-xs text-gap">amendment</span>}</td>
                  <td>{can("documents.write") && d.status !== "container" ? (
                    <select aria-label={`Type of ${d.filename}`} className="rounded border border-line bg-white px-1 py-0.5 text-sm" value={d.doc_class}
                      onChange={(e) => act.run(() => api(`/tenders/${tid}/documents/${d.id}/classify`, { json: { doc_class: e.target.value } }), refresh)}>
                      {d.doc_class === "unclassified" && <option value="unclassified">Choose type</option>}
                      {CLASSES.map((c) => <option key={c} value={c}>{lbl(c)}</option>)}
                    </select>) : lbl(d.doc_class)}</td>
                  <td><Status s={d.status} />{d.status_reason && <div className="mt-1 max-w-xs text-xs text-muted">{d.status_reason}</div>}
                    {d.status === "needs_password" && can("documents.write") && <PasswordForm tid={tid} doc={d.id} done={refresh} />}</td>
                  <td className="text-sm">{d.page_count || "—"}<div className="text-xs text-muted">{Object.entries(d.page_status).map(([s, n]) => `${n} ${s}`).join(", ")}</div></td>
                  <td className="text-xs text-muted">{(d.size / 1024).toFixed(0)} KB<br />SHA-256 {d.sha256.slice(0, 12)}…</td>
                  <td>{d.page_count > 0 && <Button kind="ghost" onClick={() => setOpen(open === d.id ? null : d.id)} aria-expanded={open === d.id}>{open === d.id ? "Hide pages" : "Pages"}</Button>}</td>
                </tr>
                {open === d.id && <tr><td colSpan={6} className="bg-[#fafaf7]"><Pages tid={tid} doc={d.id} /></td></tr>}
              </Fragment>
            ))}</tbody>
          </table></div>
        )}
      </Panel>
      <Panel title="Processing queue">
        {!jobs.data?.length ? <Empty>No processing jobs.</Empty> : (
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th>Job</th><th>File</th><th>Status</th><th>Progress</th><th>Detail</th><th></th></tr></thead>
            <tbody>{jobs.data.map((j) => (
              <tr key={j.id}>
                <td>{j.kind === "ingest_document" ? "Read pages" : "Extract requirements"}</td><td className="max-w-[16rem] truncate">{j.filename}</td>
                <td><Status s={j.status} /></td><td>{j.progress_total ? `${j.progress_done} / ${j.progress_total}` : "—"}</td>
                <td className="max-w-xs text-xs text-muted">{j.error}{j.attempts > 1 && ` (attempt ${j.attempts})`}</td>
                <td>{can("documents.write") && (j.status === "failed" || j.status === "cancelled" ? <Button kind="ghost" onClick={() => act.run(() => api(`/tenders/${tid}/jobs/${j.id}/retry`, { json: {} }), refresh)}>Retry</Button>
                  : (j.status === "queued" || j.status === "running") ? <Button kind="ghost" onClick={() => act.run(() => api(`/tenders/${tid}/jobs/${j.id}/cancel`, { json: {} }), refresh)}>Cancel</Button> : null)}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
    </div>
  );
}

function PasswordForm({ tid, doc, done }: { tid: number; doc: number; done: () => void }) {
  const [pw, setPw] = useState("");
  const act = useAction();
  return (
    <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); act.run(() => api(`/tenders/${tid}/documents/${doc}/password`, { json: { password: pw } }), () => { setPw(""); done(); }); }}>
      <input type="password" aria-label="PDF password" autoComplete="off" className="rounded border border-line px-2 py-1 text-sm" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Authorised password" />
      <Button disabled={!pw || act.busy}>Unlock</Button>
      {act.error && <span className="text-xs text-fail">{act.error}</span>}
    </form>
  );
}

function Pages({ tid, doc }: { tid: number; doc: number }) {
  const p = useLoad<{ page_index: number; page_label: string | null; status: string; reason: string | null; text_source: string | null; confidence: number | null; chars: number }[]>(
    () => api(`/tenders/${tid}/documents/${doc}/pages`), [doc]);
  if (!p.data) return <Loading />;
  return (
    <div className="max-h-80 overflow-auto">
      <table className="datagrid"><thead><tr><th>Page</th><th>Status</th><th>Read by</th><th>Confidence</th><th>Characters</th><th>Reason</th></tr></thead>
        <tbody>{p.data.map((x) => (
          <tr key={x.page_index}><td>{x.page_index + 1}{x.page_label && ` (${x.page_label})`}</td><td><Status s={x.status} /></td>
            <td>{x.text_source?.replace("_", " ") || "—"}</td><td>{x.confidence != null ? `${Math.round(x.confidence * 100)}%` : "—"}</td><td>{x.chars}</td>
            <td className="text-xs text-muted">{x.reason}</td></tr>))}</tbody></table>
    </div>
  );
}
