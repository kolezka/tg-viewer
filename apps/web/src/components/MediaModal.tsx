import { api, type Schemas } from "../api/client";
import { formatTimestamp, formatBytes, formatDuration } from "../lib/format";
import { useModal } from "../lib/useModal";

interface Props {
  item: Schemas["MediaItem"];
  onClose: () => void;
}

export default function MediaModal({ item, onClose }: Props) {
  useModal(onClose);
  const url = api.mediaUrl(item.account, item.filename);
  const linked = item.linked_message as
    | { peer_name?: string; timestamp?: number }
    | null
    | undefined;
  const type = item.media_type;

  return (
    <div
      className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-5"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="media-modal-title"
      >
        <div className="flex justify-between items-center px-5 py-3 border-b border-gray-200">
          <div id="media-modal-title" className="text-sm font-mono">{item.filename}</div>
          <button
            onClick={onClose}
            className="px-3 py-1 border border-gray-300 rounded hover:bg-gray-100"
          >
            Close
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
          {type === "photo" || type === "sticker" || type === "gif" ? (
            <img src={url} alt={item.filename} className="max-h-full max-w-full object-contain" />
          ) : type === "video" ? (
            <video src={url} controls className="max-h-full max-w-full" />
          ) : type === "audio" ? (
            <audio src={url} controls className="w-full p-5" />
          ) : (
            <a
              href={url}
              download={item.filename}
              className="text-tg-primary underline p-10 bg-white"
            >
              Download {item.filename}
            </a>
          )}
        </div>
        <div className="px-5 py-3 text-sm bg-gray-50 border-t border-gray-200">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <div className="text-gray-500">Type</div>
              <div>{type}</div>
            </div>
            <div>
              <div className="text-gray-500">Size</div>
              <div>{formatBytes(item.size)}</div>
            </div>
            {item.duration ? (
              <div>
                <div className="text-gray-500">Duration</div>
                <div>{formatDuration(item.duration)}</div>
              </div>
            ) : null}
            {item.width && item.height ? (
              <div>
                <div className="text-gray-500">Dimensions</div>
                <div>{item.width}×{item.height}</div>
              </div>
            ) : null}
          </div>
          {linked && (
            <div className="mt-3 pt-3 border-t border-gray-200 text-xs text-gray-600">
              {linked.peer_name && <span className="font-medium">{linked.peer_name}</span>}
              {linked.timestamp && <span className="ml-2">{formatTimestamp(linked.timestamp)}</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
