import { useRef, useState } from "react";
import { api } from "../api/client";

interface MediaEntry {
  filename?: string;
  media_type?: string;
  mime_type?: string;
  account?: string;
  width?: number | null;
  height?: number | null;
}

/**
 * Determine how to render a media item.
 *
 * The /api/media catalog provides a clean `media_type` string (photo, video,
 * gif, sticker, audio, document). The /api/messages route's per-message
 * `media[]` entries DON'T — they only carry `filename`, dimensions, and
 * sometimes `mime_type`. So fall back to `mime_type`, then to filename
 * extension. As a last resort, render as `<img>` (which fails gracefully
 * to the missing-file placeholder via onError) — that matches the old
 * inline-JS UI's heuristic.
 */
function resolveType(item: MediaEntry): "photo" | "video" | "audio" | "sticker" | "gif" | "document" {
  const t = (item.media_type ?? "").toLowerCase();
  if (t === "photo" || t === "video" || t === "audio" || t === "sticker" || t === "gif") return t;

  const mime = (item.mime_type ?? "").toLowerCase();
  if (mime.startsWith("image/gif")) return "gif";
  if (mime.startsWith("image/")) return "photo";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";

  const fname = (item.filename ?? "").toLowerCase();
  if (/\.(jpe?g|png|webp|heic|bmp)$/i.test(fname)) return "photo";
  if (/\.gif$/i.test(fname)) return "gif";
  if (/\.(mp4|webm|mov|m4v|mkv|avi)$/i.test(fname)) return "video";
  if (/\.(mp3|m4a|ogg|opus|wav|aac|flac)$/i.test(fname)) return "audio";

  // Secret chat files often have no extension (e.g. "secret-file-XXXX-4").
  // The old UI rendered these as <img> and let onError fall back. Match that.
  if (/^(secret-)?file-/i.test(fname)) return "photo";

  return "document";
}

interface Props {
  item: MediaEntry;
  defaultAccount: string;
  className?: string;
  onClick?: () => void;
}

/**
 * Renders an inline thumbnail for a single media item: photo/sticker/gif as <img>,
 * video as <video> seeked past the black opening frame, anything else as a small
 * download chip. Falls back to a placeholder if the file is missing on disk.
 */
export default function MediaTile({ item, defaultAccount, className, onClick }: Props) {
  const account = item.account ?? defaultAccount;
  const filename = item.filename;
  const type = resolveType(item);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);

  if (!filename || !account) {
    return <Placeholder type={type} className={className} />;
  }

  const url = api.mediaUrl(account, filename);

  const handleVideoLoaded = () => {
    const v = videoRef.current;
    if (!v) return;
    const seekTo = Number.isFinite(v.duration) && v.duration > 0
      ? Math.min(1, v.duration / 4)
      : 0;
    if (seekTo > 0) {
      try { v.currentTime = seekTo; } catch { /* ignore */ }
    }
  };

  if (failed) {
    return <Placeholder type={type} filename={filename} className={className} onClick={onClick} />;
  }

  if (type === "photo" || type === "sticker" || type === "gif") {
    return (
      <button onClick={onClick} className={className} type="button">
        <img
          src={url}
          alt={filename}
          loading="lazy"
          onError={() => setFailed(true)}
          className="w-full h-full object-cover"
        />
      </button>
    );
  }

  if (type === "video") {
    return (
      <button onClick={onClick} className={className} type="button">
        <video
          ref={videoRef}
          src={url}
          preload="metadata"
          muted
          playsInline
          onLoadedMetadata={handleVideoLoaded}
          onError={() => setFailed(true)}
          className="w-full h-full object-cover"
        />
      </button>
    );
  }

  return <Placeholder type={type} filename={filename} className={className} onClick={onClick} />;
}

function Placeholder({ type, filename, className, onClick }: { type: string; filename?: string; className?: string; onClick?: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${className ?? ""} w-full h-full flex items-center justify-center text-xs text-gray-500 p-2 break-all bg-gray-100`}
    >
      {filename ?? type}
    </button>
  );
}
