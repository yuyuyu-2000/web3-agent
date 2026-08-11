import type {
  AuthTokenResponse,
  ChatRequest,
  ChatResponse,
  ChatStreamEvent,
  LoginRequest,
  MemoryListResponse,
  MemoryRecord,
  RegisterRequest,
  ToolListResponse,
  UserResponse
} from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";
const API_TOKEN = import.meta.env.VITE_CHAT_API_TOKEN || "";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

interface RequestOptions {
  authToken?: string;
  useStaticToken?: boolean;
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
  options: RequestOptions = {}
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");

  if (options.authToken) {
    headers.set("Authorization", `Bearer ${options.authToken}`);
  } else if (options.useStaticToken !== false && API_TOKEN) {
    headers.set("Authorization", `Bearer ${API_TOKEN}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body);
    } catch {
      // ignore non-json error response
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function registerUser(body: RegisterRequest): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>(
    "/auth/register",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    { useStaticToken: false }
  );
}

export function loginUser(body: LoginRequest): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    { useStaticToken: false }
  );
}

export function getCurrentUser(authToken: string): Promise<UserResponse> {
  return requestJson<UserResponse>("/auth/me", undefined, {
    authToken,
    useStaticToken: false
  });
}

export function sendChat(body: ChatRequest, authToken?: string): Promise<ChatResponse> {
  return requestJson<ChatResponse>(
    "/chat",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    authToken ? { authToken, useStaticToken: false } : {}
  );
}

export async function streamChat(
  body: ChatRequest,
  onEvent: (event: ChatStreamEvent) => void | Promise<void>,
  authToken?: string
): Promise<void> {
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "application/x-ndjson"
  });
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  } else if (API_TOKEN) {
    headers.set("Authorization", `Bearer ${API_TOKEN}`);
  }

  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload);
    } catch {
      // ignore non-json error response
    }
    throw new ApiError(response.status, detail);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += value || "";
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.trim()) await onEvent(JSON.parse(line) as ChatStreamEvent);
    }
    if (done) break;
  }
  if (buffer.trim()) await onEvent(JSON.parse(buffer) as ChatStreamEvent);
}

export function getMemories(authToken: string): Promise<MemoryListResponse> {
  return requestJson<MemoryListResponse>("/memory", undefined, {
    authToken,
    useStaticToken: false
  });
}

export function summarizeMemory(
  params: {
    thread_id: string;
    memory_key: string;
    metadata?: Record<string, unknown>;
  },
  authToken: string
): Promise<MemoryRecord> {
  return requestJson<MemoryRecord>(
    "/memory/summarize",
    {
      method: "POST",
      body: JSON.stringify({
        thread_id: params.thread_id,
        memory_key: params.memory_key,
        metadata: params.metadata ?? { source: "chaincloud-agent-web" }
      })
    },
    {
      authToken,
      useStaticToken: false
    }
  );
}

export function getTools(): Promise<ToolListResponse> {
  return requestJson<ToolListResponse>("/tools");
}
