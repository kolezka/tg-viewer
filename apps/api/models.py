"""Pydantic response models for the webui FastAPI endpoints.

Messages and media-catalog entries are flexible by design — the parser may
emit fields we don't know about. We use `extra='allow'` for those.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    id: int | str
    name: str
    username: str = ""
    phone: str = ""
    database: str


class UsersPage(BaseModel):
    users: list[User]
    total: int
    page: int
    per_page: int
    total_pages: int


class DatabaseSummary(BaseModel):
    name: str
    decrypted: bool
    message_count: int
    tables: list[str]


class DatabaseDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    decrypted: bool
    messages: list[dict[str, Any]]
    peers: list[dict[str, Any]]
    conversations: list[dict[str, Any]]
    media_catalog: list[dict[str, Any]]


class Chat(BaseModel):
    id: str
    all_peer_ids: list[str]
    name: str
    username: str = ""
    type: str
    has_fts: bool
    message_count: int
    # The parser may emit ISO-8601 strings (e.g. "2026-04-25T23:24:23+00:00")
    # OR numeric Unix timestamps. Accept both; the frontend normalises in
    # formatTimestamp().
    last_message: int | float | str | None = None
    databases: list[str]


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str = ""
    peer_id: int | str | None = None
    # Same heterogeneity as Chat.last_message — parser may emit a string or number.
    timestamp: int | float | str | None = None
    outgoing: bool | None = None


class MessagesPage(BaseModel):
    messages: list[Message]
    total: int
    page: int
    per_page: int
    total_pages: int


class MediaItem(BaseModel):
    filename: str = ""
    mime_type: str = ""
    media_type: str = ""
    account: str = ""
    linked_message: dict[str, Any] | None = None
    thumbnail: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None


class MediaPage(BaseModel):
    media: list[MediaItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    counts: dict[str, int]


class StatsDb(BaseModel):
    decrypted: bool
    message_count: int
    tables: int


class Stats(BaseModel):
    total_databases: int
    decrypted_databases: int
    total_messages: int
    total_chats: int
    databases: dict[str, StatsDb]


class LogEvent(BaseModel):
    """One forensic event extracted from a Telegram macOS debug log.

    Shape mirrors `tool.log_parser`'s record format. `data` is event-specific
    so we leave it flexible. `in_db` + `db_match` are populated only for
    `encrypted_message` events; `account` is added at API serialization time.
    """
    model_config = ConfigDict(extra="allow")
    event: str
    source_file: str = ""
    source_line: int = 0
    log_timestamp: str = ""
    data: dict[str, Any] = {}
    in_db: bool | None = None
    db_match: dict[str, Any] | None = None
    account: str = ""


class LogEventPage(BaseModel):
    events: list[LogEvent]
    total: int
    page: int
    per_page: int
    total_pages: int
    counts: dict[str, int]


class ExportData(BaseModel):
    accounts: list[Any] = []
    databases: dict[str, Any] = {}
    media_files: list[Any] = []
    total_media: int = 0
    backup_size: str = ""
