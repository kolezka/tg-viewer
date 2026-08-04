# Output format

Layout of `parsed_data/` and the JSON shape of messages and media catalog entries.

## Directory tree

```
parsed_data/
  summary.json                     # Export metadata
  account-{id}/
    peers.json                     # All peers with names, usernames, phones
    messages.json                  # All t7 messages with timestamps + media refs
    messages_fts.json              # Cached/deleted messages from full-text index
    all_messages.json              # t7 + FTS combined and deduplicated
    media_catalog.json             # All media files with MIME, size, dimensions, conversation links
    conversations_index.json       # Conversation list sorted by message count
    conversations/
      {username_or_name}.json      # Individual conversation with full history
```

## Example message JSON

```json
{
  "peer_id": 11049657091,
  "text": "Message content here",
  "outgoing": true,
  "timestamp": 1764974409,
  "date": "2025-12-05T22:40:09+00:00",
  "peer_name": "Channel Name",
  "peer_username": "channel_handle",
  "media": [
    {"file_id": 5203996991054432397, "dc_id": 2, "width": 128, "height": 128,
     "filename": "telegram-cloud-document-2-5203996991054432397"}
  ]
}
```

`outgoing: true` means you sent it; `false` means you received it. For channels, `outgoing` is always `false`. Cached/FTS-only entries set `outgoing: null` (direction not recoverable).

## Example media catalog entry

```json
{
  "filename": "telegram-cloud-photo-size-4-5962787772773288034-y",
  "mime_type": "image/jpeg",
  "size": 487231,
  "media_type": "photo",
  "width": 1280,
  "height": 720,
  "thumbnail": "telegram-cloud-photo-size-4-5962787772773288034-s",
  "linked_message": {
    "peer_id": 10005541293,
    "peer_name": "Group name",
    "timestamp": 1759000793,
    "date": "2025-09-27T19:19:53+00:00"
  }
}
```

`media_type` is one of: `photo`, `video`, `audio`, `gif`, `sticker`, `document`, `avatar`, `deleted`. `linked_message` is null when the file can't be cross-referenced to a parsed message.

`deleted` marks a tombstone for media Telegram has purged. Telegram gives a cached file a second, extension-bearing name by symlinking `<name>.jpg` at it; when the bytes are dropped the link is left dangling. Such an entry has `size: null` (there is nothing to serve — `/api/media/...` returns 404 for it) and a `deleted_target` naming the cache file it pointed at. Its `mime_type` comes from the extension in the dangling name, which is the **only** surviving record of the media's real type: the bare-suffix cache files carry no extension at all. Links whose target is still present in the backup are duplicate views and are not catalogued twice. Like `avatar`, tombstones are excluded from the unfiltered gallery and requested with `?type=deleted`.

`avatar` marks a peer profile picture (`telegram-peer-photo-size-*`) — catalogued for completeness but excluded from the unfiltered `/api/media` gallery, since avatars outnumber conversation photos several times over (3,422 vs 483 in the reference backup). Request them explicitly with `?type=avatar`.

Telegram writes a low-resolution preview alongside every cloud document (`telegram-cloud-document-size-{dc}-{id}-{suffix}`). Those are folded into the full file's `thumbnail` rather than catalogued as separate items; a preview only gets its own entry when the full document is absent from the backup.
