"""Check that updating TenderPilot leaves an existing database's data unchanged.

Usage (from the backend folder):
    python verify_upgrade.py "C:\\Users\\<you>\\AppData\\Local\\TenderPilot\\data\\tenderpilot.db"

The database you name is only READ. The script copies it to a temporary folder, applies every pending
update (migration) to the copy, and then compares every row of every table that existed before:
each original column must hold exactly the same value. New tables and new columns are listed.
Exit code 0 = all existing data identical; 1 = a difference was found (details are printed).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def snapshot(db: Path) -> dict[str, tuple[list[str], list[tuple]]]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = {}
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            rows = con.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}" ORDER BY rowid').fetchall()
            out[t] = (cols, rows)
        return out
    finally:
        con.close()


def main(src: str) -> int:
    src_path = Path(src)
    if not src_path.exists():
        print(f"Not found: {src_path}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="tp-verify-"))
    copy = work / "tenderpilot.db"
    # sqlite backup API: consistent copy including the -wal file; the original is opened read-only
    s = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(copy))
    s.backup(d)
    d.close()
    s.close()
    before = snapshot(copy)

    os.environ["DATABASE_URL"] = f"sqlite:///{copy.as_posix()}"
    os.environ["DATA_DIR"] = str(work)
    os.environ["STORAGE_DIR"] = str(work / "storage")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app.migrations import migrate  # noqa: E402  (must follow the environment set-up)

    applied = migrate()
    after = snapshot(copy)

    problems = []
    for table, (cols, rows) in before.items():
        if table not in after:
            problems.append(f"table {table} is missing after the update")
            continue
        new_cols, new_rows = after[table]
        missing = [c for c in cols if c not in new_cols]
        if missing:
            problems.append(f"{table}: columns removed {missing}")
            continue
        idx = [new_cols.index(c) for c in cols]
        projected = [tuple(r[i] for i in idx) for r in new_rows]
        if table == "schema_version":
            if not set(rows) <= set(projected):
                problems.append("schema_version lost entries")
        elif projected != rows:
            changed = sum(1 for a, b in zip(rows, projected) if a != b) + abs(len(rows) - len(projected))
            problems.append(f"{table}: {changed} row(s) differ")

    total = sum(len(r) for t, (_, r) in before.items())
    print(f"Updates applied to the copy: {', '.join(applied) or 'none (already up to date)'}")
    print(f"Existing tables checked: {len(before)}, rows checked: {total}")
    new_tables = sorted(set(after) - set(before))
    if new_tables:
        print(f"New tables added: {', '.join(new_tables)}")
    for t in sorted(before):
        added = [c for c in after.get(t, ([], []))[0] if c not in before[t][0]]
        if added:
            print(f"New columns in {t}: {', '.join(added)}")
    if problems:
        print("PROBLEMS FOUND - do not update this database:")
        for p in problems:
            print("  -", p)
        return 1
    print("OK: every existing row and value is unchanged.")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
