"""Pure compute_chats function shared by /api/chats and /api/stats."""
from __future__ import annotations

from typing import Any

from api.peer import peer_type
from api.state import AppState


def compute_chats(
    state: AppState,
    *,
    search: str = "",
    type_filter: str = "",
    user_id: str = "",
) -> list[dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}

    fts_peer_refs: set = set()
    bots: set[str] = set()
    for db_data in state.databases.values():
        for m in db_data.get("messages_fts", []):
            ref = str(m.get("peer_ref", ""))
            fts_peer_refs.add(ref.lstrip("p"))
        for peer in db_data.get("peers", []):
            if peer.get("is_bot"):
                bots.add(str(peer.get("id", "")))

    def _has_fts(ids: list[str]) -> bool:
        # FTS peer_refs are bare ids; conversation ids may be namespaced
        # composites (namespace<<32 | id), so compare both forms.
        for aid in ids:
            if aid in fts_peer_refs:
                return True
            try:
                if str(int(aid) & 0xFFFFFFFF) in fts_peer_refs:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def _resolve_type(pid: int | str | None, all_ids: list[str]) -> str:
        base = peer_type(pid) if isinstance(pid, int) else "other"
        if base == "user" and any(aid in bots for aid in all_ids):
            return "bot"
        return base

    for db_name, db_data in state.databases.items():
        conversations = db_data.get("conversations", [])
        if conversations:
            for conv in conversations:
                chat_id = str(conv.get("peer_id", ""))
                all_ids = [str(x) for x in conv.get("all_peer_ids", [])] or (
                    [chat_id] if chat_id else []
                )
                if chat_id and chat_id not in chats:
                    pid = conv.get("peer_id") or 0
                    has_fts = _has_fts(all_ids)
                    chats[chat_id] = {
                        "id": chat_id,
                        "all_peer_ids": all_ids,
                        "name": conv.get("peer_name") or f"Chat {chat_id}",
                        "username": conv.get("peer_username") or "",
                        "type": _resolve_type(pid, all_ids),
                        "has_fts": has_fts,
                        "message_count": conv.get("message_count", 0),
                        "last_message": conv.get("last_message"),
                        "databases": [db_name],
                    }
                elif chat_id and chat_id in chats:
                    chats[chat_id]["message_count"] += conv.get("message_count", 0)
                    chats[chat_id]["databases"].append(db_name)
            continue

        # Legacy path (no conversations_index.json)
        for msg in db_data.get("messages", []):
            chat_id = None
            chat_name = None
            for field in ["chat_id", "peer_id", "dialog_id", "from_id", "to_id"]:
                if field in msg and msg[field]:
                    chat_id = str(msg[field])
                    break
            for field in ["chat_title", "peer_name", "from_name", "title"]:
                if field in msg and msg[field]:
                    chat_name = str(msg[field])
                    break
            if not chat_id:
                continue
            if chat_id not in chats:
                pid = msg.get("peer_id") or 0
                chats[chat_id] = {
                    "id": chat_id,
                    "all_peer_ids": [chat_id],
                    "name": chat_name or f"Chat {chat_id}",
                    "username": msg.get("peer_username") or "",
                    "type": _resolve_type(pid, [chat_id]),
                    "has_fts": _has_fts([chat_id]),
                    "message_count": 0,
                    "last_message": None,
                    "databases": [db_name],
                }
            chats[chat_id]["message_count"] += 1
            if db_name not in chats[chat_id]["databases"]:
                chats[chat_id]["databases"].append(db_name)
            msg_time = msg.get("timestamp", msg.get("date", 0))
            if not chats[chat_id]["last_message"] or msg_time > chats[chat_id]["last_message"]:
                chats[chat_id]["last_message"] = msg_time

    needle = search.lower()
    if needle:
        chats = {
            cid: c
            for cid, c in chats.items()
            if needle in (c.get("name") or "").lower()
            or needle in (c.get("username") or "").lower()
            or needle in cid
        }

    if type_filter == "secret":
        chats = {cid: c for cid, c in chats.items() if c["type"] == "secret"}
    elif type_filter == "fts":
        chats = {cid: c for cid, c in chats.items() if c["has_fts"]}
    elif type_filter:
        chats = {cid: c for cid, c in chats.items() if c["type"] == type_filter}

    if user_id:
        user_name = None
        for db_data in state.databases.values():
            for peer in db_data.get("peers", []):
                if str(peer.get("id", "")) == user_id:
                    user_name = peer.get("first_name", "")
                    if peer.get("last_name"):
                        user_name = f"{user_name} {peer['last_name']}"
                    break
            if user_name:
                break
        if user_name:
            n = user_name.lower()
            chats = {
                cid: c
                for cid, c in chats.items()
                if n in (c.get("name") or "").lower()
            }

    return sorted(chats.values(), key=lambda x: x.get("message_count", 0), reverse=True)
