import { createContext, useContext, useEffect, useState } from "react";
import { lang, setLang, t } from "./i18n";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, setToken } from "./api";
import { Button, ErrorBox, Field, inputCls, Loading } from "./ui";
import Portfolio from "./pages/Portfolio";
import NewTender from "./pages/NewTender";
import TenderPage from "./pages/tender/TenderPage";
import Vault from "./pages/Vault";
import FirmPage from "./pages/FirmPage";
import Settings from "./pages/Settings";

export type Me = { user: { id: number; name: string; email: string }; org: { id: number; name: string; allow_external_ai: boolean };
  roles: string[]; permissions: string[]; unread_notifications: number };

const MeCtx = createContext<{ me: Me; can: (p: string) => boolean; refresh: () => void } | null>(null);
export const useMe = () => useContext(MeCtx)!;

function Login() {
  const nav = useNavigate();
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [demo, setDemo] = useState<{ email: string; password: string } | null>(null);
  const [f, setF] = useState({ email: "", password: "", name: "", org_name: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/setup/status").then((r) => { setNeedsSetup(r.needs_setup); setDemo(r.demo || null); }).catch(() => setNeedsSetup(false)); }, []);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const r = needsSetup ? await api("/setup", { json: f }) : await api("/auth/login", { json: { email: f.email, password: f.password } });
      setToken(r.token); nav("/");
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  if (needsSetup === null) return <Loading />;
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="flex min-h-full items-center justify-center bg-ink p-4">
      <button type="button" className="fixed right-4 top-3 text-sm text-white underline" onClick={() => setLang(lang === "hi" ? "en" : "hi")}>
        {lang === "hi" ? "English" : "हिन्दी"}</button>
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg bg-panel p-6 shadow-sm">
        <h1 className="text-xl font-semibold">TenderPilot</h1>
        <p className="mb-5 text-sm text-muted">{t(needsSetup ? "Create your organization and the first administrator account." : "Sign in to your bid workspace.")}</p>
        {demo && !needsSetup && (
          <div className="mb-4 rounded-md border border-line bg-teal-soft p-3 text-sm">
            <div className="font-medium">{t("Demo version with made-up (SYNTHETIC) data")}</div>
            <div className="mt-1 text-muted">Email: {demo.email}<br />Password: {demo.password}</div>
            <button type="button" className="mt-2 text-teal underline" onClick={() => setF({ ...f, email: demo.email, password: demo.password })}>{t("Fill in for me")}</button>
          </div>
        )}
        <ErrorBox error={err} />
        <div className="space-y-3">
          {needsSetup && <Field label="Organization name"><input required className={inputCls} value={f.org_name} onChange={set("org_name")} /></Field>}
          {needsSetup && <Field label="Your name"><input required className={inputCls} value={f.name} onChange={set("name")} /></Field>}
          <Field label="Email"><input required type="email" autoComplete="username" className={inputCls} value={f.email} onChange={set("email")} /></Field>
          <Field label="Password" hint={needsSetup ? t("At least 10 characters.") : undefined}>
            <input required type="password" minLength={needsSetup ? 10 : 1} autoComplete={needsSetup ? "new-password" : "current-password"} className={inputCls} value={f.password} onChange={set("password")} />
          </Field>
        </div>
        <Button className="mt-5 w-full justify-center" disabled={busy}>{t(needsSetup ? "Create organization" : "Sign in")}</Button>
      </form>
    </div>
  );
}

const NAV: [string, string, string][] = [
  ["/", "Tenders", "M4 5h16M4 12h16M4 19h10"],
  ["/vault", "Client firms", "M5 4h14v16H5zM9 8h6M9 12h6"],
  ["/settings", "Settings & audit", "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v3M12 19v3M2 12h3M19 12h3"],
];

function Shell() {
  const [me, setMe] = useState<Me | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api<Me>("/me").then(setMe).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const [newVersion, setNewVersion] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => { fetch("/api/health").then((r) => r.json()).then((j) => setVersion(j.version || null)).catch(() => undefined); }, []);
  useEffect(() => {  // administrators: quiet check (the server asks GitHub at most every 12 hours)
    if (!me?.permissions.includes("org.admin")) return;
    api("/update/check?force=false", { method: "POST" })
      .then((r) => setNewVersion(r.update_available ? r.latest?.version : null)).catch(() => undefined);
  }, [me?.user.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="p-6"><ErrorBox error={err} onRetry={load} /></div>;
  if (!me) return <Loading />;
  const can = (p: string) => me.permissions.includes(p);
  return (
    <MeCtx.Provider value={{ me, can, refresh: load }}>
      <div className="flex min-h-full flex-col md:flex-row">
        <nav aria-label="Main" className="flex shrink-0 items-center gap-1 bg-ink px-3 py-2 text-[#c9d5e4] md:w-56 md:flex-col md:items-stretch md:px-3 md:py-5">
          <div className="mr-3 px-2 text-white md:mb-6 md:mr-0">
            <div className="text-[1.05rem] font-semibold tracking-tight">TenderPilot</div>
            <div className="hidden text-xs text-[#8fa3bd] md:block">{me.org.name}</div>
          </div>
          {NAV.map(([to, label, d]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm ${isActive ? "bg-ink-2 text-white" : "hover:bg-ink-2/60 hover:text-white"}`}>
              <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d={d} /></svg>
              <span className="hidden sm:inline">{t(label)}</span>
            </NavLink>
          ))}
          {newVersion && (
            <NavLink to="/settings" className="mx-1 mt-2 rounded-md bg-teal px-2.5 py-1.5 text-xs font-medium text-white hover:bg-[#0b605d] md:mx-0">
              {t("Update available")}: {newVersion}
            </NavLink>
          )}
          <div className="ml-auto text-xs md:mt-auto md:ml-0 md:px-2.5">
            <div className="hidden text-white md:block">{me.user.name}</div>
            <div className="hidden text-[#8fa3bd] md:block">{me.roles.map((r) => r.replace(/_/g, " ")).join(", ")}</div>
            <button className="mt-1 underline hover:text-white" onClick={() => { setToken(null); window.location.assign("/login"); }}>{t("Sign out")}</button>
            <button className="mt-1 ml-3 block underline hover:text-white md:ml-0" lang={lang === "hi" ? "en" : "hi"}
              onClick={() => setLang(lang === "hi" ? "en" : "hi")}>{lang === "hi" ? "English" : "हिन्दी"}</button>
            <div className="mt-3 hidden border-t border-ink-2 pt-2 text-[#8fa3bd] md:block">
              TenderPilot {version && `v${version}`}
              {can("org.admin") && <NavLink to="/settings#software-update" className="ml-2 underline hover:text-white">{t("Updates")}</NavLink>}
            </div>
          </div>
        </nav>
        <main className="min-w-0 flex-1 px-4 py-5 md:px-8 md:py-7">
          <Routes>
            <Route path="/" element={<Portfolio />} />
            <Route path="/tenders/new" element={<NewTender />} />
            <Route path="/tenders/:id/*" element={<TenderPage />} />
            <Route path="/vault" element={<Vault />} />
            <Route path="/firms/:id" element={<FirmPage />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </MeCtx.Provider>
  );
}

// Evaluated on every navigation (not once at startup), so signing in takes effect immediately.
function RequireAuth() {
  return getToken() ? <Shell /> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<RequireAuth />} />
      </Routes>
    </BrowserRouter>
  );
}
