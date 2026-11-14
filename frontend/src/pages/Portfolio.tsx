import { useState } from "react";
import { t as tr } from "../i18n";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useMe } from "../App";
import { Button, Empty, ErrorBox, fmtDate, inputCls, Loading, useLoad } from "../ui";

type T = { id: number; title: string; tender_ref: string; department: string; deadline_at: string | null; status: string;
  blocker_count: number; critical_count: number; workload_percent: number; bidder_name: string | null; current_version: number };

function daysLeft(d: string | null) {
  if (!d) return null;
  return Math.ceil((new Date(d).getTime() - Date.now()) / 86400000);
}

export default function Portfolio() {
  const { can } = useMe();
  const { data, error, loading, reload } = useLoad<T[]>(() => api("/tenders"));
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("active");
  const rows = (data || []).filter((t) => (status === "all" || t.status === status) &&
    (t.title + t.tender_ref + t.department).toLowerCase().includes(q.toLowerCase()));
  const sorted = [...rows].sort((a, b) => b.critical_count - a.critical_count || (daysLeft(a.deadline_at) ?? 999) - (daysLeft(b.deadline_at) ?? 999));
  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{tr("Tenders")}</h1>
          <p className="text-sm text-muted">{tr("Every bid in preparation, with the most urgent first.")}</p>
        </div>
        {can("tender.write") && <Link to="/tenders/new"><Button>{tr("New tender")}</Button></Link>}
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <input aria-label="Search tenders" placeholder={tr("Search title, tender ID or department")} className={`${inputCls} max-w-sm`} value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Filter by status" className={`${inputCls} w-44`} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="active">{tr("In preparation")}</option><option value="submitted">{tr("Submitted")}</option><option value="all">{tr("All tenders")}</option>
        </select>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Loading /> : sorted.length === 0 ? (
        <Empty>{tr(data?.length ? "No tenders match this filter." : "No tenders yet. Create one and upload the tender volumes to begin.")}</Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-panel">
          <table className="datagrid">
            <thead><tr><th>{tr("Tender")}</th><th>{tr("Bidder")}</th><th>{tr("Deadline")}</th><th>{tr("Open blockers")}</th><th>{tr("Checklist closed")}</th></tr></thead>
            <tbody>
              {sorted.map((t) => {
                const dl = daysLeft(t.deadline_at);
                return (
                  <tr key={t.id}>
                    <td>
                      <Link className="font-medium text-ink hover:underline" to={`/tenders/${t.id}`}>{t.title}</Link>
                      <div className="text-xs text-muted">{t.tender_ref || "No tender ID yet"}{t.department && `, ${t.department}`}{t.current_version > 1 && ` (amended ${t.current_version - 1}×)`}</div>
                    </td>
                    <td>{t.bidder_name || <span className="text-gap">{tr("Not chosen")}</span>}</td>
                    <td>
                      <div>{fmtDate(t.deadline_at)}</div>
                      {dl !== null && <div className={`text-xs ${dl <= 3 ? "font-semibold text-fail" : "text-muted"}`}>{dl < 0 ? tr("Deadline passed") : `${dl} day${dl === 1 ? "" : "s"} left`}</div>}
                    </td>
                    <td>{t.critical_count > 0 && <span className="font-semibold text-fail">{t.critical_count} critical, </span>}<span className="text-muted">{t.blocker_count} total</span></td>
                    <td>{t.workload_percent}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
