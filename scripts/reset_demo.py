"""Reset the demo to its starting state: an empty claims database and no uploaded files.

    .venv/bin/python scripts/reset_demo.py [--yes]

Only two things are removed, and only if they sit inside this project folder:
  - the demo database file (CLAIMPILOT_DB, default claimpilot.db), and
  - the uploads folder (CLAIMPILOT_UPLOADS, default data/uploads).
Fixtures, policies, trained models, imports and reports are never touched. Stop the API first:
the database is recreated empty on the next start. The audit log is append-only by design, so a
reset starts a new file rather than editing the old one; pass --keep to rename it instead of deleting.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def inside_project(path: Path) -> bool:
    return path.resolve().is_relative_to(ROOT) and path.resolve() != ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    parser.add_argument("--keep", action="store_true", help="rename the old database instead of deleting it")
    args = parser.parse_args()

    db = ROOT / os.environ.get("CLAIMPILOT_DB", "claimpilot.db")
    uploads = ROOT / os.environ.get("CLAIMPILOT_UPLOADS", "data/uploads")
    for path in (db, uploads):
        if not inside_project(path):
            print(f"Refusing to touch {path}: it is outside {ROOT}")
            return 1
    if db.suffix != ".db":
        print(f"Refusing to delete {db}: a demo database ends in .db")
        return 1
    if not args.yes and input(f"Reset the demo? This removes {db.name} and {uploads.relative_to(ROOT)}/ [y/N] ").strip().lower() != "y":
        print("Nothing changed")
        return 0

    for extra in ("", "-wal", "-shm", "-journal"):
        path = db.with_name(db.name + extra)
        if path.exists():
            if args.keep and not extra:
                path.rename(path.with_name(f"{db.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"))
            else:
                path.unlink()
    if uploads.exists():
        shutil.rmtree(uploads)
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from backend.db.db import init_db

    init_db(db).close()
    print(f"Demo reset: {db.name} is empty and ready; uploads cleared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
