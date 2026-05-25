import type { components } from "./types";

export type Schemas = components["schemas"];

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`HTTP ${status}: ${body}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return (await res.json()) as T;
}

export const api = {
  stats: () => request<Schemas["Stats"]>("/api/stats"),
  databases: () => request<Schemas["DatabaseSummary"][]>("/api/databases"),
  database: (name: string) =>
    request<Schemas["DatabaseDetail"]>(`/api/database/${encodeURIComponent(name)}`),
  users: (params: { search?: string; page?: number; per_page?: number } = {}) =>
    request<Schemas["UsersPage"]>(`/api/users?${qs(params)}`),
  chats: (params: { search?: string; type?: string; user_id?: string } = {}) =>
    request<Schemas["Chat"][]>(`/api/chats?${qs(params)}`),
  messages: (params: {
    page?: number;
    per_page?: number;
    database?: string;
    search?: string;
    peer_id?: string;
  } = {}) => request<Schemas["MessagesPage"]>(`/api/messages?${qs(params)}`),
  media: (params: {
    search?: string;
    type?: string;
    account?: string;
    page?: number;
    per_page?: number;
  } = {}) => request<Schemas["MediaPage"]>(`/api/media?${qs(params)}`),
  mediaUrl: (account: string, filename: string) =>
    `/api/media/${encodeURIComponent(account)}/${encodeURIComponent(filename)}`,
  storage: (params: {
    tombstone_only?: boolean;
    source?: string;
    search?: string;
    page?: number;
    per_page?: number;
  } = {}) => request<Schemas["StoragePage"]>(`/api/storage?${qs(params)}`),
  logs: (params: {
    event_type?: string;
    ghost_only?: boolean;
    peer_id?: string;
    account?: string;
    search?: string;
    page?: number;
    per_page?: number;
  } = {}) => request<Schemas["LogEventPage"]>(`/api/logs?${qs(params)}`),
  forensics: (params: {
    tombstone_only?: boolean;
    with_message?: boolean;
    with_log?: boolean;
    account?: string;
    search?: string;
    page?: number;
    per_page?: number;
  } = {}) => request<Schemas["ForensicPage"]>(`/api/forensics?${qs(params)}`),
};

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === false) continue;
    u.set(k, String(v));
  }
  return u.toString();
}
