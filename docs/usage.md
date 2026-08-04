# Usage

Detailed step-by-step usage and common workflows for `tg-viewer`.

## Step-by-step usage

```bash
./tg-viewer backup ./data           # Copy Telegram data
./tg-viewer decrypt ./data/tg_*/    # Decrypt databases
./tg-viewer parse ./data/tg_*/      # Parse messages into conversations
./tg-viewer webui ./data/tg_*/parsed_data   # Browse in web UI
```

Or call the scripts directly:

```bash
./apps/tool/tg-backup.sh ./data
(cd apps && python3 -m tool.tg_appstore_decrypt ../data/tg_*/)
(cd apps && python3 -m tool.postbox_parser ../data/tg_*/)
./tg-viewer webui ./data/tg_*/parsed_data    # or: (cd apps && python3 -m api ../data/tg_*/parsed_data)
```

## Common scenarios

### Browse a previously-extracted backup

If you've already run the pipeline and just want to open the web UI again, point it at the backup root or the `parsed_data/` directly — both work:

```bash
./tg-viewer webui ./tg_2026-04-26_01-26-40                  # auto-descends into parsed_data/
./tg-viewer webui ./tg_2026-04-26_01-26-40/parsed_data      # explicit form
./tg-viewer webui ./tg_2026-04-26_01-26-40 --port 5050      # custom port
./tg-viewer webui ./tg_2026-04-26_01-26-40 --host 0.0.0.0   # bind all interfaces (LAN access)
```

### Re-parse without re-backing-up

The parser is idempotent — re-running it overwrites `parsed_data/` in place. Useful after pulling parser updates or tweaking extraction logic:

```bash
./tg-viewer parse ./tg_2026-04-26_01-26-40
# then reload the web UI
./tg-viewer webui ./tg_2026-04-26_01-26-40
```

You can also call the parser directly to use extra flags:

```bash
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40)                                # all accounts
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40 --account 12103474868840298699) # one account
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40 --output ../custom-out)         # custom output dir
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40 --password "your_passcode")     # custom passcode
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40 --redact)                       # mask paths/IDs in logs
```

### Re-decrypt only (faster than full pipeline)

If you only need fresh `decrypted_data/` (for example to inspect raw SQLite tables), skip the parser:

```bash
./tg-viewer decrypt ./tg_2026-04-26_01-26-40
# or directly:
(cd apps && python3 -m tool.tg_appstore_decrypt ../tg_2026-04-26_01-26-40 --output ../decrypted-out)
```

### Process an externally-imported account

If you copied an account directory from another machine and have its `.tempkeyEncrypted` (e.g. into `tg_imported_docs/`), the parser handles it the same way — just point at the wrapper directory containing both:

```
tg_imported_docs/
  .tempkeyEncrypted              # required to decrypt
  account-11371877790030934133/
    postbox/
      db/db_sqlite
      media/
```

```bash
(cd apps && python3 -m tool.postbox_parser ../tg_imported_docs --output ../tg_imported_docs/parsed_data)
./tg-viewer webui ./tg_imported_docs/parsed_data
```

If you have the raw `dbKey` + `dbSalt` instead of `.tempkeyEncrypted`, pass them explicitly:

```bash
(cd apps && python3 -m tool.postbox_parser ../tg_imported_docs \
    --db-key   <64-hex-chars> \
    --db-salt  <32-hex-chars>)
```

### Inspect parsed data without the web UI

Each account's JSON output is human-readable and can be queried with `jq`:

```bash
# All conversations sorted by message count
jq '.[] | {name: .peer_name, count: .message_count}' \
    tg_2026-04-26_01-26-40/parsed_data/account-*/conversations_index.json

# Outgoing-only messages from a specific peer
jq '.[] | select(.peer_id == 15868844285 and .outgoing == true) | .text' \
    tg_2026-04-26_01-26-40/parsed_data/account-*/messages.json

# Total media size by type
jq '[.[] | {type: .media_type, size: .size}] | group_by(.type)
    | map({type: .[0].type, count: length, mb: ([.[].size] | add / 1048576)})' \
    tg_2026-04-26_01-26-40/parsed_data/account-*/media_catalog.json
```

### Run on a custom passcode

If you've set a Telegram local passcode, pass it through the workflow:

```bash
TG_PASSCODE="your_passcode"   # used by webui's auto-detection
(cd apps && python3 -m tool.tg_appstore_decrypt ../tg_2026-04-26_01-26-40 --password "your_passcode")
(cd apps && python3 -m tool.postbox_parser     ../tg_2026-04-26_01-26-40 --password "your_passcode")
```

### Privacy-preserving runs (`--redact`)

Mask account IDs, encryption keys, absolute paths, and personal names in console output — useful for sharing logs or screen-recording:

```bash
./tg-viewer --redact full
TG_REDACT=1 ./tg-viewer full              # equivalent via env var
(cd apps && python3 -m tool.postbox_parser ../data --redact)
(cd apps && python3 -m tool.tg_appstore_decrypt ../data --redact)
```

Names are masked structurally (`"Alice Smith"` → `"A**** S****"`) so the log stays readable while leaking nothing useful. Redaction applies to terminal output only — JSON outputs and the web UI are unchanged.

### Multiple accounts at once

The parser auto-discovers every `account-*/` directory under the backup root. If you only want one:

```bash
(cd apps && python3 -m tool.postbox_parser ../tg_2026-04-26_01-26-40 --account 12103474868840298699)
```

The web UI loads every account directory it finds in `parsed_data/`, so the Chats / Media / Users tabs aggregate across all accounts. Each result row carries the `_account` field so you can tell which account it came from.

### Cleaning up

```bash
./tg-viewer clean      # interactive: lists every tg_*/ directory at the project root and asks before deleting
```

Note: `clean` only touches `tg_*/` directories at the project root. Custom output paths or `tg_imported_docs/` are left alone — delete those manually if needed.

### Extraction results

From a real run on 4 accounts:

| Metric | Result |
|--------|--------|
| Databases decrypted | 4/4 (100%) |
| Messages extracted | 1,430,103 |
| Peers identified | 83,364 |
| Conversations | 402 |
| Media files catalogued | 27,595 (photos, videos, audio, stickers, documents) |
| Cached/deleted (FTS) | 588 |
| Metadata noise | 0% |
