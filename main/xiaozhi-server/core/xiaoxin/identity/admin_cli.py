from __future__ import annotations

import argparse
import json
from pathlib import Path

from .store import XiaoxinIdentityStore


def promote_admin(db_path: str | Path, username: str) -> dict[str, str]:
    clean_username = str(username or "").strip()
    if not clean_username:
        raise ValueError("username must not be empty")
    store = XiaoxinIdentityStore(db_path)
    before, after = store.set_user_role(clean_username, "admin")
    return {"username": clean_username, "before": before, "after": after}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote an existing Xiaoxin identity account to administrator."
    )
    parser.add_argument("--db", required=True, help="Path to the Xiaoxin identity SQLite database")
    parser.add_argument("--username", required=True, help="Existing local account username")
    args = parser.parse_args(argv)
    try:
        result = promote_admin(args.db, args.username)
    except (LookupError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
