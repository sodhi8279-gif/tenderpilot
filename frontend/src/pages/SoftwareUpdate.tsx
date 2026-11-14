import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, ErrorBox, fmtDate, Loading, Notice, Panel, useAction, useLoad } from "../ui";

export type UpdateStatus = {
  current_version: string; phase: "idle" | "downloading" | "ready" | "installing" | "error"; error: string | null;
  checked_at: number | null; update_available: boolean; can_install: boolean; cannot_install_reason: string | null;
  downloaded: number; total: number; backup: string | null;
  latest: { version: string; name: string; notes: string; published_at: string | null; page: string | null; installer_url: string | null } | null;
};

const mb = (n: number) => `${(n / 1048576).toFixed(0)} MB`;

/** Settings > Software update (administrators). Check, download (verified), back up and install. */
export default function SoftwareUpdate() {
  const st = useLoad<UpdateStatus>(() => api("/update"));
  const act = useAction();
  const [confirm, setConfirm] = useState(false);
  const [restarting, setRestarting] = useState<string | null>(null);
  const s = st.data;

  // first visit: check (at most every 12 hours on the server)
  useEffect(() => { act.run(async () => st.setData(await api("/update/check?force=false", { method: "POST" }))); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // follow the download
  useEffect(() => {
    if (s?.phase !== "downloading") return;
    const h = setInterval(st.reload, 1000);
    return () => clearInterval(h);
  }, [s?.phase]); // eslint-disable-line react-hooks/exhaustive-deps
  // after "install": wait for the new version to answer, then reload the page
  useEffect(() => {
    if (!restarting) return;
    const h = setInterval(async () => {
      try {
        const r = await fetch("/api/health", { cache: "no-store" });
        const j = await r.json();
        if (j.version && j.version !== s?.current_version) window.location.reload();
      } catch { /* TenderPilot is restarting */ }
    }, 3000);
    return () => clearInterval(h);
  }, [restarting]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {  // opened from the sidebar "Updates" link: bring the panel into view
    if (window.location.hash === "#software-update") document.getElementById("software-update")?.scrollIntoView({ block: "start" });
  }, [!!s]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!s) return <Panel title="Software update">{st.error ? <ErrorBox error={st.error} onRetry={st.reload} /> : <Loading />}</Panel>;
  const nothingPublished = !s.latest && !!s.error && s.error.startsWith("No version has been published");
  const check = () => act.run(async () => st.setData(await api("/update/check", { method: "POST" })));
  const download = () => act.run(async () => st.setData(await api("/update/download", { method: "POST" })));
  const install = () => act.run(async () => {
    const r = await api<UpdateStatus & { message: string }>("/update/install", { json: { confirm: true } });
    st.setData(r); setRestarting(r.message);
  });

  return (
    <div id="software-update" className="scroll-mt-4">
    <Panel title="Software update" actions={<Button kind="secondary" disabled={act.busy || s.phase === "downloading" || !!restarting} onClick={check}>Check for updates</Button>}>
      <ErrorBox error={act.error || (nothingPublished ? null : s.error)} />
      <div className="space-y-3 text-sm">
        <div>Installed version: <strong>{s.current_version}</strong>
          {s.checked_at && <span className="text-muted"> · last checked {fmtDate(new Date(s.checked_at * 1000).toISOString())}</span>}</div>

        {restarting ? (
          <Notice>{restarting} This page reloads when the new version is ready. Your data was backed up to: {s.backup}</Notice>
        ) : nothingPublished ? (
          <p className="text-pass">You have the latest version. No newer version has been published yet.</p>
        ) : !s.latest ? (
          <p className="text-muted">{s.checked_at ? "Could not check for updates. Try again." : "Not checked yet."}</p>
        ) : !s.update_available ? (
          <p className="text-pass">TenderPilot is up to date.</p>
        ) : (
          <div className="rounded-md border border-teal p-3">
            <div className="font-semibold">Version {s.latest.version} is available{s.latest.published_at && <span className="font-normal text-muted"> (released {fmtDate(s.latest.published_at)})</span>}</div>
            {s.latest.notes && <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap font-sans text-sm leading-relaxed">{s.latest.notes}</pre>}

            {!s.can_install ? (
              <p className="mt-3 text-gap">{s.cannot_install_reason}{s.latest.page && <> Or download the installer from the <a className="underline" href={s.latest.page} target="_blank" rel="noreferrer">release page</a>.</>}</p>
            ) : s.phase === "downloading" ? (
              <div className="mt-3" role="status">
                Downloading{s.total ? ` ${mb(s.downloaded)} of ${mb(s.total)}` : "…"}
                {s.total > 0 && <div className="mt-1 h-2 w-full max-w-md rounded bg-[#eeeff1]"><div className="h-2 rounded bg-teal" style={{ width: `${Math.round((s.downloaded / s.total) * 100)}%` }} /></div>}
              </div>
            ) : s.phase === "ready" ? (
              <div className="mt-3 space-y-2">
                <p className="text-pass">Downloaded and checked against its published checksum.</p>
                {!confirm ? <Button onClick={() => setConfirm(true)}>Back up my data and install</Button> : (
                  <div className="rounded bg-[#fbeedb] p-2 text-[#6b3e00]">
                    <p>TenderPilot will first save a full backup of your data (tenders, documents, company vault). It then closes, installs version {s.latest.version} and
                      starts again by itself in about a minute. Ask other users to save their work first.</p>
                    <div className="mt-2 flex gap-2">
                      <Button disabled={act.busy} onClick={install}>Yes, back up and install now</Button>
                      <Button kind="secondary" onClick={() => setConfirm(false)}>Not now</Button>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <Button className="mt-3" disabled={act.busy} onClick={download}>Download update</Button>
            )}
          </div>
        )}
        <p className="text-xs text-muted">Your data is kept in its own folder and is never replaced by an update. Before installing, a full backup zip is written to
          the backups folder, and the database is copied again before any change to it.</p>
      </div>
    </Panel>
    </div>
  );
}
