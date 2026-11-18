import { useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { Button, ErrorBox, Field, inputCls, Notice, Panel, useAction } from "../ui";
import { FIELDS } from "./Vault";

/** What the server read from one document (POST /evidence/read). Nothing is saved until the person saves it. */
export type FirmHint = { value: string; label: string; target: "core" | "profile"; excerpt: string; note?: string;
  current?: string | null; state?: "new" | "same" | "different" };
export type ReadResult = {
  filename: string; kind: string; pages_total: number; pages_read: number; read_by: string; notes: string[]; readable: boolean;
  category: string; category_sure: boolean; title: string; issuer: string | null; issue_date: string | null; expiry_date: string | null;
  subject_name_on_document: string | null; fields: Record<string, string>; excerpts: Record<string, string>;
  rows: { fy: string; turnover: string; excerpt: string }[]; firm: Record<string, FirmHint>;
};

export async function readDocument(file: File, entityId?: number): Promise<ReadResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (entityId) fd.append("legal_entity_id", String(entityId));
  return api<ReadResult>("/evidence/read", { form: fd });
}

type Draft = { category: string; title: string; issuer: string; subject_name_on_document: string; issue_date: string; expiry_date: string;
  fields: Record<string, string>; rows: { fy: string; turnover: string; on: boolean }[] };

export function draftFrom(r: ReadResult): Draft {
  return { category: r.category, title: r.title, issuer: r.issuer || "", subject_name_on_document: r.subject_name_on_document || "",
    issue_date: r.issue_date || "", expiry_date: r.expiry_date || "", fields: { ...r.fields }, rows: r.rows.map((x) => ({ fy: x.fy, turnover: x.turnover, on: true })) };
}

/** Save one read document as the firm's record(s). A multi-year turnover certificate becomes one record per year. */
export async function saveDraft(entityId: number, file: File, d: Draft, firmName?: string) {
  const one = async (title: string, fields: Record<string, string>) => {
    const fd = new FormData();
    fd.append("legal_entity_id", String(entityId));
    fd.append("category", d.category);
    fd.append("title", title);
    if (d.issuer) fd.append("issuer", d.issuer);
    const nm = d.subject_name_on_document.trim();
    if (nm && (!firmName || nm.toLowerCase() !== firmName.toLowerCase())) fd.append("subject_name_on_document", nm);
    if (d.issue_date) fd.append("issue_date", d.issue_date);
    if (d.expiry_date) fd.append("expiry_date", d.expiry_date);
    const clean = Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, k === "is_similar" ? v === "yes" : v]));
    fd.append("fields", JSON.stringify(clean));
    fd.append("confidence", "0.75"); // read automatically: the record shows "verify against the document" until checked
    fd.append("file", file);
    await api("/evidence", { form: fd });
  };
  const rows = d.rows.filter((r) => r.on);
  if (["turnover_certificate", "audited_accounts"].includes(d.category) && rows.length > 1) {
    for (const r of rows) await one(`${d.title} ${r.fy}`, { ...d.fields, fy: r.fy, turnover: r.turnover });
  } else {
    await one(d.title, d.fields);
  }
}

/** Firm details found in documents: tick what to add. Existing values are only replaced when ticked. */
export function FirmDetails({ hints, sources, entityId, onSaved }: { hints: Record<string, FirmHint>; sources: Record<string, string>; entityId: number; onSaved: (applied: string[]) => void }) {
  const keys = Object.keys(hints).filter((k) => hints[k].state !== "same");
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(keys.map((k) => [k, hints[k].state !== "different"])));
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(keys.map((k) => [k, hints[k].value])));
  const [done, setDone] = useState(false);
  const act = useAction();
  if (!keys.length && !done) return null;
  const save = () => act.run(async () => {
    const body: Record<string, unknown> = {};
    const profile: Record<string, string> = {};
    const applied = keys.filter((k) => on[k] && vals[k]?.trim());
    applied.forEach((k) => { if (hints[k].target === "core") body[k] = vals[k].trim(); else profile[k] = vals[k].trim(); });
    if (Object.keys(profile).length) body.profile = profile;
    await api(`/entities/${entityId}`, { method: "PUT", json: body });
    setDone(true);
    onSaved(applied);
  });
  return (
    <Panel title={t("Firm details found in the documents")}>
      <ErrorBox error={act.error} />
      {done ? <Notice>{t("Added to the firm's profile.")}</Notice> : (
        <>
          <p className="mb-2 text-xs text-muted">{t("Ticked lines are added to the firm's profile. Lines that would change a value already recorded are not ticked: tick them only if the document is newer.")}</p>
          <div className="overflow-x-auto"><table className="datagrid">
            <thead><tr><th></th><th>{t("Detail")}</th><th>{t("Found in the document")}</th><th>{t("Recorded now")}</th><th>{t("Where it was read")}</th></tr></thead>
            <tbody>{keys.map((k) => (
              <tr key={k}>
                <td><input type="checkbox" aria-label={`Add ${hints[k].label}`} checked={!!on[k]} onChange={(e) => setOn({ ...on, [k]: e.target.checked })} /></td>
                <td className="text-sm">{t(hints[k].label)}</td>
                <td><input className={`${inputCls} w-64`} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
                  {hints[k].note && <div className="text-xs text-gap">{hints[k].note}</div>}</td>
                <td className={`text-sm ${hints[k].state === "different" ? "text-gap" : "text-muted"}`}>{hints[k].current || "—"}</td>
                <td className="max-w-xs text-xs text-muted">“{hints[k].excerpt}”<div>{sources[k]}</div></td>
              </tr>))}</tbody>
          </table></div>
          <Button className="mt-3" disabled={act.busy || !keys.some((k) => on[k])} onClick={save}>{t("Add ticked details to the firm")}</Button>
        </>
      )}
    </Panel>
  );
}

