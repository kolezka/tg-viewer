# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Telegram data extraction, decryption, forensics and visualization toolkit for macOS. Copies the Telegram App Store container, decrypts its SQLCipher Postbox databases, parses the binary message format, surfaces deleted messages and purged media, and serves everything through a local web UI.

Backup directories (`tg_*`, `tg_continuous/`, `tg_vault/`, `test-data/`) hold personal data and are gitignored. Never commit them and never feed them to graph builders.

## Pipeline

```
tg-backup.sh  ->  tg_appstore_decrypt.py  ->  postbox_parser.py  ->  python -m api  (cwd=apps/)
   backup            decrypt                     parse                  webui
```

`./tg-viewer` (bash) orchestrates these steps. Python modules under `apps/tool/` and `apps/api/` are run as packages from `apps/` (`python -m tool.postbox_parser`, `python -m api`), so both `tool` and `api` are top-level imports.

**Decrypt.** `.tempkeyEncrypted` is AES-CBC with a SHA-512 derived key (default password `no-matter-key` when no local passcode is set). The result is a 48-byte raw key + salt. Key correctness is checked with MurmurHash3 x86_32, seed `0xF7CA7FD2`. SQLCipher is opened in raw-key mode with `PRAGMA cipher_default_plaintext_header_size = 32`.

**Parse** (`postbox_parser.py`, per `account-*` directory, steps in order):
1. peers from table `t2`
2. messages from table `t7`, then cross-reference MTProto log events against those rows (`log_parser.py`)
3. full-text rows from `ft41_content` (deleted messages often survive here), media catalog (scan of `media/`), storage catalog from the plaintext sidecar DBs (`storage_db_parser.py`, surfaces tombstones), and a run-vs-run ghost diff against the previous snapshot (`ghost_detector.py`)
4. combined export with FTS dedup, then grouping by conversation

Output per account in `parsed_data/account-<id>/`: `messages.json`, `peers.json`, `conversations_index.json`, `messages_fts.json`, `media_catalog.json`, `storage_catalog.json`, `log_events.json`, `ghosts_history.json`, plus a top-level `summary.json`. `apps/api/loader.py` reads exactly these names; the parser and the API `MediaItem`/model field names must agree or the UI shows blanks.

**Forensics layer.** Three independent sources describe the same media file: the storage catalog (filename, size, on-disk flag), the MTProto debug logs (`<backup>/logs/`, envelope metadata only, payloads are truncated) and `t7` rows with `media[].file_id`. `apps/tool/forensics.py` joins them by `file_id`; tombstones (`on_disk=False`) sort first.

**Continuous capture.** `tg_daemon.py` runs backup, decrypt and parse every 5 minutes with `rsync --link-dest` so a snapshot costs only delta bytes, and prunes by a tiered retention policy. `tg_watcher.py` listens to FSEvents and copies new media into a content-addressed vault (`objects/<sha256>` + `index.jsonl`) to catch upload-then-purge files the daemon would miss. `launchd_install.py` wraps both in code-signed `.app` bundles so TCC remembers consent across restarts.

**Web UI.** `apps/api/app.py` registers routers (`databases`, `users`, `chats`, `messages`, `media`, `storage`, `stats`, `export_data`, `logs`, `ghosts`, `forensics`) before mounting `apps/web/dist/` as `StaticFiles(html=True)` at `/`; the order matters because the catch-all static mount would otherwise shadow the API. State lives on `app.state.app_state` (`AppState`), not module globals. The frontend is React + Bun with TanStack Query and Tailwind; `apps/web/src/api/types.ts` is generated from the running API's OpenAPI schema.

Legacy pipeline (`apps/tool/extract-keys.sh`, `apps/tool/tg_decrypt.py`) still exists but `tg-viewer` does not use it.

## Commands

```bash
./tg-viewer setup && source .venv/bin/activate   # venv, Python deps, bun install + build

./tg-viewer full                                 # backup -> decrypt -> parse -> webui
./tg-viewer backup ./data
./tg-viewer decrypt ./data/tg_*/
./tg-viewer parse ./data/tg_*/
./tg-viewer webui ./data/tg_*/parsed_data        # builds apps/web/dist/ if missing
./tg-viewer dev ./data/tg_*/parsed_data          # FastAPI :5000 + Bun HMR :5173 proxying /api, /docs, /openapi.json

./tg-viewer ghosts [OLD NEW]                     # diff two parsed_data snapshots (defaults: two newest)
./tg-viewer daemon [DEST] [SECONDS]              # foreground capture loop, default ./tg_continuous every 300 s
./tg-viewer watcher [VAULT]                      # FSEvents vault, default ./tg_vault
./tg-viewer install-launchd | uninstall-launchd | launchd-status
./tg-viewer clean                                # delete all backup, decrypted and parsed data
```

Flags for `decrypt`/`parse`/`webui`/`full`: `--account ID|NAME` (an `account-<id>` folder, a bare numeric id, or a peerName from `accounts-shared-data`), `--redact` (mask ids, keys and paths in CLI output), `--port`, `--host`.

`make` wraps the same commands with variables (`make webui DATA=./tg_*/parsed_data ACCOUNT=name`, `make daemon DEST=... INTERVAL=...`). `make import /path/to/account-<id>` stages an already-copied account next to the live `.tempkeyEncrypted` and runs decrypt, parse and webui.

## Tests and checks

```bash
pytest                                            # backend suite, testpaths = apps/api/tests
pytest apps/api/tests/test_fastapi_media.py       # one file
pytest apps/api/tests/test_ghost_detector.py -k diff   # one test by name
cd apps/web && bun run typecheck                  # tsc --noEmit
cd apps/web && bun run codegen                    # regenerate src/api/types.ts from the running API
cd apps/web && bun run build                      # production bundle -> apps/web/dist/
```

`apps/api/tests/conftest.py` puts both `apps/` and the repo root on `sys.path` and provides `fastapi_client`, a `TestClient` loaded with the tiny fixture at `apps/api/tests/fixtures/mini-parsed/` (one account, a placeholder JPEG). Endpoint tests run against that fixture, not real data. There is no frontend test runner; the TS check is the gate.

## Binary format notes

- Postbox peer tags in `t2` values: `02fn04` first_name, `02ln04` last_name, `02un04` username, `01t04` title
- `t7` key: peer_id (8 bytes BE) + padding (4) + timestamp (4 BE) + namespace (4 BE); the namespace is set for secret-chat messages
- Secret chat remote peer: field `r` (`01 72 01` + user_id as LE int32 or int64)
- Storage sidecar table `t15`: 16-byte opaque key, value = `0x00`, uint16 LE name length, UTF-8 name, status byte
- Media filenames encode a signed `file_id`; a negative id appears as a doubled separator (`local-file--123...`)

Longer references live in `docs/` (`cheat-sheet.md` for forensic gotchas, `output-format.md` for the JSON schemas, `architecture.md` for the decryption flow).

@AGENTS.md
