import { describe, expect, it } from "vitest";
import type { MemoryRecord, UserResponse } from "./types";
import {
  createThreadMemoryIdentity,
  memoryBelongsToCurrentUser,
  resolveActiveMemoryKey,
  shouldAutoSummarize
} from "./memorySession";

const alice: UserResponse = {
  user_id: "user-alice",
  username: "Alice",
  metadata: {}
};

function memory(memoryKey: string, threadId: string): MemoryRecord {
  return {
    memory_key: memoryKey,
    summary: `Summary for ${threadId}`,
    source_thread_id: threadId,
    metadata: { user_id: alice.user_id, username: alice.username },
    updated_at: "2026-09-03T00:00:00Z"
  };
}

describe("thread-scoped default memory keys", () => {
  it("generates different thread and memory IDs for two conversations on the same day", () => {
    const date = new Date("2026-09-03T08:00:00Z");
    const sessionA = createThreadMemoryIdentity(alice.username, date, "aaa111");
    const sessionB = createThreadMemoryIdentity(alice.username, date, "bbb222");

    expect(sessionA.threadId).toBe("alice-thread-20260903-aaa111");
    expect(sessionA.memoryKey).toBe("alice-memory-20260903-aaa111");
    expect(sessionB.threadId).not.toBe(sessionA.threadId);
    expect(sessionB.memoryKey).not.toBe(sessionA.memoryKey);
  });

  it("keeps a new key pending until that exact memory has been persisted", () => {
    const session = createThreadMemoryIdentity(alice.username, new Date("2026-09-03"), "newkey");

    expect(resolveActiveMemoryKey(session.memoryKey, [])).toBeUndefined();
    expect(shouldAutoSummarize(8, 8, false, undefined)).toBe(true);

    const persisted = memory(session.memoryKey, session.threadId);
    expect(resolveActiveMemoryKey(session.memoryKey, [persisted])).toBe(session.memoryKey);
    expect(shouldAutoSummarize(8, 8, false, session.memoryKey)).toBe(false);
  });

  it("creates independent memories for thread A and B and still permits manual binding", () => {
    const date = new Date("2026-09-03");
    const sessionA = createThreadMemoryIdentity(alice.username, date, "thread-a");
    const memoryA = memory(sessionA.memoryKey, sessionA.threadId);
    const sessionB = createThreadMemoryIdentity(alice.username, date, "thread-b");

    // Starting B does not bind the already persisted memory from A.
    expect(resolveActiveMemoryKey(sessionB.memoryKey, [memoryA])).toBeUndefined();
    expect(shouldAutoSummarize(8, 8, false, undefined)).toBe(true);

    // B can persist its own pending key after reaching the threshold.
    const memoryB = memory(sessionB.memoryKey, sessionB.threadId);
    expect(resolveActiveMemoryKey(sessionB.memoryKey, [memoryA, memoryB])).toBe(sessionB.memoryKey);

    // Selecting A from the list explicitly binds A to thread B.
    expect(resolveActiveMemoryKey(memoryA.memory_key, [memoryA, memoryB])).toBe(memoryA.memory_key);
    expect(memoryBelongsToCurrentUser(memoryA, alice)).toBe(true);
  });
});
