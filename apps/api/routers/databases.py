"""GET /api/databases and GET /api/database/{db_name}."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.models import DatabaseDetail, DatabaseSummary

router = APIRouter(prefix="/api", tags=["databases"])


@router.get("/databases", response_model=list[DatabaseSummary])
def list_databases(request: Request) -> list[DatabaseSummary]:
    state = request.app.state.app_state
    out: list[DatabaseSummary] = []
    for db_name, db_data in state.databases.items():
        out.append(
            DatabaseSummary(
                name=db_name,
                decrypted=db_data.get("decrypted", False),
                message_count=len(db_data.get("messages", [])),
                tables=list(db_data.get("schema", {}).get("tables", [])),
            )
        )
    return out


# Collections too large to safely dump in a single unpaginated /api/database
# response. They have dedicated paginated routes (/api/messages, /api/logs,
# /api/storage, /api/forensics, /api/ghosts) so we strip them here rather than
# returning every row at once. `messages`/`peers`/`conversations`/`media_catalog`
# stay because they are declared DatabaseDetail fields the frontend type and
# tests rely on.
_HEAVY_KEYS = (
    "messages_fts",
    "storage_catalog",
    "log_events",
    "ghosts_history",
    "forensic_index",
)


@router.get("/database/{db_name}", response_model=DatabaseDetail)
def get_database(db_name: str, request: Request) -> DatabaseDetail:
    state = request.app.state.app_state
    db_data = state.databases.get(db_name)
    if db_data is None:
        raise HTTPException(status_code=404, detail="Database not found")
    trimmed = {k: v for k, v in db_data.items() if k not in _HEAVY_KEYS}
    return DatabaseDetail(**trimmed)
