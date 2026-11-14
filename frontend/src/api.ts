// Thin API client. Token lives in sessionStorage (cleared when the browser tab closes).
const KEY = "tp_token";

export function getToken(): string | null {
  try { return sessionStorage.getItem(KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try { if (t) sessionStorage.setItem(KEY, t); else sessionStorage.removeItem(KEY); } catch { /* storage blocked */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function detail(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ");
  }
  return "Request failed";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function api<T = any>(path: string, opts: { method?: string; json?: unknown; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  let body: BodyInit | undefined;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.form) body = opts.form;
  const res = await fetch(`/api/v1${path}`, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (res.status === 401 && t) { setToken(null); window.location.assign("/login"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, detail(data));
  return data as T;
}

/** Download an authenticated file and hand it to the browser. */
export async function download(path: string, filename: string) {
  const res = await fetch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new ApiError(res.status, detail(await res.json().catch(() => ({}))));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** Authenticated blob URL for <img>/<iframe> previews. */
export async function blobUrl(path: string): Promise<string> {
  const res = await fetch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new ApiError(res.status, "Preview unavailable");
  return URL.createObjectURL(await res.blob());
}

/** Indian grouping (12,34,56,789.00) with ₹. Works on decimal strings without float conversion. */
export const inr = (v: string | number | null | undefined, places = 2) => {
  if (v === null || v === undefined || v === "") return "—";
  const s = String(v);
  const neg = s.startsWith("-");
  const [w, f = ""] = (neg ? s.slice(1) : s).split(".");
  const last3 = w.slice(-3), rest = w.slice(0, -3);
  const whole = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + last3 : last3;
  const frac = places ? "." + (f + "00").slice(0, places) : "";
  return `${neg ? "-" : ""}₹${whole}${frac}`;
};

/** POST JSON and receive a binary (e.g. an image preview) as a blob URL, with the response headers. */
export async function postBlob(path: string, json: unknown): Promise<{ url: string; headers: Headers }> {
  const res = await fetch(`/api/v1${path}`, { method: "POST", headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" },
    body: JSON.stringify(json) });
  if (!res.ok) throw new ApiError(res.status, detail(await res.json().catch(() => ({}))));
  return { url: URL.createObjectURL(await res.blob()), headers: res.headers };
}