type Item = { id: number; file: File; status: "reading" | "ready" | "saving" | "saved" | "failed"; error?: string; result?: ReadResult; draft?: Draft };

/** Firm page: choose documents, TenderPilot reads them, the person checks the values and saves. */
export default function ReadDocuments({ entityId, firmName, cats, onChange }: { entityId: number; firmName: string; cats: string[]; onChange: () => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [hintsKey, setHintsKey] = useState(0);
  const [applied, setApplied] = useState<string[]>([]); // details already added to the firm in this session
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);
  const upd = (id: number, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  const add = async (files: FileList | null) => {
    if (!files?.length) return;
    const news = Array.from(files).map((file) => ({ id: ++seq.current, file, status: "reading" as const }));
    setItems((xs) => [...xs, ...news]);
    for (const it of news) { // one after another: OCR of a scan takes a few seconds each
      try {
        const result = await readDocument(it.file, entityId);
        upd(it.id, { status: "ready", result, draft: draftFrom(result) });
        setHintsKey((k) => k + 1);
      } catch (e) {
        upd(it.id, { status: "failed", error: e instanceof Error ? e.message : String(e) });
      }
    }
    if (input.current) input.current.value = "";
  };
  const save = async (it: Item) => {
    if (!it.draft) return;
    upd(it.id, { status: "saving", error: undefined });
    try {
      await saveDraft(entityId, it.file, it.draft, firmName);
      upd(it.id, { status: "saved" });
      onChange();
    } catch (e) {
      upd(it.id, { status: "ready", error: e instanceof Error ? e.message : String(e) });
    }
  };
  const ready = items.filter((x) => x.status === "ready" && x.draft?.title);
  // Firm details across all documents: first document that has a value wins; the source is shown.
  const hints: Record<string, FirmHint> = {}, sources: Record<string, string> = {};
  items.forEach((it) => Object.entries(it.result?.firm || {}).forEach(([k, h]) => { if (!hints[k] && !applied.includes(k)) { hints[k] = h; sources[k] = it.file.name; } }));
  return (
    <div className="space-y-4">
      <Panel title={t("Add documents: details are read for you")}>
        <p className="text-sm">{t("Choose one or more documents (PDF, scan or photo, Word, Excel): GST and PAN, incorporation, Udyam, EPF/ESI, contractor registration, work orders, completion certificates, CA turnover certificates, ISO, cancelled cheque.")}</p>
        <p className="mt-1 text-xs text-muted">{t("TenderPilot reads them on this server; nothing is sent outside. Check each value against the document before saving: saved records stay unverified until a reviewer checks them.")}</p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input ref={input} type="file" multiple accept="application/pdf,image/*,.docx,.xlsx" className="hidden" onChange={(e) => add(e.target.files)} />
          <Button onClick={() => input.current?.click()}>{t("Choose documents")}</Button>
          {ready.length > 1 && <Button kind="secondary" onClick={async () => { for (const it of ready) await save(it); }}>{t("Save all checked documents")} ({ready.length})</Button>}
        </div>
      </Panel>
      {items.map((it) => <ReadCard key={it.id} it={it} cats={cats} onDraft={(d) => upd(it.id, { draft: d })} onSave={() => save(it)}
        onRemove={() => setItems((xs) => xs.filter((x) => x.id !== it.id))} />)}
      {Object.keys(hints).length > 0 && <FirmDetails key={hintsKey} hints={hints} sources={sources} entityId={entityId} onSaved={(a) => { setApplied((x) => [...x, ...a]); onChange(); }} />}
    </div>
  );
}

function ReadCard({ it, cats, onDraft, onSave, onRemove }: { it: Item; cats: string[]; onDraft: (d: Draft) => void; onSave: () => void; onRemove: () => void }) {
  const r = it.result, d = it.draft;
  const head = <span>{it.file.name}{r && <span className="ml-2 text-xs font-normal text-muted">{r.read_by && `${t("read from")} ${r.read_by}`}{r.pages_total > 1 && `, ${r.pages_read}/${r.pages_total} ${t("pages")}`}</span>}</span>;
  if (it.status === "reading") return <Panel title={head}><p className="text-sm text-muted" role="status">{t("Reading the document…")}</p></Panel>;
  if (it.status === "failed" || !r || !d) return <Panel title={head} actions={<Button kind="ghost" onClick={onRemove}>{t("Remove")}</Button>}><ErrorBox error={it.error || t("Could not read")} /></Panel>;
  if (it.status === "saved") return <Panel title={head}><Notice>{t("Saved to the firm's records as unverified.")} {d.rows.filter((x) => x.on).length > 1 && `(${d.rows.filter((x) => x.on).length} ${t("financial years")})`}</Notice></Panel>;
  const keys = Array.from(new Set([...(FIELDS[d.category] || []), ...Object.keys(d.fields)]));
  const set = (patch: Partial<Draft>) => onDraft({ ...d, ...patch });
  const src = (k: string) => (r.excerpts[k] ? `“${r.excerpts[k]}”` : undefined);
  const multi = d.rows.length > 1 && ["turnover_certificate", "audited_accounts"].includes(d.category);
  return (
    <Panel title={head} actions={<Button kind="ghost" onClick={onRemove}>{t("Remove")}</Button>}>
      <ErrorBox error={it.error || null} />
      {!r.readable && <Notice tone="warn">{t("No text could be read from this file. Choose the kind of document and enter the values yourself, or upload a clearer scan.")}</Notice>}
      {r.notes.map((n) => <p key={n} className="text-xs text-gap">{n}</p>)}
      {r.readable && !r.category_sure && <p className="text-xs text-gap">{t("Check the kind of document: TenderPilot is not sure.")}</p>}
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        <Field label="Kind of document"><select className={inputCls} value={d.category} onChange={(e) => set({ category: e.target.value })}>
          {cats.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select></Field>
        <Field label="Title"><input className={inputCls} value={d.title} onChange={(e) => set({ title: e.target.value })} /></Field>
        <Field label="Issued by"><input className={inputCls} value={d.issuer} onChange={(e) => set({ issuer: e.target.value })} /></Field>
        <Field label="Name printed on document" hint="Leave blank if identical to the firm's legal name."><input className={inputCls} value={d.subject_name_on_document} onChange={(e) => set({ subject_name_on_document: e.target.value })} /></Field>
        <Field label="Issue date"><input type="date" className={inputCls} value={d.issue_date} onChange={(e) => set({ issue_date: e.target.value })} /></Field>
        <Field label="Expiry date"><input type="date" className={inputCls} value={d.expiry_date} onChange={(e) => set({ expiry_date: e.target.value })} /></Field>
        {!multi && keys.map((k) => (
          <Field key={k} label={k.replace(/_/g, " ")} hint={src(k) || (k.includes("value") || k === "turnover" || k === "amount" || k === "net_worth" ? "Rupees, digits only" : undefined)}>
            {k === "is_similar" ? <select className={inputCls} value={d.fields[k] || ""} onChange={(e) => set({ fields: { ...d.fields, [k]: e.target.value } })}>
              <option value="">Not assessed</option><option value="yes">Yes</option><option value="no">No</option></select>
              : <input className={inputCls} value={d.fields[k] || ""} onChange={(e) => set({ fields: { ...d.fields, [k]: e.target.value } })} />}
          </Field>))}
      </div>
      {multi && (
        <div className="mt-3">
          <div className="text-sm font-medium">{t("Turnover by financial year")} <span className="text-xs font-normal text-muted">({t("one record per ticked year")})</span></div>
          <table className="datagrid mt-1"><thead><tr><th></th><th>FY</th><th>{t("Turnover (Rs)")}</th><th>{t("Where it was read")}</th></tr></thead>
            <tbody>{d.rows.map((row, i) => (
              <tr key={row.fy}>
                <td><input type="checkbox" aria-label={`Save ${row.fy}`} checked={row.on} onChange={(e) => set({ rows: d.rows.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)) })} /></td>
                <td>{row.fy}</td>
                <td><input className={`${inputCls} w-40`} value={row.turnover} onChange={(e) => set({ rows: d.rows.map((x, j) => (j === i ? { ...x, turnover: e.target.value.replace(/[^\d.]/g, "") } : x)) })} /></td>
                <td className="text-xs text-muted">“{r.rows[i]?.excerpt}”</td>
              </tr>))}</tbody></table>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <Button disabled={it.status === "saving" || !d.title || (multi && !d.rows.some((x) => x.on))} onClick={onSave}>{it.status === "saving" ? t("Saving…") : t("Save to the firm's records")}</Button>
      </div>
    </Panel>
  );
}
