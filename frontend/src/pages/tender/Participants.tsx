import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { useMe } from "../../App";
import { t } from "../../i18n";
import { Button, cx, ErrorBox, inputCls, Loading, Panel, useAction, useLoad } from "../../ui";
import { constitution, Firm, FirmFacts, FirmSummary } from "../Firms";

type Part = { mode: "single" | "jv" | null; jv: { id: number; name: string; tender_specific: boolean } | null;
  firms: { id: number; legal_name: string; role: string; share_pct: string; summary: FirmSummary }[] };
type Row = { legal_entity_id: string; role: "lead" | "member"; share_pct: string };

/** Who bids: one client firm, or a JV of client firms, chosen from the library. Their credentials show at once. */
export default function Participants({ tid, rowVersion, reload }: { tid: number; rowVersion: number; reload: () => void }) {
  const { can } = useMe();
  const part = useLoad<Part>(() => api(`/tenders/${tid}/participants`), [tid]);
  const firms = useLoad<Firm[]>(() => api("/entities?summary=true"));
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"single" | "jv">("single");
  const [single, setSingle] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [jvName, setJvName] = useState("");
  const act = useAction();

  // Firms are often added in another tab: refresh the library when the user comes back.
  useEffect(() => {
    const again = () => { if (document.visibilityState === "visible") firms.reload(); };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => { window.removeEventListener("focus", again); document.removeEventListener("visibilitychange", again); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = () => {
    const p = part.data;
    setMode(p?.mode === "jv" ? "jv" : "single");
    setSingle(p?.mode === "single" ? String(p.firms[0].id) : "");
    setRows(p?.mode === "jv" ? p.firms.map((f) => ({ legal_entity_id: String(f.id), role: f.role as Row["role"], share_pct: f.share_pct }))
      : [{ legal_entity_id: "", role: "lead", share_pct: "" }, { legal_entity_id: "", role: "member", share_pct: "" }]);
    setJvName(p?.jv?.name || "");
    setEditing(true);
  };
  useEffect(() => { if (part.data && part.data.mode === null && can("tender.write")) start(); }, [part.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!part.data || !firms.data) return <Panel title="Bidding firm(s)">{part.error || firms.error ? <ErrorBox error={part.error || firms.error} /> : <Loading />}</Panel>;
  const total = rows.reduce((s, r) => s + (Number(r.share_pct) || 0), 0);
  const chosen = new Set(rows.map((r) => r.legal_entity_id).filter(Boolean));
  const upd = (i: number, p: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : p.role === "lead" ? { ...r, role: "member" } : r)));
  const save = () => act.run(() => api(`/tenders/${tid}/participants`, { method: "PUT", json: mode === "single"
    ? { mode, entity_id: Number(single), row_version: rowVersion }
    : { mode, jv_name: jvName || null, row_version: rowVersion, members: rows.map((r) => ({ ...r, legal_entity_id: Number(r.legal_entity_id) })) } }),
  () => { setEditing(false); part.reload(); firms.reload(); reload(); });
  const preview = (id: string) => firms.data!.find((f) => String(f.id) === id);
  const canSave = mode === "single" ? !!single : rows.length >= 2 && rows.every((r) => r.legal_entity_id && Number(r.share_pct) > 0)
    && chosen.size === rows.length && total === 100 && rows.filter((r) => r.role === "lead").length === 1;

  return (
    <Panel title="Bidding firm(s)" actions={!editing && can("tender.write") && <Button kind="secondary" onClick={start}>{t("Change")}</Button>}>
      <ErrorBox error={act.error} />
      {!editing ? (
        <div className="space-y-3">
          {part.data.jv && <div className="text-sm"><span className="text-muted">{t("Joint venture")}: </span><span className="font-medium">{part.data.jv.name}</span></div>}
          {part.data.firms.map((f) => (
            <div key={f.id} className="rounded-md border border-line p-3">
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <Link to={`/firms/${f.id}`} className="font-semibold underline-offset-2 hover:underline">{f.legal_name}</Link>
                <span className="text-xs text-muted">{f.role === "bidder" ? t("Single bidder") : `${f.role === "lead" ? t("Lead partner") : t("Partner")} · ${f.share_pct}%`}</span>
              </div>
              <FirmFacts s={f.summary} compact />
            </div>
          ))}
          <p className="text-xs text-muted">{t("Eligibility, the checklist and the forms use these firms' documents from the Client firms library. Whether JV partners' credentials can be added together is decided per criterion from the tender's JV clause.")}</p>
        </div>
      ) : !firms.data.length ? (
        <p className="rounded bg-[#fbeedb] px-3 py-2 text-sm text-[#6b3e00]">
          {t("No client firms yet.")} <Link className="font-medium underline" to="/vault">{t("Add the firm in Client firms")}</Link>{t(", with its documents. It then appears here.")}
        </p>
      ) : (
        <div className="space-y-4 text-sm">
          <div role="radiogroup" aria-label={t("Participation")} className="inline-flex rounded-md border border-line p-0.5">
            {([["single", "Single firm"], ["jv", "Joint venture (JV)"]] as const).map(([k, label]) => (
              <button key={k} role="radio" aria-checked={mode === k} className={cx("rounded px-3 py-1.5", mode === k ? "bg-teal text-white" : "hover:bg-[#f3f2ee]")}
                onClick={() => setMode(k)}>{t(label)}</button>
            ))}
          </div>
          {mode === "single" ? (
            <div className="space-y-3">
              <select aria-label={t("Bidding firm")} className={`${inputCls} max-w-xl`} value={single} onChange={(e) => setSingle(e.target.value)}>
                <option value="">{t("Choose a client firm")}</option>
                {firms.data.map((f) => <option key={f.id} value={f.id}>{f.legal_name}</option>)}
              </select>
              {single && preview(single) && <div className="rounded-md border border-line p-3"><div className="mb-1 text-xs text-muted">{constitution(preview(single)!.entity_type)}</div><FirmFacts s={preview(single)!.summary} compact /></div>}
            </div>
          ) : (
            <div className="space-y-3">
              {rows.map((r, i) => (
                <div key={i} className="rounded-md border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <select aria-label={`${t("Partner")} ${i + 1}`} className={`${inputCls} min-w-[16rem] flex-1`} value={r.legal_entity_id} onChange={(e) => upd(i, { legal_entity_id: e.target.value })}>
                      <option value="">{t("Choose a client firm")}</option>
                      {firms.data!.map((f) => <option key={f.id} value={f.id} disabled={chosen.has(String(f.id)) && r.legal_entity_id !== String(f.id)}>{f.legal_name}</option>)}
                    </select>
                    <label className="flex items-center gap-1"><input type="radio" name="jv-lead" checked={r.role === "lead"} onChange={() => upd(i, { role: "lead" })} />{t("Lead")}</label>
                    <label className="flex items-center gap-1">{t("Share")}
                      <input aria-label={`${t("Share")} ${i + 1}`} inputMode="decimal" className={`${inputCls} w-20`} value={r.share_pct} onChange={(e) => upd(i, { share_pct: e.target.value })} />%</label>
                    {rows.length > 2 && <Button kind="ghost" onClick={() => setRows(rows.filter((_, j) => j !== i))}>{t("Remove")}</Button>}
                  </div>
                  {r.legal_entity_id && preview(r.legal_entity_id) && <div className="mt-2"><FirmFacts s={preview(r.legal_entity_id)!.summary} compact /></div>}
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-3">
                <Button kind="secondary" onClick={() => setRows([...rows, { legal_entity_id: "", role: "member", share_pct: "" }])}>{t("Add partner")}</Button>
                <span className={total === 100 ? "text-pass" : "text-gap"}>{t("Shares total")} {total}%</span>
              </div>
              <label className="block max-w-xl"><span className="mb-1 block font-medium">{t("JV name (optional)")}</span>
                <input className={inputCls} placeholder={t("Made from the partners' names if left blank")} value={jvName} onChange={(e) => setJvName(e.target.value)} /></label>
            </div>
          )}
          <div className="flex gap-2">
            <Button disabled={act.busy || !canSave} onClick={save}>{t("Save bidding firm(s)")}</Button>
            {part.data.mode && <Button kind="secondary" onClick={() => setEditing(false)}>{t("Cancel")}</Button>}
          </div>
        </div>
      )}
    </Panel>
  );
}
