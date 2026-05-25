"""Loads parsed_data JSON files into an AppState.

Pulled from the renamed Flask app (webui_flask.py) with one change: returns an
AppState instead of mutating module-level globals.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.state import AppState


def load_parsed_data(data_dir: Path, account: str | None = None) -> dict[str, Any]:
    databases: dict[str, Any] = {}

    all_dirs = sorted(data_dir.glob("account-*"))
    if account:
        wanted = account if account.startswith("account-") else f"account-{account}"
        all_dirs = [d for d in all_dirs if d.name == wanted]
        if not all_dirs:
            raise SystemExit(
                f"ERROR: --account {account!r} matched no directory in {data_dir}.\n"
                f"  Available: {', '.join(d.name for d in sorted(data_dir.glob('account-*'))) or '(none)'}"
            )

    for account_dir in all_dirs:
        account_id = account_dir.name

        def _read(name: str) -> Any:
            f = account_dir / name
            if f.exists():
                with open(f) as fh:
                    return json.load(fh)
            return []

        messages = _read("messages.json")
        peers = _read("peers.json")
        conversations = _read("conversations_index.json")
        messages_fts = _read("messages_fts.json")
        media_catalog = _read("media_catalog.json")
        storage_catalog = _read("storage_catalog.json")
        log_events = _read("log_events.json")

        databases[account_id] = {
            "decrypted": True,
            "messages": messages,
            "messages_fts": messages_fts,
            "peers": peers,
            "conversations": conversations,
            "media_catalog": media_catalog,
            "storage_catalog": storage_catalog,
            "log_events": log_events,
            "schema": {"tables": ["t2 (peers)", "t7 (messages)"]},
        }

        tombstones = sum(1 for e in storage_catalog if not e.get("on_disk"))
        print(
            f"  {account_id}: {len(messages)} messages, {len(peers)} peers, "
            f"{len(conversations)} conversations, {len(messages_fts)} fts, "
            f"{len(media_catalog)} media, "
            f"{len(storage_catalog)} storage ({tombstones} tombstones), "
            f"{len(log_events)} log events"
        )

    return {"databases": databases}


def load_telegram_data(data_dir: str | Path, account: str | None = None) -> AppState:
    state = AppState()
    state.export_dir = Path(data_dir)

    nested = state.export_dir / "parsed_data"
    if nested.is_dir() and (
        (nested / "summary.json").exists()
        or any(nested.glob("account-*/messages.json"))
    ):
        print(f"Auto-detected parsed_data subdirectory: {nested}")
        state.export_dir = nested

    state.backup_dir = state.export_dir.parent
    summary_file = state.export_dir / "summary.json"
    if summary_file.exists():
        try:
            with open(summary_file) as f:
                summary = json.load(f)
            if "backup_dir" in summary:
                state.backup_dir = Path(summary["backup_dir"])
        except Exception:
            pass

    has_account_dirs = any(state.export_dir.glob("account-*"))

    if summary_file.exists() or has_account_dirs:
        print("Detected parsed_data format (postbox_parser.py)")
        state.telegram_data = load_parsed_data(state.export_dir, account=account)
    else:
        master_file = state.export_dir / "telegram_export.json"
        if master_file.exists():
            with open(master_file) as f:
                state.telegram_data = json.load(f)
        else:
            state.telegram_data = {"databases": {}}
            for export_file in state.export_dir.glob("*_export.json"):
                db_name = export_file.stem.replace("_export", "")
                with open(export_file) as f:
                    state.telegram_data["databases"][db_name] = json.load(f)

    db_count = len(state.databases)
    msg_count = sum(len(db.get("messages", [])) for db in state.databases.values())
    print(f"Loaded {db_count} databases with {msg_count} total messages")
    if db_count > 0 and msg_count == 0:
        print()
        print("WARNING: account-* directories were found but contain no messages.json.")
        print(f"  This usually means '{state.export_dir}' is a raw backup root, not parsed_data.")
        print(f"  Try: python3 postbox_parser.py '{state.export_dir}'  (then re-run this command)")

    return state
