"""GET /api/stats — overview counts."""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.models import Stats, StatsDb

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats", response_model=Stats)
def get_stats(request: Request) -> Stats:
    state = request.app.state.app_state

    total_databases = 0
    decrypted_databases = 0
    total_messages = 0
    databases: dict[str, StatsDb] = {}

    for db_name, db_data in state.databases.items():
        total_databases += 1
        if db_data.get("decrypted"):
            decrypted_databases += 1
        msg_count = len(db_data.get("messages", []))
        total_messages += msg_count
        databases[db_name] = StatsDb(
            decrypted=db_data.get("decrypted", False),
            message_count=msg_count,
            tables=len(db_data.get("schema", {}).get("tables", [])),
        )

    total_chats = state.chat_count()

    return Stats(
        total_databases=total_databases,
        decrypted_databases=decrypted_databases,
        total_messages=total_messages,
        total_chats=total_chats,
        databases=databases,
    )
