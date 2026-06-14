"""GET /api/logs — paginated/filterable forensic events from Telegram MTProto logs.

These records come from `tool.log_parser`'s scan of `<backup>/logs/log-*.txt`.
Each row is metadata only — the bytes payload was already truncated on disk
(see `log_parser.py` docstring) so the value here is in seeing what envelope
reached the client even when the underlying message has since been deleted
from t7.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from api.models import LogEvent, LogEventPage

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs", response_model=LogEventPage)
def list_log_events(
    request: Request,
    event_type: str = Query("", description="Filter to one event type (e.g. encrypted_message)"),
    ghost_only: bool = Query(False, description="Only encrypted_message events with no matching t7 row"),
    peer_id: str = Query("", description="Filter encrypted_message / pending_removed by peer_id (comma-separated)"),
    account: str = Query("", description="Restrict to one account-{id}"),
    search: str = Query("", description="Substring match against the event's source_file + data dict"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=1000),
) -> LogEventPage:
    state = request.app.state.app_state
    needle = search.lower()

    peer_id_set: set[str] = set(peer_id.split(",")) if peer_id else set()

    items: list[dict[str, Any]] = []

    for db_name, db_data in state.databases.items():
        if account and db_name != account:
            continue
        for ev in db_data.get("log_events", []):
            if event_type and ev.get("event") != event_type:
                continue
            if ghost_only:
                # Ghost = encrypted_message that doesn't pair with t7.
                if ev.get("event") != "encrypted_message":
                    continue
                if ev.get("in_db") is not False:
                    continue
            if peer_id_set:
                pid = ev.get("data", {}).get("peer_id")
                if pid is None or str(pid) not in peer_id_set:
                    continue
            if needle:
                hay = (
                    ev.get("source_file", "")
                    + " "
                    + str(ev.get("data", {}))
                ).lower()
                if needle not in hay:
                    continue
            items.append({**ev, "account": db_name})

    # Newest event first when log_timestamp comparable; fall back to insertion.
    items.sort(key=lambda e: e.get("log_timestamp", ""), reverse=True)

    counts: dict[str, int] = {"all": 0, "ghost": 0}
    for db_name, db_data in state.databases.items():
        if account and db_name != account:
            continue
        for ev in db_data.get("log_events", []):
            counts["all"] += 1
            t = ev.get("event", "other")
            counts[t] = counts.get(t, 0) + 1
            if ev.get("event") == "encrypted_message" and ev.get("in_db") is False:
                counts["ghost"] += 1

    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page

    return LogEventPage(
        events=[LogEvent(**i) for i in items[start:end]],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page if per_page else 1,
        counts=counts,
    )
