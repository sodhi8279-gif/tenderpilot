import { useEffect, useRef, useState } from "react";
import { api, blobUrl } from "../../api";
import { Button, ErrorBox, inputCls, Loading, useAction, useLoad } from "../../ui";

type Box = { name: string; page: number; x: number; y: number; width: number; font_size: number };
type Info = { kind: string; pages: { width: number; height: number }[]; layout: Box[] };

/** Place text boxes on a flat or scanned PDF form: click where a value should be printed. */
export default function PdfBoxes({ tid, templateId, fields, onSaved }: { tid: number; templateId: number; fields: Record<string, string>; onSaved: () => void }) {
  const info = useLoad<Info>(() => api(`/tenders/${tid}/templates/${templateId}/pdf`), [templateId]);
  const [page, setPage] = useState(1);
  const [img, setImg] = useState<string | null>(null);
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [nextName, setNextName] = useState("legal_name");
  const imgRef = useRef<HTMLImageElement>(null);
  const act = useAction();
  useEffect(() => { if (info.data) setBoxes(info.data.layout); }, [info.data]);
  useEffect(() => {
    let url: string | null = null;
    setImg(null);
    blobUrl(`/tenders/${tid}/templates/${templateId}/pdf/pages/${page - 1}`).then((u) => { url = u; setImg(u); }).catch((e) => act.setError(e.message));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [tid, templateId, page]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!info.data) return <Loading />;
  const place = (e: React.MouseEvent<HTMLImageElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.min(0.95, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(0.97, Math.max(0, (e.clientY - r.top) / r.height - 0.012));
    let name = nextName.trim() || `field_${boxes.length + 1}`;
    if (boxes.some((b) => b.name === name)) name = `${name}_${boxes.length + 1}`;
    setBoxes([...boxes, { name, page, x: +x.toFixed(4), y: +y.toFixed(4), width: +Math.min(0.4, 1 - x).toFixed(4), font_size: 10 }]);
  };
  const upd = (i: number, p: Partial<Box>) => setBoxes(boxes.map((b, j) => (j === i ? { ...b, ...p } : b)));
  const suggestions = Object.keys(fields).filter((k) => !k.startsWith("quote.")).map((k) => k.split(".").slice(1).join("."));

  return (
    <div className="mt-4 rounded-md border border-line p-3">
      <ErrorBox error={act.error} />
      <div className="mb-2 text-sm font-medium">Place text boxes: click on the form where each value should be printed</div>
      <div className="mb-2 flex flex-wrap items-end gap-3 text-sm">
        <label>Next box name
          <input list="tp-box-names" className={`${inputCls} ml-2 w-56`} value={nextName} onChange={(e) => setNextName(e.target.value)} />
          <datalist id="tp-box-names">{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
        {info.data.pages.length > 1 && <label>Page
          <select className={`${inputCls} ml-2`} value={page} onChange={(e) => setPage(Number(e.target.value))}>
            {info.data.pages.map((_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}</select></label>}
        <span className="text-xs text-muted">Names like legal_name or tender_ref are matched to TenderPilot fields automatically; confirm them in the mapping after saving.</span>
      </div>
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="relative inline-block self-start rounded border border-line bg-white">
          {img ? <img ref={imgRef} src={img} alt={`Page ${page} of the form. Click to place a box.`} className="block h-auto w-full cursor-crosshair" onClick={place} /> : <Loading />}
          {img && boxes.filter((b) => b.page === page).map((b) => (
            <div key={b.name} className="pointer-events-none absolute border border-teal bg-teal-soft/60 px-1 text-[10px] text-[#0b4f4c]"
              style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.width * 100}%`, minHeight: `${b.font_size * 0.18}%` }}>{b.name}</div>
          ))}
        </div>
        <div className="text-sm">
          {!boxes.length ? <p className="text-muted">No boxes yet.</p> : (
            <table className="datagrid"><thead><tr><th>Name</th><th>Pg</th><th>Width %</th><th>Size</th><th></th></tr></thead>
              <tbody>{boxes.map((b, i) => (
                <tr key={i}>
                  <td><input aria-label="Box name" className={`${inputCls} w-32`} value={b.name} onChange={(e) => upd(i, { name: e.target.value })} /></td>
                  <td>{b.page}</td>
                  <td><input aria-label="Box width" type="number" min={2} max={100} className={`${inputCls} w-16`} value={Math.round(b.width * 100)}
                    onChange={(e) => upd(i, { width: Math.min(1 - b.x, Math.max(0.02, Number(e.target.value) / 100)) })} /></td>
                  <td><input aria-label="Font size" type="number" min={5} max={24} className={`${inputCls} w-14`} value={b.font_size}
                    onChange={(e) => upd(i, { font_size: Number(e.target.value) || 10 })} /></td>
                  <td><Button kind="ghost" onClick={() => setBoxes(boxes.filter((_, j) => j !== i))}>Remove</Button></td>
                </tr>))}</tbody></table>
          )}
          <Button className="mt-3" disabled={act.busy} onClick={() => act.run(() => api(`/tenders/${tid}/templates/${templateId}/layout`, { method: "PUT", json: boxes }), onSaved)}>
            Save boxes</Button>
          <p className="mt-1 text-xs text-muted">Values are printed in dark blue on a copy of the form; the uploaded form is never changed. Missing values print as «MISSING».</p>
        </div>
      </div>
    </div>
  );
}
