import { useState, useEffect } from "react";
import { useChats } from "../api/queries";
import type { Schemas } from "../api/client";
import { formatTimestamp } from "../lib/format";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import ChatModal from "./ChatModal";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "user", label: "Users" },
  { key: "secret", label: "Secret" },
  { key: "group", label: "Groups" },
  { key: "channel", label: "Channels" },
  { key: "bot", label: "Bots" },
  { key: "fts", label: "Has FTS" },
];

interface Props {
  initialSearch?: string;
}

// Defensive cap to avoid rendering an unbounded chat list (no virtualization).
const MAX_CHATS = 500;

export default function ChatsTab({ initialSearch = "" }: Props) {
  const [search, setSearch] = useState(initialSearch);
  const [type, setType] = useState("");
  const [active, setActive] = useState<Schemas["Chat"] | null>(null);

  // Sync local search when parent passes a new initial value (e.g., from user click)
  useEffect(() => {
    setSearch(initialSearch);
  }, [initialSearch]);

  const debouncedSearch = useDebouncedValue(search, 250);
  const { data, isLoading, error } = useChats({ search: debouncedSearch, type });

  return (
    <div>
      <input
        type="search"
        placeholder="Search chats…"
        className="w-full mb-3 px-3 py-2 border border-gray-300 rounded"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="flex flex-wrap gap-2 mb-4">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
              type === f.key
                ? "bg-tg-primary text-white border-tg-primary"
                : "bg-white border-gray-300 hover:border-tg-primary hover:text-tg-primary"
            }`}
            onClick={() => setType(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading && <div className="text-gray-500">Loading…</div>}
      {error && <div className="text-red-600">Error: {(error as Error).message}</div>}
      {data && (
        <div className="space-y-2">
          {data.length === 0 && <div className="text-gray-500">No chats match.</div>}
          {data.length > MAX_CHATS && (
            <div className="text-xs text-gray-500">
              Showing first {MAX_CHATS} of {data.length} — refine your search.
            </div>
          )}
          {data.slice(0, MAX_CHATS).map((c) => (
            <button
              key={c.id}
              onClick={() => setActive(c)}
              className="w-full border border-gray-200 rounded p-3 bg-white text-left hover:border-tg-primary hover:bg-blue-50"
            >
              <div className="flex justify-between items-start">
                <div>
                  <div className="font-semibold">
                    {c.name}{" "}
                    <span className={`inline-block text-xs px-2 py-0.5 rounded-full ml-1 ${typeBadge(c.type)}`}>
                      {c.type}
                    </span>
                    {c.has_fts && (
                      <span className="inline-block text-xs px-2 py-0.5 rounded-full ml-1 bg-red-100 text-red-700">
                        FTS
                      </span>
                    )}
                  </div>
                  {c.username && <div className="text-xs text-gray-500">@{c.username}</div>}
                </div>
                <div className="text-right text-sm">
                  <div>{c.message_count.toLocaleString()} msgs</div>
                  <div className="text-xs text-gray-500">{formatTimestamp(c.last_message)}</div>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {active && <ChatModal chat={active} onClose={() => setActive(null)} />}
    </div>
  );
}

function typeBadge(type: string): string {
  switch (type) {
    case "secret": return "bg-red-100 text-red-700";
    case "bot": return "bg-indigo-100 text-indigo-700";
    case "channel": return "bg-green-100 text-green-700";
    case "group": return "bg-orange-100 text-orange-700";
    default: return "bg-gray-100 text-gray-700";
  }
}
