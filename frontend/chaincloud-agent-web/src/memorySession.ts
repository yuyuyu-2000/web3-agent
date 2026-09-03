import type { MemoryRecord, UserResponse } from "./types";

export type ThreadMemoryIdentity = {
  threadId: string;
  memoryKey: string;
};

export function sanitizeKeyPrefix(value: string | null | undefined): string {
  const normalized = (value || "guest").trim().toLowerCase();
  return normalized.replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "guest";
}

function makeRandomSuffix(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID().replace(/-/g, "").slice(0, 12);
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 12);
}

/** Create the IDs for one new conversation in a single operation. */
export function createThreadMemoryIdentity(
  username?: string | null,
  now: Date = new Date(),
  randomSuffix: string = makeRandomSuffix()
): ThreadMemoryIdentity {
  const date = now.toISOString().slice(0, 10).replace(/-/g, "");
  const prefix = username ? sanitizeKeyPrefix(username) : "web";
  const suffix = sanitizeKeyPrefix(randomSuffix);
  return {
    threadId: `${prefix}-thread-${date}-${suffix}`,
    memoryKey: `${prefix}-memory-${date}-${suffix}`
  };
}

function getMemoryStringMetadata(item: MemoryRecord, key: string): string | null {
  const value = item.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function memoryBelongsToCurrentUser(
  item: MemoryRecord,
  user: UserResponse | null
): boolean {
  if (!user) return false;

  const metadataUserId = getMemoryStringMetadata(item, "user_id");
  if (metadataUserId && metadataUserId === user.user_id) return true;

  const metadataUsername = getMemoryStringMetadata(item, "username");
  if (metadataUsername && metadataUsername === user.username) return true;

  // Backward-compatible fallback for older memories that used a username prefix.
  const userPrefix = sanitizeKeyPrefix(user.username);
  return item.memory_key.startsWith(`${userPrefix}-`);
}

/** A pending key becomes active only after it exists in the user's Memory Store view. */
export function resolveActiveMemoryKey(
  memoryKey: string,
  visibleMemories: MemoryRecord[]
): string | undefined {
  const key = memoryKey.trim();
  if (!key) return undefined;
  return visibleMemories.some((item) => item.memory_key === key) ? key : undefined;
}

export function shouldAutoSummarize(
  conversationCount: number,
  threshold: number,
  summarizing: boolean,
  activeMemoryKey?: string
): boolean {
  return conversationCount >= threshold && !summarizing && !activeMemoryKey;
}
