"""Console output redaction helpers for CLI tools.

A single module-level flag toggles masking. When off (default),
helpers return str(value) unchanged. When on, they return a
masked form that hides account IDs, DB key/salt hex fragments,
and timestamped backup paths.

Activate once at program start:
    import redact
    redact.set_enabled(args.redact)

Then route sensitive values at print time:
    print(f"Account: {redact.account(account_id)}")
    print(f"Key: {redact.hexkey(db_key.hex())}")
    print(f"Output: {redact.path(output_dir)}")
"""

from __future__ import annotations

import re
from pathlib import Path

REDACT: bool = False

_TG_BACKUP_SEGMENT = re.compile(r"tg_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
_ACCOUNT_SEGMENT = re.compile(r"account-\d+")


def set_enabled(flag: bool) -> None:
    """Enable or disable redaction. Call once at program start."""
    global REDACT
    REDACT = bool(flag)


def account(value) -> str:
    """Mask a Telegram account / user ID."""
    if not REDACT:
        return str(value)
    return "***"


def hexkey(value) -> str:
    """Mask a hex key/salt fragment (full hex or truncated `aabb...ccdd` form)."""
    if not REDACT:
        return str(value)
    return "***"


def path(value) -> str:
    """Mask sensitive segments of a backup path, preserving any tail.

    Replaces the `tg_<timestamp>` backup segment with `<backup>` and any
    `account-<numericid>` segment with `<account>`, so REDACT=True doesn't
    leak account IDs embedded in paths.
    """
    if not REDACT:
        return str(value)
    masked = _TG_BACKUP_SEGMENT.sub("<backup>", str(value))
    return _ACCOUNT_SEGMENT.sub("<account>", masked)


def name(value) -> str:
    """Mask a personal name (peer/user/contact display name).

    Preserves the rough shape so logs stay readable: keeps the first
    letter of each word and length hint. `"Alice Smith"` becomes
    `"A**** S****"`; empty / unknown / single-char inputs collapse
    to `"***"`.
    """
    if not REDACT:
        return str(value)
    if value is None:
        return "***"
    s = str(value).strip()
    if not s or s.lower() in {"unknown", "none", "null"}:
        return "***"
    parts = s.split()
    masked_parts = []
    for p in parts:
        if len(p) <= 1:
            masked_parts.append("*")
        else:
            masked_parts.append(p[0] + "*" * (len(p) - 1))
    return " ".join(masked_parts) or "***"


if __name__ == "__main__":
    # Off by default
    assert REDACT is False
    assert account(12345678) == "12345678"
    assert hexkey("a1b2c3d4") == "a1b2c3d4"
    assert name("Alice Smith") == "Alice Smith"
    assert name("") == ""
    assert name(None) == "None"
    assert path("/tmp/tg_2026-04-15_12-58-12/parsed_data") == "/tmp/tg_2026-04-15_12-58-12/parsed_data"

    # On
    set_enabled(True)
    assert account(12345678) == "***"
    assert account("12345678") == "***"
    assert account(None) == "***"
    assert hexkey("a1b2...ef01") == "***"
    assert hexkey("") == "***"
    assert name("Alice Smith") == "A**** S****"
    assert name("Alice") == "A****"
    assert name("X") == "*"
    assert name("") == "***"
    assert name(None) == "***"
    assert name("unknown") == "***"
    assert name("  John   Doe ") == "J*** D**"
    assert path("/tmp/tg_2026-04-15_12-58-12/parsed_data") == "/tmp/<backup>/parsed_data"
    assert path("./tg_2026-04-15_12-58-12") == "./<backup>"
    assert path(Path("/a/tg_2026-04-15_12-58-12/b")) == "/a/<backup>/b"
    # account-<id> segments are masked too
    assert path("/data/account-123456789/parsed_data") == "/data/<account>/parsed_data"
    assert (
        path("/data/tg_2026-04-15_12-58-12/account-42/x")
        == "/data/<backup>/<account>/x"
    )
    # Non-matching path passes through
    assert path("/no/timestamp/here") == "/no/timestamp/here"

    # Back off — flag is resettable
    set_enabled(False)
    assert account(12345678) == "12345678"
    assert name("Alice Smith") == "Alice Smith"

    print("redact.py self-test: OK")
