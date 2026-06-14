import { useState } from "react";
import { useMedia } from "../api/queries";
import { type Schemas } from "../api/client";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import Pagination from "./Pagination";
import MediaModal from "./MediaModal";
import MediaTile from "./MediaTile";

const TYPES: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "photo", label: "Photos" },
  { key: "video", label: "Videos" },
  { key: "audio", label: "Audio" },
  { key: "document", label: "Documents" },
  { key: "sticker", label: "Stickers" },
  { key: "gif", label: "GIFs" },
];

export default function MediaTab() {
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  const [active, setActive] = useState<Schemas["MediaItem"] | null>(null);

  const debouncedSearch = useDebouncedValue(search, 250);
  const { data, isLoading, error } = useMedia({ search: debouncedSearch, type, page, per_page: 60 });

  return (
    <div>
      <input
        type="search"
        placeholder="Search media…"
        className="w-full mb-3 px-3 py-2 border border-gray-300 rounded"
        value={search}
        onChange={(e) => { setSearch(e.target.value); setPage(1); }}
      />
      <div className="flex flex-wrap gap-2 mb-4">
        {TYPES.map((t) => {
          const count = t.key ? data?.counts?.[t.key] ?? 0 : data?.total ?? 0;
          return (
            <button
              key={t.key}
              className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                type === t.key
                  ? "bg-tg-primary text-white border-tg-primary"
                  : "bg-white border-gray-300 hover:border-tg-primary"
              }`}
              onClick={() => { setType(t.key); setPage(1); }}
            >
              {t.label} <span className="opacity-75 ml-1">{count}</span>
            </button>
          );
        })}
      </div>

      {isLoading && <div className="text-gray-500">Loading…</div>}
      {error && <div className="text-red-600">Error: {(error as Error).message}</div>}
      {data && (
        <>
          <div className="text-sm text-gray-500 mb-3">{data.total} items</div>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {data.media.map((m) => (
              <MediaTile
                key={`${m.account}:${m.filename}`}
                item={m}
                defaultAccount={m.account ?? ""}
                onClick={() => setActive(m)}
                className="aspect-square bg-gray-100 rounded overflow-hidden border border-gray-200 hover:border-tg-primary"
              />
            ))}
          </div>
          <Pagination page={data.page} totalPages={data.total_pages} onChange={setPage} />
        </>
      )}

      {active && <MediaModal item={active} onClose={() => setActive(null)} />}
    </div>
  );
}
