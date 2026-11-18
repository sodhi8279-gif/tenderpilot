import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, inr } from "../api";
import { useMe } from "../App";
import { t } from "../i18n";
import { Button, Empty, ErrorBox, Field, inputCls, Loading, Notice, Panel, useAction, useLoad } from "../ui";
import { draftFrom, readDocument, ReadResult, saveDraft } from "./ReadDocuments";

export type FirmSummary = {
  profile_missing: string[]; counts: Record<string, number>; total: number; unverified: number; expired: number; expiring_30_days: number;
  expiring: { id: number; title: string; expiry_date: string; expired: boolean }[];
  turnover_by_fy: { fy: string; amount: string }[]; net_worth: { amount: string; as_of: string | null } | null;
  works: { count: number; total_value: string }; machinery_units: string; personnel: number;
  tenders?: { id: number; title: string; tender_ref: string; department: string; role: string; status: string; deadline_at: string | null }[];
};
export type Firm = { id: number; legal_name: string; entity_type: string; registered_address: string; pan: string | null; gstin: string | null;
  signatory_name: string | null; signatory_designation: string | null; profile: Record<string, string>; summary: FirmSummary; tender_count?: number };
export type FirmSchema = { groups: Record<string, { label: string; categories: string[] }>; profile_fields: Record<string, string>; agency_fields: Record<string, string> };

export const CONSTITUTIONS: [string, string][] = [["company", "Private / public limited company"], ["llp", "LLP"], ["partnership", "Partnership firm"],
  ["proprietorship", "Proprietorship"], ["individual", "Individual"], ["public_sector", "Public sector undertaking"], ["society", "Society / trust"]];
export const constitution = (v: string) => CONSTITUTIONS.find(([k]) => k === v)?.[1] || v;

