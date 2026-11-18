import { useEffect, useState } from "react";
import { api, download } from "../api";
import { Button, ErrorBox, inputCls, Loading, Notice, Panel, useAction, useLoad } from "../ui";

type Net = {
  config: { network: boolean; port: number; https: boolean }; active: { network: boolean; port: number; https: boolean };
  restart_needed: boolean; addresses: { names: string[]; ips: string[] }; urls: string[]; firewall_command: string;
  certificate: { names: string[]; valid_until: string; fingerprint_sha256: string } | null; can_restart: boolean; demo: boolean; autostart: boolean | null;
};

/** Settings > Network access (administrators): let staff use TenderPilot from their own computers. */
export default function NetworkAccess() {
  const n = useLoad<Net>(() => api("/network"));
  const [f, setF] = useState({ network: false, port: "8471", https: true });
  const [restarting, setRestarting] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const act = useAction();
  useEffect(() => { if (n.data) setF({ network: n.data.config.network, port: String(n.data.config.port), https: n.data.config.https }); }, [n.data]);
  if (!n.data) return <Panel title="Network access">{n.error ? <ErrorBox error={n.error} onRetry={n.reload} /> : <Loading />}</Panel>;
  const d = n.data;
  if (d.demo) return <Panel title="Network access"><p className="text-sm text-muted">The demo always runs for this computer only.</p></Panel>;
  const save = () => act.run(async () => n.setData(await api("/network", { method: "PUT", json: { network: f.network, port: Number(f.port), https: f.https } })));
  const restart = () => act.run(async () => {
    const r = await api<{ message: string }>("/network/restart", { method: "POST" });
    setRestarting(r.message);
    const target = d.config.network ? `${d.config.https ? "https" : "http"}://${window.location.hostname}:${d.config.port}` : `http://127.0.0.1:${d.config.port}`;
    setTimeout(() => { window.location.href = target; }, 20000);
  });
  const copy = (text: string) => { navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => undefined); };
  return (
    <Panel title="Network access">
      <ErrorBox error={act.error} />
      <div className="space-y-4 text-sm">
        <p>Now: {d.active.network ? <strong className="text-pass">open to the office network</strong> : <strong>this computer only</strong>}
          {d.active.network && <> ({d.active.https ? "HTTPS" : "HTTP"}, port {d.active.port})</>}.</p>
        <div className="space-y-2 rounded-md border border-line p-3">
          <label className="flex items-center gap-2 font-medium"><input type="checkbox" checked={f.network} onChange={(e) => setF({ ...f, network: e.target.checked })} />
            Let other computers in the office use TenderPilot from their browsers</label>
          <div className="flex flex-wrap items-center gap-4 pl-6">
            <label>Port <input inputMode="numeric" className={`${inputCls} ml-1 w-24`} value={f.port} onChange={(e) => setF({ ...f, port: e.target.value.replace(/\D/g, "") })} /></label>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={f.https} onChange={(e) => setF({ ...f, https: e.target.checked })} /> Encrypted connection (HTTPS, recommended)</label>
          </div>
          {f.network && !f.https && <Notice tone="warn">Without HTTPS, passwords and tender documents cross the network unencrypted. Use this only on a network you fully control.</Notice>}
          <Button disabled={act.busy || !f.port} onClick={save}>Save</Button>
        </div>

        {d.restart_needed && !restarting && (
          <Notice tone="warn">The new setting applies after TenderPilot restarts.{" "}
            {d.can_restart ? <Button className="ml-2" disabled={act.busy} onClick={restart}>Restart TenderPilot now</Button>
              : "Close the TenderPilot window on the server and start it again."}</Notice>
        )}
        {restarting && <Notice>{restarting}</Notice>}

        {d.autostart !== null && (
          <label className="flex items-start gap-2 rounded-md border border-line p-3">
            <input type="checkbox" className="mt-1" checked={d.autostart} disabled={act.busy}
              onChange={(e) => { const on = e.target.checked; act.run(async () => { await api("/network/autostart", { method: "PUT", json: { on } }); n.reload(); }); }} />
            <span><span className="font-medium">Start TenderPilot by itself when this Windows account signs in</span><br />
              <span className="text-xs text-muted">Recommended on a server. After the server restarts, sign in to it once (for example with Remote Desktop) and TenderPilot starts without a browser.</span></span>
          </label>
        )}

        {d.config.network && (
          <div className="space-y-3">
            <div>
              <div className="font-medium">1. Give staff this address</div>
              <ul className="mt-1 space-y-0.5">{d.urls.map((u) => <li key={u}><code className="rounded bg-[#f3f2ee] px-1.5 py-0.5">{u}</code></li>)}</ul>
              <p className="mt-1 text-xs text-muted">The computer name works inside an office domain; otherwise use the number. Give the server a fixed IP address so it does not change.</p>
            </div>
            <div>
              <div className="font-medium">2. Allow it through Windows Firewall (once, by a server administrator)</div>
              <p className="text-xs text-muted">In PowerShell opened with "Run as administrator" on the server:</p>
              <div className="mt-1 flex items-start gap-2">
                <code className="block flex-1 rounded bg-[#f3f2ee] px-2 py-1.5 text-xs">{d.firewall_command}</code>
                <Button kind="secondary" onClick={() => copy(d.firewall_command)}>{copied ? "Copied" : "Copy"}</Button>
              </div>
            </div>
            <div>
              <div className="font-medium">3. Each person signs in with their own account</div>
              <p className="text-xs text-muted">Add people under People and roles below. Never share one account: the audit trail records who did what.</p>
            </div>
            {d.config.https && (
              <div>
                <div className="font-medium">4. The certificate warning</div>
                <p className="text-xs text-muted">TenderPilot made its own certificate for this server, so browsers warn the first time ("Your connection is not private").
                  To remove the warning, install the certificate on each staff computer: download it, double-click, Install Certificate, Local Machine,
                  "Place all certificates in the following store", Trusted Root Certification Authorities. Or choose Advanced, Continue in the browser.</p>
                {d.certificate && <p className="mt-1 text-xs text-muted">Covers: {d.certificate.names.join(", ")} · valid until {d.certificate.valid_until}<br />
                  Fingerprint (SHA-256): <span className="break-all">{d.certificate.fingerprint_sha256}</span></p>}
                <div className="mt-2 flex flex-wrap gap-2">
                  {d.certificate && <Button kind="secondary" onClick={() => act.run(() => download("/network/certificate", "TenderPilot-server.crt"))}>Download certificate</Button>}
                  <Button kind="ghost" disabled={act.busy} onClick={() => act.run(async () => { await api("/network/certificate/renew", { method: "POST" }); n.reload(); })}>Make a new certificate</Button>
                </div>
              </div>
            )}
            <Notice>Keep TenderPilot running on the server: leave its window open and disconnect from Remote Desktop instead of signing out.
              Tick "Start TenderPilot by itself" above so it comes back after a server restart.</Notice>
          </div>
        )}
      </div>
    </Panel>
  );
}
