import type { Chat } from "../types";

export function sortChatsForDisplay(chats: Chat[]): Chat[] {
  const pinned = chats
    .filter((chat) => chat.pinned)
    .sort((a, b) => (a.pinnedAt ?? 0) - (b.pinnedAt ?? 0));
  const unpinned = chats.filter((chat) => !chat.pinned);
  return [...pinned, ...unpinned];
}
