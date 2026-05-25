# CLAUDE.md

## Project Overview

Telegram data extraction, decryption, and visualization toolkit for macOS. Extracts deleted messages, secret chats, and creates offline archives.

## Architecture

```
apps/tool/tg-backup.sh → apps/tool/tg_appstore_decrypt.py → apps/tool/postbox_parser.py → python -m api (in apps/)
```

`tg-viewer` orchestrates this pipeline. It decrypts `.tempkeyEncrypted` (AES-CBC with SHA-512 of password), opens SQLCipher with `PRAGMA cipher_default_plaintext_header_size = 32`, and parses Postbox binary format from tables t2 (peers) and t7 (messages).

Legacy pipeline (`apps/tool/extract-keys.sh` + `apps/tool/tg_decrypt.py`) still exists but is not used by `tg-viewer`.

## Key files

| File | Purpose |
|------|---------|
| `tg-viewer` | CLI orchestrator (bash) — `full`, `backup`, `decrypt`, `parse`, `webui`, `clean` |
| `apps/tool/tg-backup.sh` | Backup Telegram data from macOS (supports `--batch` for non-interactive use) |
| `apps/tool/tg_appstore_decrypt.py` | Decrypt .tempkeyEncrypted + open SQLCipher databases |
| `apps/tool/postbox_parser.py` | Parse Postbox binary format, extract messages/peers/conversations |
| `apps/tool/redact.py` | Console output redaction helpers |
| `apps/api/` | FastAPI backend package — `python -m api` (with `cwd=apps/`); mounts `apps/web/dist/` via StaticFiles |
| `apps/api/routers/` | Per-resource routers — `chats`, `messages`, `media`, `users`, `stats`, `databases`, `export_data` |
| `apps/api/tests/` | Pytest suite (FastAPI + peer/postbox unit tests) |
| `apps/web/` | React + Bun frontend (TanStack Query, Tailwind, OpenAPI codegen) — `bun run dev` for HMR; `bun run build` → `apps/web/dist/` |
| `apps/tool/extract-keys.sh` | Extract keys from Keychain (legacy) |
| `apps/tool/tg_decrypt.py` | Legacy decryptor (tries multiple key formats) |

## Development commands

```bash
# First-time setup: creates .venv/, installs Python deps, bun install + build
./tg-viewer setup && source .venv/bin/activate

# Full workflow (backup + decrypt + parse + web UI)
./tg-viewer full

# Individual steps
./tg-viewer backup ./data
./tg-viewer decrypt ./data/tg_*/
./tg-viewer parse ./data/tg_*/
./tg-viewer webui ./data/tg_*/parsed_data

# Two-process dev stack: FastAPI on :5000 + Bun HMR on :5173 (with API proxy)
./tg-viewer dev ./data/tg_*/parsed_data

# Cleanup generated data
./tg-viewer clean
```

Useful flags (apply to `decrypt`/`parse`/`webui`/`full`): `--account ID|NAME`
(scope to one account — accepts `account-{id}`, bare numeric id, or peerName
from `accounts-shared-data`), `--redact` (mask IDs/keys/paths in CLI output),
`--port`, `--host`.

## Tests

```bash
pytest                                       # backend (paths set in pyproject.toml → apps/api/tests)
cd apps/web && bun run typecheck             # frontend TS check
cd apps/web && bun run codegen               # regenerate OpenAPI types from running API
```

## Technical notes

- SQLCipher config: `cipher_default_plaintext_header_size = 32`, raw key mode (key + salt = 48 bytes hex)
- Default password: `"no-matter-key"` (when no local passcode set)
- Key verification: MurmurHash3 x86_32 with seed `0xF7CA7FD2`
- Postbox peer tags: `02fn04` (first_name), `02ln04` (last_name), `02un04` (username), `01t04` (title)
- t7 message key: peer_id(8b BE) + padding(4b) + timestamp(4b BE) + namespace(4b BE)
- Secret chat remote peer: field `r` (`01 72 01` + user_id as LE int32/int64)
- Backup directories (`tg_*`, `test-data/`) are gitignored
