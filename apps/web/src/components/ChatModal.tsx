import { useState } from "react";
import { useMessages } from "../api/queries";
import type { Schemas } from "../api/client";
import { formatTimestamp } from "../lib/format";
import { useModal } from "../lib/useModal";
import MediaTile from "./MediaTile";

interface Props {
  chat: Schemas["Chat"];
  onClose: () => void;
}

export default function ChatModal({ chat, onClose }: Props) {
  useModal(onClose);
  const [page, setPage] = useState(1);
  const peerIds = chat.all_peer_ids.join(",");
  const { data, isLoading, error } = useMessages({
    peer_id: peerIds,
    per_page: 50,
    page,
  });

  return (
    <div
      className="fixed inset-0 bg-black/55 z-50 flex items-center justify-center p-5"
      onClick={onClose}
      role="dialog"
    >
      <div
        className="bg-white rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center px-5 py-4 border-b border-gray-200 bg-gray-50">
          <h3 className="text-tg-primary font-semibold">
            {chat.name} <span className="text-xs text-gray-500 ml-2">{chat.message_count} msgs</span>
          </h3>
          <button
            onClick={onClose}
            className="px-3 py-1 border border-gray-300 rounded hover:bg-gray-100"
          >
            Close
          </button>
        </div>
        <div className="flex flex-col p-5 overflow-y-auto bg-[#efeae2] flex-1">
          {isLoading && <div className="text-gray-500">Loading…</div>}
          {error && <div className="text-red-600">Error: {(error as Error).message}</div>}
          {data?.messages
            .slice()
            .reverse() // backend returns newest first; flip to chronological per page
            .map((m, i) => (
              <div
                key={`${m.peer_id ?? ""}:${m.timestamp ?? ""}:${i}`}
                className={`conv-bubble ${
                  m.outgoing === true
                    ? "conv-bubble-outgoing"
                    : m.outgoing === false
                    ? "conv-bubble-incoming"
                    : "conv-bubble-unknown"
                }`}
              >
                {(m as { media?: Array<{ filename?: string; media_type?: string; account?: string }>; _account?: string }).media?.length ? (
                  <div className="grid grid-cols-2 gap-1 mb-2">
                    {(m as { media?: Array<{ filename?: string; media_type?: string; account?: string }>; _account?: string }).media!.map((mi, mi_idx) => (
                      <MediaTile
                        key={mi_idx}
                        item={mi}
                        defaultAccount={(m as { _account?: string })._account ?? ""}
                        className="aspect-square overflow-hidden rounded"
                      />
                    ))}
                  </div>
                ) : null}
                <div className="whitespace-pre-wrap">{m.text || <em className="text-gray-500">(no text)</em>}</div>
                <div className="text-xs text-gray-500 mt-1">{formatTimestamp(m.timestamp)}</div>
              </div>
            ))}
        </div>
        {data && data.total_pages > 1 && (
          <div className="flex justify-between items-center px-5 py-3 border-t border-gray-200 bg-gray-50 text-sm">
            <button
              className="px-3 py-1 border border-gray-300 rounded disabled:opacity-50"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ← Newer
            </button>
            <div className="text-gray-500">
              Page {data.page} of {data.total_pages} · {data.total} messages
            </div>
            <button
              className="px-3 py-1 border border-gray-300 rounded disabled:opacity-50"
              disabled={page >= data.total_pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Older →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
