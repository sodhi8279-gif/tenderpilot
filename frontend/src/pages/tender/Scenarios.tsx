import { useState } from "react";
import { api, inr } from "../../api";
import { Button, ErrorBox, inputCls, useAction } from "../../ui";

const TRADES = ["civil", "mechanical", "electrical", "piping", "instrumentation", "testing", "om"];
const TRADE_LABEL: Record<string, string> = { om: "O&M", civil: "Civil", mechanical: "Mechanical", electrical: "Electrical",
  piping: "Piping", instrumentation: "Instrumentation", testing: "Testing" };

export type ScenarioCase = { name: string; rate_change_pct: Record<string, string>; delay_months: string; monthly_time_cost: string;
  payment_cycle_days: string; retention_pct: string; retention_months: string };
type Result = { base: Record<string, string>; trade_costs: Record<string, string>;
  cases: { name: string; cost: string; margin: string; margin_pct_of_price: string | null; notes: string[] }[] };

const blank = (name: string): ScenarioCase => ({ name, rate_change_pct: {}, delay_months: "", monthly_time_cost: "", payment_cycle_days: "",
  retention_pct: "", retention_months: "12" });
const STANDARD: ScenarioCase[] = [
  { ...blank("Steel and cement +8%"), rate_change_pct: { civil: "8" } },
  { ...blank("Pumps and panels +5%"), rate_change_pct: { mechanical: "5", electrical: "5" } },
  { ...blank("3-month delay"), delay_months: "3", monthly_time_cost: "450000" },
  { ...blank("90-day payment cycle, 5% retention"), payment_cycle_days: "90", retention_pct: "5" },
];

// Remembered per tender in this browser only (a convenience; nothing is saved on the server).
const storeKey = (tid: number) => `tp_scenarios_${tid}`;
function loadSaved(tid: number): { interest: string; cases: ScenarioCase[] } | null {
  try { const s = localStorage.getItem(storeKey(tid)); return s ? JSON.parse(s) : null; } catch { return null; }
}
function save(tid: number, v: { interest: string; cases: ScenarioCase[] }) {
  try { localStorage.setItem(storeKey(tid), JSON.stringify(v)); } catch { /* storage blocked */ }
}

export default function Scenarios({ tid, quoteId, onClose }: { tid: number; quoteId: number; onClose: () => void }) {
  const saved = loadSaved(tid);
  const [interest, setInterest] = useState(saved?.interest ?? "10");
  const [cases, setCases] = useState<ScenarioCase[]>(saved?.cases ?? STANDARD);
  const [res, setRes] = useState<Result | null>(null);
  const act = useAction();

  const upd = (i: number, patch: Partial<ScenarioCase>) => setCases(cases.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const run = () => {
    save(tid, { interest, cases });
    return act.run(async () => setRes(await api(`/tenders/${tid}/quotes/${quoteId}/scenarios`, { json: { interest_pct_pa: interest, cases } })));
  };
  const num = (label: string, value: string, onChange: (v: string) => void, hint?: string) => (
    <label className="block text-xs">{label}
      <input inputMode="decimal" className={`${inputCls} mt-0.5 w-full`} value={value} placeholder={hint} onChange={(e) => onChange(e.target.value)} />
    </label>
  );

  return (
    <div className="mt-4 rounded-md border border-line p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium">Scenarios: change the assumptions and compare the margin</div>
        <Button kind="ghost" onClick={onClose}>Close</Button>
      </div>
      <ErrorBox error={act.error} />
      <div className="mb-3 max-w-xs">{num("Interest / cost of money (% per year)", interest, setInterest)}</div>
      <div className="grid gap-3 lg:grid-cols-2">
        {cases.map((c, i) => (
          <fieldset key={i} className="rounded-md border border-line p-3">
            <legend className="sr-only">Scenario {i + 1}</legend>
            <div className="flex gap-2">
              <input aria-label={`Name of scenario ${i + 1}`} className={`${inputCls} flex-1 font-medium`} value={c.name} onChange={(e) => upd(i, { name: e.target.value })} />
              <Button kind="danger" aria-label={`Remove scenario ${i + 1}`} onClick={() => setCases(cases.filter((_, j) => j !== i))}>Remove</Button>
            </div>
            <div className="mt-2 text-xs font-medium text-muted">Rate change by trade (%, use minus for a fall)</div>
            <div className="mt-1 grid grid-cols-3 gap-2 sm:grid-cols-4">
              {TRADES.map((t) => num(TRADE_LABEL[t], c.rate_change_pct[t] ?? "", (v) => upd(i, { rate_change_pct: { ...c.rate_change_pct, [t]: v } })))}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {num("Delay (months)", c.delay_months, (v) => upd(i, { delay_months: v }))}
              {num("Time cost per month (₹)", c.monthly_time_cost, (v) => upd(i, { monthly_time_cost: v }), "site overheads, hire")}
              {num("Payment cycle (days)", c.payment_cycle_days, (v) => upd(i, { payment_cycle_days: v }))}
              {num("Retention (%)", c.retention_pct, (v) => upd(i, { retention_pct: v }))}
              {num("Retention held (months)", c.retention_months, (v) => upd(i, { retention_months: v }))}
            </div>
          </fieldset>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={act.busy || !cases.length} onClick={run}>Calculate</Button>
        <Button kind="secondary" disabled={cases.length >= 20} onClick={() => setCases([...cases, blank(`Scenario ${cases.length + 1}`)])}>Add scenario</Button>
        <Button kind="secondary" onClick={() => setCases(STANDARD)}>Reset to standard cases</Button>
      </div>
      <p className="mt-1 text-xs text-muted">Blank fields count as zero. Your scenarios are remembered in this browser for this tender. They never change the quote.</p>
      {res && (
        <div className="mt-4 overflow-x-auto">
          <table className="datagrid"><thead><tr><th>Case</th><th>Cost</th><th>Margin</th><th>Margin % of price</th><th>Working</th></tr></thead>
            <tbody><tr><td>Base (frozen quote)</td><td>{inr(res.base.cost, 0)}</td><td>{inr(res.base.margin, 0)}</td><td></td>
              <td className="text-xs text-muted">Cost by trade: {Object.entries(res.trade_costs).map(([t, v]) => `${TRADE_LABEL[t] || t} ${inr(v, 0)}`).join("; ")}</td></tr>
              {res.cases.map((c, i) => <tr key={i}><td>{c.name}</td><td>{inr(c.cost, 0)}</td><td className={Number(c.margin) < 0 ? "text-fail" : ""}>{inr(c.margin, 0)}</td>
                <td>{c.margin_pct_of_price}%</td><td className="text-xs text-muted">{c.notes.join("; ") || "no change"}</td></tr>)}</tbody></table>
        </div>
      )}
    </div>
  );
}
