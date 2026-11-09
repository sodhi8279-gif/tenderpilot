import { useEffect, useMemo, useState } from "react";
import { api, blobUrl } from "../../api";
import { useMe } from "../../App";
import { Button, cx, Empty, ErrorBox, inputCls, Loading, Status, useAction, useLoad } from "../../ui";

type Anchor = { document_id: number; filename: string; page_index: number; page_label: string | null; clause_no: string | null; excerpt: string;
  bbox: number[] | null; doc_sha256: string; doc_version: number };
type Version = { version_no: number; value: Record<string, unknown>; value_text: string; confidence: number; review_status: string; is_current: boolean;
  nature: string; extractor: string; review_note: string | null; amendment_document_id: number | null; tender_version: number; anchor: Anchor | null };
export type Req = { id: number; key: string; category: string; title: string; mandatory: boolean; has_conflict: boolean; low_confidence: boolean;
  current: Version | null; versions: Version[] };

const NATURE: Record<string, string> = { extracted: "Extracted fact", derived: "Derived calculation", interpretation: "Interpretation", question: "Unresolved question" };
const CATS = ["deadline", "commercial", "financial", "experience", "qualification", "jv", "document", "envelope", "precedence", "technical"];

function valueText(v: Record<string, unknown>) {
  return Object.entries(v).filter(([, x]) => x !== null && x !== "").map(([k, x]) => `${k.replace(/_/g, " ")}: ${String(x)}`).join("; ");
}

