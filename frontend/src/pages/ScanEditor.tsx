import { useEffect, useRef, useState } from "react";
import { api, download, postBlob } from "../api";
import { useMe } from "../App";
import { Button, ErrorBox, fmtDate, inputCls, Loading, Notice, Panel, useAction, useLoad } from "../ui";

type Cleaned = { id: number; page_index: number; params: { rotate: number; deskew_degrees: number; crop?: number[]; page: number }; sha256: string; created_at: string };
type Info = { kind: string; pages: number; cleaned: Cleaned[]; note: string };

const pct = (v: number) => Math.round(v * 100);

/** Rotate, crop and straighten a vault scan. Saves a cleaned copy; the original record is never changed. */
export default function ScanEditor({ evidenceId, title, onClose }: { evidenceId: number; title: string; onClose: () => void }) {
  const { can } = useMe();
  const info = useLoad<Info>(() => api(`/evidence/${evidenceId}/scan`), [evidenceId]);
  const [page, setPage] = useState(0);
  const [rotate, setRotate] = useState(0);
  const [deskew, setDeskew] = useState(true);
  const [crop, setCrop] = useState({ left: 0, top: 0, right: 100, bottom: 100 });
  const [img, setImg] = useState<string | null>(null);
  const [angle, setAngle] = useState<string | null>(null);
  const act = useAction();
  const top = useRef<HTMLDivElement>(null);
  useEffect(() => { top.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, []); // opens below a long list
  const body = () => {
    const c = [crop.left / 100, crop.top / 100, crop.right / 100, crop.bottom / 100];
    const full = c[0] === 0 && c[1] === 0 && c[2] === 1 && c[3] === 1;
    return { page, rotate, deskew, crop: full ? null : c };
  };
  const preview = () => act.run(async () => {
    const r = await postBlob(`/evidence/${evidenceId}/scan/preview`, body());
    setImg((old) => { if (old) URL.revokeObjectURL(old); return r.url; });
    setAngle(r.headers.get("X-Deskew-Degrees"));
  });
  useEffect(() => { if (info.data) preview(); }, [info.data, page, rotate, deskew]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (img) URL.revokeObjectURL(img); }, [img]);

  const cropInput = (k: keyof typeof crop, label: string) => (
    <label className="block text-xs">{label} (%)
      <input type="number" min={0} max={100} className={`${inputCls} mt-0.5 w-20`} value={crop[k]}
        onChange={(e) => setCrop({ ...crop, [k]: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} />
    </label>
  );

  return (
    <div ref={top} className="scroll-mt-4">
    <Panel title={`Clean up scan: ${title}`} actions={<Button kind="ghost" onClick={onClose}>Close</Button>}>
      <ErrorBox error={info.error || act.error} />
      {!info.data ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="rounded-lg border border-line bg-[#e9e8e3] p-2">
            {img ? <img src={img} alt="Preview of the cleaned page" className="mx-auto h-auto max-h-[70vh] bg-white shadow-sm" /> : <Loading />}
          </div>
          <div className="space-y-3 text-sm">
            <Notice>{info.data.note}</Notice>
            {info.data.pages > 1 && (
              <label className="block">Page
                <select className={`${inputCls} ml-2`} value={page} onChange={(e) => setPage(Number(e.target.value))}>
                  {Array.from({ length: info.data.pages }, (_, i) => <option key={i} value={i}>{i + 1}</option>)}
                </select>
              </label>
            )}
            <div className="flex items-center gap-2">Rotate
              <Button kind="secondary" aria-label="Rotate left" onClick={() => setRotate((rotate + 270) % 360)}>↺ Left</Button>
              <Button kind="secondary" aria-label="Rotate right" onClick={() => setRotate((rotate + 90) % 360)}>Right ↻</Button>
              <span className="text-xs text-muted">{rotate}°</span>
            </div>
            <label className="flex items-center gap-2"><input type="checkbox" checked={deskew} onChange={(e) => setDeskew(e.target.checked)} />
              Straighten tilted text automatically</label>
            {deskew && angle !== null && <div className="text-xs text-muted">{Number(angle) === 0 ? "No tilt found." : `Straightened by ${angle}°.`}</div>}
            <div>
              <div className="mb-1 font-medium">Crop (keep this area)</div>
              <div className="flex flex-wrap gap-2">{cropInput("left", "Left")}{cropInput("top", "Top")}{cropInput("right", "Right")}{cropInput("bottom", "Bottom")}</div>
              <Button className="mt-2" kind="secondary" disabled={act.busy} onClick={preview}>Preview crop</Button>
            </div>
            {can("vault.write") && (
              <Button disabled={act.busy} onClick={() => act.run(() => api(`/evidence/${evidenceId}/scan/clean`, { json: body() }), info.reload)}>
                Save cleaned copy (PDF)
              </Button>
            )}
            {info.data.cleaned.length > 0 && (
              <div>
                <div className="mb-1 font-medium">Cleaned copies</div>
                <ul className="space-y-1">
                  {info.data.cleaned.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-line px-2 py-1">
                      <span className="text-xs">Page {c.params.page}, rotate {c.params.rotate}°, straightened {c.params.deskew_degrees}°
                        {c.params.crop && `, cropped ${pct(c.params.crop[0])}-${pct(c.params.crop[2])}% × ${pct(c.params.crop[1])}-${pct(c.params.crop[3])}%`}
                        <span className="block text-muted">{fmtDate(c.created_at)}</span></span>
                      <Button kind="ghost" onClick={() => act.run(() => download(`/evidence/cleaned/${c.id}/file`, `cleaned-${evidenceId}-${c.id}.pdf`))}>Download</Button>
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-muted">To use a cleaned copy as the record's file, download it and choose Replace: it becomes a new version that a reviewer verifies again.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </Panel>
    </div>
  );
}
