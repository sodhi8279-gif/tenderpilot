import { useState } from "react";
import { api } from "../api";
import { Button, ErrorBox, Field, inputCls, Notice, Panel, useAction } from "../ui";

/** Every user: change your own password. */
export default function YourAccount() {
  const [f, setF] = useState({ current_password: "", new_password: "", again: "" });
  const [done, setDone] = useState(false);
  const act = useAction();
  const mismatch = f.again !== "" && f.again !== f.new_password;
  return (
    <Panel title="Your account">
      <ErrorBox error={act.error} />
      {done && <Notice>Password changed. Use the new password next time you sign in.</Notice>}
      <form className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); setDone(false);
        act.run(() => api("/me/password", { json: { current_password: f.current_password, new_password: f.new_password } }),
          () => { setF({ current_password: "", new_password: "", again: "" }); setDone(true); }); }}>
        <Field label="Current password"><input required type="password" autoComplete="current-password" className={inputCls} value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} /></Field>
        <Field label="New password" hint="At least 10 characters."><input required minLength={10} type="password" autoComplete="new-password" className={inputCls} value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} /></Field>
        <Field label="New password again" hint={mismatch ? "The two new passwords differ." : undefined}><input required type="password" autoComplete="new-password" className={inputCls} value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} /></Field>
        <div><Button disabled={act.busy || mismatch || !f.new_password}>Change password</Button></div>
      </form>
    </Panel>
  );
}
