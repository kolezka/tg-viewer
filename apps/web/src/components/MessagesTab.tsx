import { useState } from "react";
import { useMessages } from "../api/queries";
import { formatTimestamp } from "../lib/format";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import Pagination from "./Pagination";

export default function MessagesTab() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search, 250);
  const { data, isLoading, error } = useMessages({ search: debouncedSearch, page, per_page: 50 });

  return (
    <div>
      <input
        type="search"
        placeholder="Search messages…"
        className="w-full mb-4 px-3 py-2 border border-gray-300 rounded"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(1);
        }}
      />
      {isLoading && <div className="text-gray-500">Loading…</div>}
      {error && <div className="text-red-600">Error: {(error as Error).message}</div>}
      {data && (
        <>
          <div className="text-sm text-gray-500 mb-3">{data.total.toLocaleString()} messages</div>
          <div className="space-y-3 max-h-[600px] overflow-y-auto">
            {data.messages.map((m, i) => (
              <div
                key={`${m.peer_id ?? ""}:${m.timestamp ?? ""}:${i}`}
                className="border-b border-gray-100 pb-3 last:border-b-0"
              >
                <div className="text-xs text-gray-500 mb-1">
                  {formatTimestamp(m.timestamp)}
                  {m.peer_id !== undefined && m.peer_id !== null && (
                    <span className="ml-2 font-mono">peer={String(m.peer_id)}</span>
                  )}
                  {(m as { source?: string }).source === "fts" && (
                    <span className="ml-2 px-1.5 rounded bg-red-100 text-red-700">FTS</span>
                  )}
                </div>
                <div className="whitespace-pre-wrap">{m.text || <em className="text-gray-500">(no text)</em>}</div>
              </div>
            ))}
          </div>
          <Pagination page={data.page} totalPages={data.total_pages} onChange={setPage} />
        </>
      )}
    </div>
  );
}
