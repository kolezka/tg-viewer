import { useState } from "react";
import { useForensics } from "../api/queries";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { formatBytes, formatTimestamp } from "../lib/format";
import Pagination from "./Pagination";

type Filter = "all" | "tombstone" | "with_message" | "with_log";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "tombstone", label: "Tombstones" },
  { key: "with_message", label: "With message" },
  { key: "with_log", label: "With log" },
  { key: "all", label: "All known files" },
];

export default function TombstonesTab() {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("tombstone");
  const [page, setPage] = useState(1);
  const perPage = 60;
  const debouncedSearch = useDebouncedValue(search, 250);

  const params = {
    search: debouncedSearch,
    page,
    per_page: perPage,
    tombstone_only: filter === "tombstone",
    with_message: filter === "with_message",
    with_log: filter === "with_log",
  };

  const { data, isLoading, error } = useForensics(params);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / perPage)) : 1;

  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-3">
        <input
          type="search"
          placeholder="Search by filename, peer, or file_id…"
          className="flex-1 px-3 py-2 border border-gray-300 rounded"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {FILTERS.map((f) => {
          const count = data?.counts?.[f.key] ?? 0;
          return (
            <button
              key={f.key}
              className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                filter === f.key
                  ? "bg-tg-primary text-white border-tg-primary"
                  : "bg-white border-gray-300 hover:border-tg-primary"
              }`}
              onClick={() => {
                setFilter(f.key);
                setPage(1);
              }}
            >
              {f.label} <span className="opacity-75 ml-1">{count}</span>
            </button>
          );
        })}
      </div>

      <p className="text-xs text-gray-500 mb-3">
        Files Telegram's storage DB has ever tracked, joined with MTProto log events and
        t7 message rows by <code>file_id</code>. Tombstones = known but no longer on disk —
        the bytes are gone, only metadata survives.
      </p>

      {isLoading && <div className="text-gray-500">Loading…</div>}
      {error && <div className="text-red-600">{String(error)}</div>}

      {data && (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600 text-xs uppercase">
                <tr>
                  <th className="text-left px-3 py-2">File</th>
                  <th className="text-right px-3 py-2">Size</th>
                  <th className="text-left px-3 py-2">Peer</th>
                  <th className="text-left px-3 py-2">When</th>
                  <th className="text-left px-3 py-2">Sources</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr
                    key={`${row.account}-${row.file_id}`}
                    className={
                      row.tombstone
                        ? "border-b border-red-100 bg-red-50/40"
                        : "border-b border-gray-100"
                    }
                  >
                    <td className="px-3 py-2 align-top">
                      <div className="font-mono text-xs">
                        {row.filenames.length > 0 ? row.filenames[0] : <em className="text-gray-400">no filename</em>}
                      </div>
                      {row.filenames.length > 1 && (
                        <div className="text-xs text-gray-500">
                          + {row.filenames.length - 1} alias{row.filenames.length === 2 ? "" : "es"}
                        </div>
                      )}
                      <div className="text-xs text-gray-400 mt-0.5">file_id={row.file_id}</div>
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums">
                      {formatBytes(row.size_bytes)}
                    </td>
                    <td className="px-3 py-2 align-top">
                      {row.message ? (
                        <>
                          <div className="font-medium">{row.message.peer_name ?? `peer ${row.message.peer_id}`}</div>
                          <div className="text-xs text-gray-500">
                            {row.message.outgoing === true ? "outgoing" : row.message.outgoing === false ? "incoming" : ""}
                          </div>
                        </>
                      ) : row.log_event?.chatId ? (
                        <div className="text-gray-500 italic text-xs">chat {row.log_event.chatId}</div>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-gray-600">
                      {row.message?.timestamp
                        ? formatTimestamp(row.message.timestamp)
                        : row.log_event?.date
                          ? formatTimestamp(row.log_event.date)
                          : "—"}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex flex-wrap gap-1">
                        {row.tombstone && (
                          <span className="px-1.5 py-0.5 rounded text-xs bg-red-100 text-red-700 font-medium">
                            tombstone
                          </span>
                        )}
                        {row.sources.map((s) => (
                          <span
                            key={s}
                            className="px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-600"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.items.length === 0 && (
              <div className="text-center text-gray-500 py-8">No matching files.</div>
            )}
          </div>
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </>
      )}
    </div>
  );
}

