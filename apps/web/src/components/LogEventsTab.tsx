import { useState } from "react";
import { useLogs } from "../api/queries";
import type { Schemas } from "../api/client";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { formatBytes } from "../lib/format";
import Pagination from "./Pagination";

const EVENT_TYPES: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "encrypted_message", label: "Encrypted msg" },
  { key: "upload_part", label: "Uploads" },
  { key: "download_file", label: "Downloads" },
  { key: "download_request", label: "Dl requests" },
  { key: "pending_removed", label: "Sent acks" },
  { key: "secret_chat_update", label: "Secret-chat" },
];

export default function LogEventsTab() {
  const [search, setSearch] = useState("");
  const [eventType, setEventType] = useState("");
  const [ghostOnly, setGhostOnly] = useState(false);
  const [page, setPage] = useState(1);
  const perPage = 80;
  const debouncedSearch = useDebouncedValue(search, 250);

  const { data, isLoading, error } = useLogs({
    event_type: eventType,
    ghost_only: ghostOnly,
    search: debouncedSearch,
    page,
    per_page: perPage,
  });

  const totalPages = data?.total_pages ?? 1;

  return (
    <div>
      <div className="flex items-start gap-3 mb-3">
        <input
          type="search"
          placeholder="Search log events…"
          className="flex-1 px-3 py-2 border border-gray-300 rounded"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <label className="flex items-center gap-2 text-sm select-none">
          <input
            type="checkbox"
            checked={ghostOnly}
            onChange={(e) => {
              setGhostOnly(e.target.checked);
              setPage(1);
            }}
          />
          Ghost only
        </label>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {EVENT_TYPES.map((t) => {
          const count = t.key ? data?.counts?.[t.key] ?? 0 : data?.total ?? 0;
          return (
            <button
              key={t.key}
              className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                eventType === t.key
                  ? "bg-tg-primary text-white border-tg-primary"
                  : "bg-white border-gray-300 hover:border-tg-primary"
              }`}
              onClick={() => {
                setEventType(t.key);
                setPage(1);
              }}
            >
              {t.label} <span className="opacity-75 ml-1">{count}</span>
            </button>
          );
        })}
      </div>

      <p className="text-xs text-gray-500 mb-3">
        Parsed from <code>logs/log-*.txt</code> in the backup. Each
        <code className="mx-1">encrypted_message</code> records the file's id,
        accessHash, dcId, size and keyFingerprint — survives even when the t7 row
        has been deleted (<span className="font-medium text-red-600">GHOST</span>).
      </p>

      {isLoading && <div className="text-gray-500">Loading…</div>}
      {error && <div className="text-red-600">{String(error)}</div>}

      {data && (
        <>
          <ul className="space-y-1 font-mono text-xs">
            {data.events.map((ev, i) => (
              <li
                key={`${ev.source_file}-${ev.source_line}-${i}`}
                className={`px-2 py-1.5 rounded ${
                  ev.event === "encrypted_message" && ev.in_db === false
                    ? "bg-red-50 border border-red-200"
                    : "bg-gray-50"
                }`}
              >
                <LogRow ev={ev} />
              </li>
            ))}
          </ul>
          {data.events.length === 0 && (
            <div className="text-center text-gray-500 py-8">No matching events.</div>
          )}
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </>
      )}
    </div>
  );
}

// Log `data` fields are loosely typed (`unknown`); coerce for display.
function str(v: unknown): string {
  return v == null ? "" : String(v);
}
function num(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}

function LogRow({ ev }: { ev: Schemas["LogEvent"] }) {
  const ts = ev.log_timestamp;
  const evType = ev.event;
  const data = (ev.data ?? {}) as Record<string, unknown>;

  if (evType === "encrypted_message") {
    const file = data.file as Record<string, unknown> | undefined;
    const isGhost = ev.in_db === false;
    return (
      <div>
        <span className="text-gray-500">{ts}</span>
        {" "}
        <span className="text-blue-700 font-semibold">encrypted_message</span>
        {" "}
        chatId={str(data.chatId ?? data.chat_id)}
        {file && (
          <>
            {" · "}
            <span className="text-purple-700">file</span>{" "}
            id={str(file.id)} dc={str(file.dcId ?? file.dc_id)} size={formatBytes(num(file.size))} kf={str(file.keyFingerprint ?? file.key_fingerprint)}
          </>
        )}
        {!file && <span className="text-gray-400"> · text-only</span>}
        {isGhost && (
          <span className="ml-2 px-1.5 py-0.5 rounded bg-red-600 text-white text-xs font-bold">
            GHOST
          </span>
        )}
      </div>
    );
  }

  if (evType === "upload_part") {
    return (
      <div>
        <span className="text-gray-500">{ts}</span>{" "}
        <span className="text-orange-700 font-semibold">upload_part</span>{" "}
        fileId={str(data.fileId ?? data.file_id)} part={str(data.filePart ?? data.file_part)}{" "}
        bytes={formatBytes(num(data.bytes_size))}
      </div>
    );
  }

  if (evType === "download_file" || evType === "download_request") {
    return (
      <div>
        <span className="text-gray-500">{ts}</span>{" "}
        <span className={evType === "download_file" ? "text-green-700 font-semibold" : "text-amber-700 font-semibold"}>
          {evType}
        </span>{" "}
        id={str(data.fileId ?? data.file_id ?? data.id)} size={formatBytes(num(data.size ?? data.bytes_size))}
      </div>
    );
  }

  if (evType === "pending_removed") {
    return (
      <div>
        <span className="text-gray-500">{ts}</span>{" "}
        <span className="text-gray-700 font-semibold">pending_removed</span>{" "}
        chatId={str(data.chatId ?? data.chat_id)} msg={str(data.msg_index_a ?? data.msgIndexA)}_
        {str(data.msg_index_b ?? data.msgIndexB)}
      </div>
    );
  }

  return (
    <div>
      <span className="text-gray-500">{ts}</span>{" "}
      <span className="font-semibold">{evType}</span>{" "}
      <span className="text-gray-500">{JSON.stringify(data).slice(0, 200)}</span>
    </div>
  );
}
