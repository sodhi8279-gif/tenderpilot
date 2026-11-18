import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, inr } from "../api";
import { useMe } from "../App";
import { t } from "../i18n";
import { Button, Empty, ErrorBox, fmtDay, Loading, Panel, Status, Tabs, useLoad } from "../ui";
import { constitution, Firm, FirmFacts, FirmForm, FirmSchema } from "./Firms";
import ReadDocuments from "./ReadDocuments";
import { Evidence } from "./Vault";

/** One client firm: profile, credentials grouped by kind, and the tenders it is part of. */
export default function FirmPage() {
  const id = Number(useParams().id);
  const { can } = useMe();
  const firm = useLoad<Firm>(() => api(`/entities/${id}?reveal=true`), [id]);
  const schema = useLoad<FirmSchema>(() => api("/firms/schema"));
  const cats = useLoad<string[]>(() => api("/evidence/categories"));
  const [tab, setTab] = useState("profile");
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(0); // records added by the reader: refresh the open tab's list
  if (!firm.data || !schema.data) return firm.error ? <ErrorBox error={firm.error} onRetry={firm.reload} /> : <Loading />;
  const f = firm.data, s = f.summary, g = schema.data.groups;
  const tabs: [string, string][] = [["profile", "Profile"], ...Object.entries(g).map(([k, v]) => [k, `${v.label} (${s.counts[k] || 0})`] as [string, string]),
    ["tenders", `${t("Tenders")} (${s.tenders?.length || 0})`]];
  const row = (label: string, v?: string | null) => (
    <div className="grid grid-cols-[180px_1fr] gap-2 border-b border-line py-1.5 text-sm last:border-0">
      <div className="text-muted">{t(label)}</div><div>{v || <span className="text-gap">{t("not recorded")}</span>}</div>
    </div>
  );
  const pr = f.profile || {};
  const reader = can("vault.write") && cats.data && <ReadDocuments entityId={f.id} firmName={f.legal_name} cats={cats.data} onChange={() => { firm.reload(); setSaved((n) => n + 1); }} />;
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="text-sm"><Link className="text-muted underline" to="/vault">{t("Client firms")}</Link> /</div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{f.legal_name}</h1>
          <div className="text-sm text-muted">{constitution(f.entity_type)}{pr.short_name && ` · ${pr.short_name}`}</div>
        </div>
      </div>
      <Panel title="At a glance"><FirmFacts s={s} /></Panel>
      {!editing && reader}
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "profile" ? (
        editing ? (
          <Panel title="Edit firm"><FirmForm firm={f} schema={schema.data} onSaved={() => { setEditing(false); firm.reload(); }} onCancel={() => setEditing(false)} /></Panel>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Identity and registrations" actions={can("vault.write") && <Button kind="secondary" onClick={() => setEditing(true)}>{t("Edit")}</Button>}>
              {row("Legal name", f.legal_name)}{row("Constitution", constitution(f.entity_type))}{row("Registered address", f.registered_address)}
              {row("Date of incorporation", pr.incorporation_date && fmtDay(pr.incorporation_date))}{row("CIN / LLPIN / registration no.", pr.cin)}
              {row("PAN", f.pan)}{row("GSTIN", f.gstin)}{row("TAN", pr.tan)}{row("Udyam (MSME) registration no.", pr.udyam)}
              {pr.msme_category && row("MSME category", pr.msme_category)}{row("EPF code", pr.epf)}{row("ESI code", pr.esi)}
              {pr.labour_licence && row("Labour licence no.", pr.labour_licence)}
            </Panel>
            <div className="space-y-4">
              <Panel title="Signatory and contacts">
                {row("Authorised signatory", f.signatory_name && `${f.signatory_name}${f.signatory_designation ? `, ${f.signatory_designation}` : ""}`)}
                {row("E-mail", pr.email)}{row("Phone", pr.phone)}{row("Contact person", pr.contact_person && `${pr.contact_person}${pr.contact_mobile ? `, ${pr.contact_mobile}` : ""}`)}
                {pr.website && row("Website", pr.website)}
              </Panel>
              <Panel title="Bank account for EMD refunds">
                {row("Bank", pr.bank_name && `${pr.bank_name}${pr.bank_branch ? `, ${pr.bank_branch}` : ""}`)}{row("Account no.", pr.bank_account)}{row("IFSC", pr.bank_ifsc)}
              </Panel>
              {s.expiring.length > 0 && (
                <Panel title="Expired or expiring documents">
                  <ul className="space-y-1 text-sm">{s.expiring.map((x) => <li key={x.id} className={x.expired ? "text-fail" : "text-gap"}>
                    {x.title}: {x.expired ? t("expired") : t("expires")} {fmtDay(x.expiry_date)}</li>)}</ul>
                </Panel>
              )}
              {pr.notes && <Panel title="Notes"><p className="whitespace-pre-wrap text-sm">{pr.notes}</p></Panel>}
            </div>
          </div>
        )
      ) : tab === "tenders" ? (
        <Panel title="Tenders for this firm">
          {!s.tenders?.length ? <Empty>{t("This firm is not part of any tender yet. Choose it in a tender's Command centre.")}</Empty> : (
            <table className="datagrid"><thead><tr><th>{t("Tender")}</th><th>{t("Participation")}</th><th>{t("Deadline")}</th><th>{t("Status")}</th></tr></thead>
              <tbody>{s.tenders.map((x) => (
                <tr key={x.id}><td><Link className="font-medium underline" to={`/tenders/${x.id}`}>{x.title}</Link>
                  <div className="text-xs text-muted">{x.tender_ref} · {x.department}</div></td>
                  <td className="text-sm">{x.role}</td><td className="text-sm">{x.deadline_at ? fmtDay(x.deadline_at) : "—"}</td><td><Status s={x.status} /></td></tr>))}</tbody></table>
          )}
        </Panel>
      ) : (
        <>
          {tab === "financial" && s.turnover_by_fy.length > 0 && (
            <Panel title="Turnover by financial year">
              <table className="datagrid"><thead><tr><th>FY</th><th>{t("Turnover")}</th></tr></thead>
                <tbody>{s.turnover_by_fy.map((x) => <tr key={x.fy}><td>{x.fy}</td><td>{inr(x.amount, 0)}</td></tr>)}</tbody></table>
            </Panel>
          )}
          <Evidence key={`${tab}-${saved}`} entityId={f.id} categories={g[tab].categories} onChange={firm.reload} />
        </>
      )}
    </div>
  );
}