/** Key figures and warnings for one firm (library cards, firm page, tender participants). */
export function FirmFacts({ s, compact = false }: { s: FirmSummary; compact?: boolean }) {
  const last3 = s.turnover_by_fy.slice(0, 3);
  return (
    <div className="space-y-1.5 text-sm">
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span><span className="text-muted">{t("Turnover")}: </span>{last3.length ? last3.map((x) => `${x.fy} ${inr(x.amount, 0)}`).join(" · ") : <span className="text-gap">{t("not recorded")}</span>}</span>
        {s.net_worth && <span><span className="text-muted">{t("Net worth")}: </span>{inr(s.net_worth.amount, 0)}</span>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span><span className="text-muted">{t("Completed works")}: </span>{s.works.count}{s.works.count > 0 && ` (${inr(s.works.total_value, 0)})`}</span>
        <span><span className="text-muted">{t("Machinery")}: </span>{s.machinery_units}</span>
        <span><span className="text-muted">{t("Personnel")}: </span>{s.personnel}</span>
        {!compact && <span><span className="text-muted">{t("Documents")}: </span>{s.total}</span>}
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        {s.expired > 0 && <span className="rounded bg-[#fde8e6] px-1.5 py-0.5 text-fail">{s.expired} {t("expired")}</span>}
        {s.expiring_30_days > 0 && <span className="rounded bg-[#fbeedb] px-1.5 py-0.5 text-[#6b3e00]">{s.expiring_30_days} {t("expiring within 30 days")}</span>}
        {s.unverified > 0 && <span className="rounded bg-[#eeeff1] px-1.5 py-0.5 text-na">{s.unverified} {t("not verified")}</span>}
        {s.profile_missing.length > 0 && <span className="rounded bg-[#fbeedb] px-1.5 py-0.5 text-[#6b3e00]">{t("Profile missing")}: {s.profile_missing.join(", ")}</span>}
      </div>
    </div>
  );
}

/** Add or edit a client firm: identity, registrations, contacts, bank. */
export function FirmForm({ firm, schema, onSaved, onCancel }: { firm?: Firm; schema: FirmSchema; onSaved: (id: number) => void; onCancel?: () => void }) {
  const core = { legal_name: firm?.legal_name || "", entity_type: firm?.entity_type || "company", registered_address: firm?.registered_address || "",
    pan: firm?.pan || "", gstin: firm?.gstin || "", signatory_name: firm?.signatory_name || "", signatory_designation: firm?.signatory_designation || "" };
  const [f, setF] = useState(core);
  const [p, setP] = useState<Record<string, string>>({ ...(firm?.profile || {}) });
  const [warn, setWarn] = useState<string[]>([]);
  const [read, setRead] = useState<{ file: File; result: ReadResult }[]>([]);
  const [reading, setReading] = useState<string | null>(null);
  const [filled, setFilled] = useState<string[]>([]);
  const [keepDocs, setKeepDocs] = useState(true);
  const [readError, setReadError] = useState<string | null>(null);
  const act = useAction();
  const fileRef = useRef<HTMLInputElement>(null);
  /** Fill EMPTY boxes from the chosen documents; what is already typed is never changed. */
  const fillFrom = async (files: FileList | null) => {
    if (!files?.length) return;
    const got: { file: File; result: ReadResult }[] = [];
    let nf = { ...f }, np = { ...p };
    const names: string[] = [];
    for (const file of Array.from(files)) {
      setReading(`${t("Reading")} ${file.name}…`);
      try {
        const result = await readDocument(file, firm?.id);
        got.push({ file, result });
        Object.entries(result.firm).forEach(([k, h]) => {
          const cur = nf as Record<string, string>;
          if (h.target === "core") {
            const empty = k === "entity_type" ? !firm && !names.includes(h.label) : k in cur && !cur[k];
            if (empty) { nf = { ...nf, [k]: h.value }; names.push(h.label); }
          } else if (!np[k]) { np = { ...np, [k]: h.value }; names.push(h.label); }
        });
      } catch (e) {
        setReadError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    setF(nf); setP(np); setRead((r) => [...r, ...got]); setFilled((x) => Array.from(new Set([...x, ...names]))); setReading(null);
    if (fileRef.current) fileRef.current.value = "";
  };
  const pf = (k: string, label?: string, type = "text") => (
    <Field key={k} label={label || schema.profile_fields[k]}>
      <input type={type} className={inputCls} value={p[k] || ""} onChange={(e) => setP({ ...p, [k]: e.target.value })} />
    </Field>
  );
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const body = { ...f, pan: f.pan || null, gstin: f.gstin || null, profile: p };
    let id = 0;
    act.run(async () => {
      const r = firm ? await api(`/entities/${firm.id}`, { method: "PUT", json: body }) : await api("/entities", { json: body });
      setWarn(r.similar_names_warning || []);
      id = r.id;
      if (!firm && keepDocs) for (const x of read) if (x.result.readable) await saveDraft(id, x.file, draftFrom(x.result), f.legal_name);
    }).then((ok) => { if (ok) onSaved(id); });
  };
  const section = (title: string) => <div className="sm:col-span-3 mt-2 border-b border-line pb-1 text-xs font-semibold uppercase tracking-wide text-muted">{t(title)}</div>;
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-3">
      <div className="sm:col-span-3"><ErrorBox error={act.error} />
        {warn.length > 0 && <Notice tone="warn">A firm with a similar name already exists: {warn.join(", ")}.</Notice>}
        <div className="rounded-md border border-dashed border-teal bg-teal-soft/40 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <input ref={fileRef} type="file" multiple accept="application/pdf,image/*,.docx,.xlsx" className="hidden" onChange={(e) => fillFrom(e.target.files)} />
            <Button type="button" kind="secondary" disabled={!!reading} onClick={() => fileRef.current?.click()}>{t("Fill from documents")}</Button>
            <span className="text-xs text-muted">{reading || t("Choose the GST certificate, PAN, incorporation certificate, Udyam, cancelled cheque…: empty boxes are filled from them.")}</span>
          </div>
          <ErrorBox error={readError} />
          {filled.length > 0 && <p className="mt-2 text-xs">{t("Filled from the documents")}: {filled.map((x) => t(x)).join(", ")}. <strong>{t("Check each value before saving.")}</strong></p>}
          {read.length > 0 && !firm && <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={keepDocs} onChange={(e) => setKeepDocs(e.target.checked)} />
            {t("Also keep these documents in the firm's records")} ({read.length}, {t("unverified until checked")})</label>}
        </div></div>
      {section("Identity")}
      <Field label="Legal name (as registered)"><input required className={inputCls} value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} /></Field>
      <Field label="Constitution"><select className={inputCls} value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })}>
        {CONSTITUTIONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
      {pf("short_name")}
      <Field label="Registered address"><textarea rows={2} className={inputCls} value={f.registered_address} onChange={(e) => setF({ ...f, registered_address: e.target.value })} /></Field>
      {pf("incorporation_date", undefined, "date")}
      {pf("cin")}
      {section("Tax and statutory registrations")}
      <Field label="PAN"><input className={inputCls} value={f.pan} onChange={(e) => setF({ ...f, pan: e.target.value.toUpperCase() })} /></Field>
      <Field label="GSTIN"><input className={inputCls} value={f.gstin} onChange={(e) => setF({ ...f, gstin: e.target.value.toUpperCase() })} /></Field>
      {pf("tan")}{pf("udyam")}{pf("msme_category")}{pf("epf")}{pf("esi")}{pf("labour_licence")}
      {section("Authorised signatory and contacts")}
      <Field label="Authorised signatory"><input className={inputCls} value={f.signatory_name} onChange={(e) => setF({ ...f, signatory_name: e.target.value })} /></Field>
      <Field label="Signatory designation"><input className={inputCls} value={f.signatory_designation} onChange={(e) => setF({ ...f, signatory_designation: e.target.value })} /></Field>
      {pf("email", undefined, "email")}{pf("phone")}{pf("contact_person")}{pf("contact_mobile")}{pf("website")}
      {section("Bank account for EMD refunds")}
      {pf("bank_name")}{pf("bank_branch")}{pf("bank_account")}{pf("bank_ifsc")}
      <div className="sm:col-span-3"><Field label={schema.profile_fields.notes}><textarea rows={2} className={inputCls} value={p.notes || ""} onChange={(e) => setP({ ...p, notes: e.target.value })} /></Field></div>
      <p className="sm:col-span-3 text-xs text-muted">{t("Contractor registrations (department and class), certificates and credentials are added as documents on the firm's page, so they can be verified and used for eligibility.")}</p>
      <div className="sm:col-span-3 flex gap-2">
        <Button disabled={act.busy}>{firm ? t("Save firm") : t("Add firm")}</Button>
        {onCancel && <Button kind="secondary" onClick={onCancel}>{t("Cancel")}</Button>}
      </div>
    </form>
  );
}

/** Client firms tab: one card per firm with what it brings to a tender. */
export default function FirmLibrary() {
  const { can } = useMe();
  const firms = useLoad<Firm[]>(() => api("/entities?summary=true"));
  const schema = useLoad<FirmSchema>(() => api("/firms/schema"));
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  if (!firms.data || !schema.data) return firms.error ? <ErrorBox error={firms.error} onRetry={firms.reload} /> : <Loading />;
  const list = firms.data.filter((f) => !q || `${f.legal_name} ${f.profile?.short_name || ""}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <input aria-label={t("Search firms")} placeholder={t("Search firms")} className={`${inputCls} max-w-sm`} value={q} onChange={(e) => setQ(e.target.value)} />
        {can("vault.write") && !adding && <Button onClick={() => setAdding(true)}>{t("Add client firm")}</Button>}
      </div>
      {adding && (
        <Panel title="Add client firm">
          <FirmForm schema={schema.data} onSaved={() => { setAdding(false); firms.reload(); }} onCancel={() => setAdding(false)} />
        </Panel>
      )}
      {!list.length ? <Empty>{firms.data.length ? t("No firm matches the search.") : t("No client firms yet. Add the first firm you submit tenders for.")}</Empty> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((f) => (
            <Link key={f.id} to={`/firms/${f.id}`} className="block rounded-lg border border-line bg-white p-4 shadow-sm transition hover:border-teal hover:shadow">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-semibold">{f.legal_name}</div>
                  <div className="text-xs text-muted">{constitution(f.entity_type)}{f.gstin && ` · GSTIN ${f.gstin}`}{f.profile?.udyam && " · MSME"}</div>
                </div>
                <span className="whitespace-nowrap rounded bg-teal-soft px-2 py-0.5 text-xs text-[#0b4f4c]">{f.tender_count || 0} {t(f.tender_count === 1 ? "tender" : "tenders")}</span>
              </div>
              <div className="mt-3"><FirmFacts s={f.summary} /></div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
