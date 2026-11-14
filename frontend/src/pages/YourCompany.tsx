import { useEffect, useState } from "react";
import { api } from "../api";
import { useMe } from "../App";
import { Button, ErrorBox, Field, inputCls, Loading, Panel, useAction, useLoad } from "../ui";

const FIELDS: [string, string][] = [["name", "Company name"], ["contact_person", "Contact person"], ["address", "Address"],
  ["phone", "Phone"], ["email", "E-mail"], ["website", "Website"], ["gstin", "GSTIN"], ["pan", "PAN"]];

/** The bid-submission company's own details (not a bidder). Shown on letters it prepares as "agency" fields. */
export default function YourCompany() {
  const { can } = useMe();
  const p = useLoad<Record<string, string>>(() => api("/org/profile"));
  const [f, setF] = useState<Record<string, string>>({});
  const act = useAction();
  useEffect(() => { if (p.data) setF(p.data); }, [p.data]);
  if (!p.data) return <Panel title="Your company">{p.error ? <ErrorBox error={p.error} onRetry={p.reload} /> : <Loading />}</Panel>;
  const admin = can("org.admin");
  return (
    <Panel title="Your company">
      <ErrorBox error={act.error} />
      <p className="mb-3 text-sm text-muted">Your own company, which prepares and submits tenders for the client firms. It is not a bidder; bidders are chosen per tender from Client firms.
        Forms can use these details as "Submitting company" fields.</p>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); act.run(async () => { p.setData(await api("/org/profile", { method: "PUT", json: f })); }); }}>
        {FIELDS.map(([k, label]) => (
          <Field key={k} label={label}>
            {k === "address" ? <textarea rows={2} disabled={!admin} className={inputCls} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
              : <input disabled={!admin} required={k === "name"} className={inputCls} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: k === "gstin" || k === "pan" ? e.target.value.toUpperCase() : e.target.value })} />}
          </Field>
        ))}
        {admin && <div className="sm:col-span-2"><Button disabled={act.busy}>Save</Button></div>}
      </form>
    </Panel>
  );
}
