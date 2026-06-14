/**
 * Render a timestamp from the API. The parser emits two shapes for the same
 * field across different files:
 *  - numeric Unix seconds (e.g. messages.json `timestamp: 1700000010`)
 *  - ISO 8601 string (e.g. conversations_index.json `last_message: "2026-04-25T23:24:23+00:00"`)
 * Accept both.
 */
export function formatTimestamp(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "" || ts === 0) return "—";
  const d = typeof ts === "string" ? new Date(ts) : new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null || n < 0) return "—";
  if (n === 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
