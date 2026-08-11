export type Role = "user" | "assistant" | "system" | "error";

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  trace?: ChatTraceEvent[];
}

export interface ChatTraceEvent {
  event: string;
  tool_name?: string | null;
  content_preview?: string | null;
  args_preview?: string | null;
  error?: string | null;
}

export interface ChatRequest {
  thread_id: string;
  message: string;
  memory_key?: string;
  debug?: boolean;
  trace_max_chars?: number;
}

export interface ChatResponse {
  reply: string;
  trace?: ChatTraceEvent[];
  permission_required?: PermissionRequest;
  clarification_required?: ClarificationRequest;
}

export type ChatStreamEvent =
  | { type: "status"; content: string }
  | { type: "delta"; content: string }
  | { type: "done"; reply: string; trace?: ChatTraceEvent[] }
  | ({ type: "permission_required" } & PermissionRequest)
  | ({ type: "clarification_required" } & ClarificationRequest)
  | { type: "error"; message: string };

export interface PermissionRequest {
  step_id: string;
  tool_name: string;
  risk_level: "none" | "low" | "medium" | "high" | "critical";
  reason: string;
  operation_summary: string;
  estimated_impact: string;
}

export interface MissingStateField {
  field: string;
  reason: string;
  question: string;
  expected_format: string;
}

export interface ClarificationRequest {
  step_id: string;
  resolution: "clarification" | "partial" | "fail";
  missing_state: MissingStateField[];
  reason: string;
}

export interface MemoryRecord {
  memory_key: string;
  summary: string;
  source_thread_id: string;
  metadata: Record<string, unknown>;
  updated_at: string;
}

export interface MemoryListResponse {
  memories: MemoryRecord[];
}

export interface ToolInfo {
  name: string;
  description?: string;
  enabled?: boolean;
}

export interface ToolListResponse {
  tools?: ToolInfo[];
  registered_tools?: ToolInfo[];
}

export interface UserResponse {
  user_id: string;
  username: string;
  display_name?: string | null;
  metadata: Record<string, unknown>;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: UserResponse;
}

export interface RegisterRequest {
  username: string;
  password: string;
  display_name?: string;
  metadata?: Record<string, unknown>;
}

export interface LoginRequest {
  username: string;
  password: string;
}
