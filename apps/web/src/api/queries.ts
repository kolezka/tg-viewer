import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export const useStats = () =>
  useQuery({ queryKey: ["stats"], queryFn: api.stats });

export const useDatabases = () =>
  useQuery({ queryKey: ["databases"], queryFn: api.databases });

export const useUsers = (params: Parameters<typeof api.users>[0] = {}) =>
  useQuery({ queryKey: ["users", params], queryFn: () => api.users(params) });

export const useChats = (params: Parameters<typeof api.chats>[0] = {}) =>
  useQuery({ queryKey: ["chats", params], queryFn: () => api.chats(params) });

export const useMessages = (params: Parameters<typeof api.messages>[0] = {}) =>
  useQuery({ queryKey: ["messages", params], queryFn: () => api.messages(params) });

export const useMedia = (params: Parameters<typeof api.media>[0] = {}) =>
  useQuery({ queryKey: ["media", params], queryFn: () => api.media(params) });

export const useStorage = (params: Parameters<typeof api.storage>[0] = {}) =>
  useQuery({ queryKey: ["storage", params], queryFn: () => api.storage(params) });

export const useLogs = (params: Parameters<typeof api.logs>[0] = {}) =>
  useQuery({ queryKey: ["logs", params], queryFn: () => api.logs(params) });

export const useForensics = (params: Parameters<typeof api.forensics>[0] = {}) =>
  useQuery({ queryKey: ["forensics", params], queryFn: () => api.forensics(params) });