export default function Clauses({ tid, onChange }: { tid: number; onChange: () => void }) {
  const reqs = useLoad<Req[]>(() => api(`/tenders/${tid}/requirements`), [tid]);
  const [sel, setSel] = useState<number | null>(null);
  const [filter, setFilter] = useState("all");
  const list = useMemo(() => (reqs.data || []).filter((r) =>
    filter === "all" || (filter === "attention" && (r.has_conflict || r.low_confidence || r.current?.review_status !== "approved")) ||
    (filter === "conflict" && r.has_conflict) || r.category === filter), [reqs.data, filter]);
  const current = (reqs.data || []).find((r) => r.id === sel) || list[0];

  if (reqs.loading && !reqs.data) return <Loading />;
  if (!reqs.data?.length) return <Empty>No requirements yet. They appear after the documents are processed.</Empty>;
  const attention = reqs.data.filter((r) => r.has_conflict || r.low_confidence || r.current?.review_status !== "approved").length;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_1fr]">
      <div className="rounded-lg border border-line bg-panel">
        <div className="border-b border-line p-3">
          <select aria-label="Filter requirements" className={inputCls} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All requirements ({reqs.data.length})</option>
            <option value="attention">Needs review ({attention})</option>
            <option value="conflict">Conflicts and amendments</option>
            {CATS.map((c) => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
          </select>
        </div>
        <ul className="max-h-[70vh] overflow-auto" role="listbox" aria-label="Requirements">
          {list.map((r) => (
            <li key={r.id}>
              <button role="option" aria-selected={current?.id === r.id} onClick={() => setSel(r.id)}
                className={cx("block w-full border-b border-[#ebe9e3] px-3 py-2.5 text-left", current?.id === r.id ? "bg-teal-soft" : "hover:bg-[#fafaf7]")}>
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium">{r.title}</span>
                  <Status s={r.has_conflict ? "needs_review" : r.current?.review_status} />
                </div>
                <div className="mt-0.5 text-xs text-muted">
                  {r.current?.anchor ? `Clause ${r.current.anchor.clause_no || "?"}, page ${r.current.anchor.page_index + 1}` : NATURE[r.current?.nature || "question"]}
                  {r.low_confidence && <span className="text-gap">, low confidence</span>}
                  {r.versions.length > 1 && <span>, {r.versions.length} versions</span>}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
      {current && <Detail key={current.id} tid={tid} r={current} reload={() => { reqs.reload(); onChange(); }} />}
    </div>
  );
}

function Detail({ tid, r, reload }: { tid: number; r: Req; reload: () => void }) {
  const { can } = useMe();
  const [vno, setVno] = useState(r.current?.version_no ?? r.versions[r.versions.length - 1].version_no);
  const v = r.versions.find((x) => x.version_no === vno)!;
  const [img, setImg] = useState<string | null>(null);
  const [imgErr, setImgErr] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [note, setNote] = useState("");
  const act = useAction();
  const a = v.anchor;

  useEffect(() => {
    setImg(null); setImgErr(null);
    if (!a) return;
    const q = a.bbox ? `?x0=${a.bbox[0]}&y0=${a.bbox[1]}&x1=${a.bbox[2]}&y1=${a.bbox[3]}` : "";
    let url: string | null = null;
    blobUrl(`/tenders/${tid}/documents/${a.document_id}/pages/${a.page_index}/image${q}`).then((u) => { url = u; setImg(u); }).catch((e) => setImgErr(e.message));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [tid, a?.document_id, a?.page_index, a?.bbox?.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const review = (decision: string, corrected?: Record<string, unknown>) =>
    act.run(() => api(`/tenders/${tid}/requirements/${r.id}/review`, { json: { version_no: vno, decision, corrected_value: corrected ?? null, note: note || null } }),
      () => { setEdit(null); reload(); });

  return (
    <div className="grid gap-4 2xl:grid-cols-[1fr_minmax(300px,380px)]">
      <div className="rounded-lg border border-line bg-[#e9e8e3] p-2">
        {!a ? <Empty>Added manually; no source page.</Empty> : imgErr ? <Empty>Page preview unavailable ({imgErr}). Excerpt shown on the right.</Empty>
          : img ? <img src={img} alt={`Page ${a.page_index + 1} of ${a.filename} with the clause highlighted`} className="h-auto w-full bg-white shadow-sm" /> : <Loading />}
      </div>
      <div className="space-y-3 text-sm">
        <ErrorBox error={act.error} />
        <div className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-base font-semibold">{r.title}</h3>
          <div className="mt-1 text-xs text-muted">{NATURE[v.nature]} by {v.extractor}, {Math.round(v.confidence * 100)}% confidence, tender version {v.tender_version}</div>
          {r.has_conflict && <p className="mt-2 rounded bg-[#fbeedb] px-2 py-1 text-[#6b3e00]">This requirement changed or conflicts. It blocks final readiness until a reviewer confirms which version applies.</p>}
          {a && (
            <>
              <blockquote className="mt-3 border-l-2 border-teal pl-3 text-[0.9rem] leading-relaxed">{a.excerpt}</blockquote>
              <div className="mt-2 text-xs text-muted">{a.filename}, page {a.page_index + 1}{a.page_label && ` (printed ${a.page_label})`}, clause {a.clause_no || "not numbered"}. Document v{a.doc_version}, SHA-256 {a.doc_sha256.slice(0, 12)}…</div>
            </>
          )}
          <div className="mt-3"><span className="font-medium">Value: </span>{valueText(v.value) || "—"}</div>
          {v.review_note && <div className="mt-2 text-xs text-gap">{v.review_note}</div>}
        </div>

        {r.versions.length > 1 && (
          <div className="rounded-lg border border-line bg-panel p-4">
            <div className="mb-2 font-medium">Version history</div>
            <ol className="space-y-1.5">
              {r.versions.map((x) => (
                <li key={x.version_no}>
                  <button onClick={() => setVno(x.version_no)} className={cx("w-full rounded px-2 py-1 text-left", x.version_no === vno ? "bg-teal-soft" : "hover:bg-[#fafaf7]")}>
                    v{x.version_no}{x.is_current && " (current)"}: {valueText(x.value).slice(0, 80)} <Status s={x.review_status} />
                    {x.amendment_document_id && <span className="ml-1 text-xs text-gap">from amendment</span>}
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}

        {can("requirements.review") && (
          <div className="rounded-lg border border-line bg-panel p-4">
            <div className="mb-2 font-medium">Review this version</div>
            <input aria-label="Review note" className={inputCls} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            {edit ? (
              <div className="mt-3 space-y-2">
                {Object.keys(edit).map((k) => (
                  <label key={k} className="block text-xs">{k.replace(/_/g, " ")}
                    <input className={inputCls} value={edit[k]} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></label>
                ))}
                <div className="flex gap-2">
                  <Button disabled={act.busy} onClick={() => review("approve", Object.fromEntries(Object.entries(edit).map(([k, x]) => [k, x === "true" ? true : x === "false" ? false : x])))}>Save corrected value</Button>
                  <Button kind="secondary" onClick={() => setEdit(null)}>Cancel</Button>
                </div>
                <p className="text-xs text-muted">A correction is saved as a new version; the extracted value stays in history.</p>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button disabled={act.busy} onClick={() => review("approve")}>{r.has_conflict && !v.is_current ? "Use this version" : "Confirm as read"}</Button>
                <Button kind="secondary" onClick={() => setEdit(Object.fromEntries(Object.entries(v.value).map(([k, x]) => [k, x === null ? "" : String(x)])))}>Correct value</Button>
                <Button kind="danger" disabled={act.busy} onClick={() => review("reject")}>Reject</Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
