export type Role = "user" | "assistant" | "system" | "error";

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  trace?: ChatTraceEvent[];
  progress?: ExecutionProgressEvent[];
  executionTrace?: ExecutionTrace;
}

export type ObservabilityEventType =
  | "route_selected"
  | "plan_created"
  | "plan_updated"
  | "step_started"
  | "permission_checked"
  | "step_evaluated"
  | "step_completed"
  | "review_decided"
  | "answer_reviewed"
  | "tool_retry"
  | "tool_recovered"
  | "node_completed"
  | "execution_completed";

export interface ExecutionProgressEvent {
  type: ObservabilityEventType;
  timestamp: string;
  node_name?: string;
  duration_ms?: number;
  status?: string;
  mode?: string;
  source?: string;
  reason?: string;
  goal?: string;
  steps?: unknown[];
  step_id?: string;
  action?: string;
  required?: boolean;
  risk_level?: string;
  tool_name?: string;
  attempt?: number;
  final_status?: string;
  total_duration_ms?: number;
  llm_calls?: number;
  tool_calls?: number;
  tool_retries?: number;
  errors?: number;
  [key: string]: unknown;
}

export interface ExecutionTrace {
  trace_id?: string;
  thread_id?: string;
  node_events?: Record<string, unknown>[];
  tool_events?: Record<string, unknown>[];
  decision_events?: Record<string, unknown>[];
  error_events?: Record<string, unknown>[];
  request_summary?: Record<string, unknown>;
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
  monitor_draft_required?: MonitorDraftRequest;
}

export type ChatStreamEvent =
  | { type: "status"; content: string }
  | { type: "delta"; content: string }
  | { type: "done"; reply: string; trace?: ChatTraceEvent[]; execution_trace?: ExecutionTrace }
  | ({ type: "permission_required" } & PermissionRequest)
  | ({ type: "clarification_required" } & ClarificationRequest)
  | ({ type: "monitor_draft_required" } & MonitorDraftRequest)
  | { type: "error"; message: string }
  | ExecutionProgressEvent;

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

export interface MonitorDraft {
  rule_type: "address_transaction" | "large_transaction";
  address: string | null;
  min_amount: number | null;
  min_amount_usd: number | null;
  chain: string | null;
  token: string | null;
  notification_channel: string;
  protocol?: string | null;
}

export interface MonitorDraftRequest {
  status: "monitor_draft_required";
  draft: MonitorDraft;
  summary: string;
  version: number;
  draft_hash: string;
  missing_fields: Array<{ field: string; reason: string }>;
  can_confirm: boolean;
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
