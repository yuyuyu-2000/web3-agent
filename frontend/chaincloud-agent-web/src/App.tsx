import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  API_BASE_URL,
  decideMonitorDraft,
  decidePermission,
  getCurrentUser,
  getMemories,
  getTools,
  streamChat,
  submitClarification,
  summarizeMemory
} from "./api";
import LoginPage from "./LoginPage";
import type {
  AuthTokenResponse,
  ChatMessage,
  ChatStreamEvent,
  ClarificationRequest,
  ExecutionProgressEvent,
  ExecutionTrace,
  MemoryRecord,
  MonitorDraftRequest,
  PermissionRequest,
  ToolInfo,
  UserResponse
} from "./types";

const AUTH_TOKEN_STORAGE_KEY = "chaincloud_agent_web_auth_token";
const AUTH_USER_STORAGE_KEY = "chaincloud_agent_web_auth_user";

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
}

function sanitizeKeyPrefix(value: string | null | undefined): string {
  const normalized = (value || "guest").trim().toLowerCase();
  return normalized.replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "guest";
}

function makeThreadId(username?: string | null): string {
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const prefix = username ? sanitizeKeyPrefix(username) : "web";
  return `${prefix}-thread-${date}-${Math.random().toString(16).slice(2, 8)}`;
}

function makeMemoryKey(username?: string | null): string {
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const prefix = username ? sanitizeKeyPrefix(username) : "web";
  return `${prefix}-memory-${date}`;
}

function getMemoryStringMetadata(item: MemoryRecord, key: string): string | null {
  const value = item.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function memoryBelongsToCurrentUser(item: MemoryRecord, user: UserResponse | null): boolean {
  if (!user) return false;

  const metadataUserId = getMemoryStringMetadata(item, "user_id");
  if (metadataUserId && metadataUserId === user.user_id) return true;

  const metadataUsername = getMemoryStringMetadata(item, "username");
  if (metadataUsername && metadataUsername === user.username) return true;

  // Backward-compatible fallback for older memories that were named with username prefix.
  const userPrefix = sanitizeKeyPrefix(user.username);
  return item.memory_key.startsWith(`${userPrefix}-`);
}

function cleanupMemoryTitle(text: string): string {
  return text
    .replace(/^#+\s*/g, "")
    .replace(/^[-*\d.、\s]+/g, "")
    .replace(/^(本次对话|对话总结|总结|摘要)[:：\s]*/g, "")
    .trim();
}

function memoryTitle(item: MemoryRecord): string {
  const metadataTitle = getMemoryStringMetadata(item, "title");
  const summary = item.summary || "";
  const firstLine = summary
    .split("\\n")
    .map((line) => cleanupMemoryTitle(line))
    .find(Boolean);

  const title = cleanupMemoryTitle(metadataTitle || firstLine || item.memory_key || "未命名记忆");
  return title.length > 36 ? `${title.slice(0, 36)}...` : title;
}

function roleLabel(role: ChatMessage["role"]): string {
  if (role === "user") return "你";
  if (role === "assistant") return "ChainCloud Agent";
  if (role === "system") return "系统";
  return "错误";
}

function isImageContent(content: string): boolean {
  return content.startsWith("[IMAGE]") && content.length > 7;
}

function extractImageUrl(content: string): string {
  return content.slice(7);
}

function isChartContent(content: string): boolean {
  return content.startsWith("[CHART]") && content.length > 7;
}

function extractChartUrl(content: string): string {
  const url = content.slice(7);
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_BASE_URL}${url}`;
}

function stripReasoningBlocks(content: string): string {
  return content
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/<\/?think>/gi, "")
    .replace(/!\[.*?\]\(\s*\)/g, "")
    .trim();
}

function buildAssistantMessages(
  replyText: string,
  trace: ChatMessage["trace"],
  progress?: ExecutionProgressEvent[],
  executionTrace?: ExecutionTrace
): ChatMessage[] {
  const assistantMessages: ChatMessage[] = [];
  const cleanedReply = stripReasoningBlocks(replyText);
  let textOnly = cleanedReply;

  const imageRegex = /!\[.*?\]\((https?:\/\/[^\s)]+)\)/g;
  const imageUrls = Array.from(cleanedReply.matchAll(imageRegex), (match) => match[1]);
  textOnly = textOnly.replace(imageRegex, "").trim();

  const chartUrls: string[] = [];
  const chartJsonRegex = /\{[^{}]*"url"\s*:\s*"([^"]+\.html)"[^{}]*\}/g;
  textOnly = textOnly.replace(chartJsonRegex, (_matched, url: string) => {
    chartUrls.push(url);
    return "";
  }).trim();

  const chartUrlRegex = /(^|[\s(])((?:https?:\/\/[^\s"')]+)?\/charts\/[^\s"')]+\.html)/g;
  textOnly = textOnly.replace(chartUrlRegex, (matched, prefix: string, url: string) => {
    chartUrls.push(url);
    return prefix && matched.startsWith(prefix) ? prefix : "";
  }).trim();

  if (textOnly) {
    assistantMessages.push({
      id: newId("assistant-text"),
      role: "assistant",
      content: textOnly,
      trace,
      progress,
      executionTrace
    });
  }

  imageUrls.forEach((url, idx) => {
    assistantMessages.push({
      id: newId(`assistant-img-${idx}`),
      role: "assistant",
      content: `[IMAGE]${url}`,
      trace: !textOnly && idx === 0 ? trace : undefined,
      progress: !textOnly && idx === 0 ? progress : undefined,
      executionTrace: !textOnly && idx === 0 ? executionTrace : undefined
    });
  });

  Array.from(new Set(chartUrls)).forEach((url, idx) => {
    assistantMessages.push({
      id: newId(`assistant-chart-${idx}`),
      role: "assistant",
      content: `[CHART]${url}`,
      trace: !textOnly && imageUrls.length === 0 && idx === 0 ? trace : undefined,
      progress: !textOnly && imageUrls.length === 0 && idx === 0 ? progress : undefined,
      executionTrace: !textOnly && imageUrls.length === 0 && idx === 0 ? executionTrace : undefined
    });
  });

  if (assistantMessages.length === 0) {
    assistantMessages.push({
      id: newId("assistant"),
      role: "assistant",
      content: cleanedReply || replyText,
      trace,
      progress,
      executionTrace
    });
  }

  return assistantMessages;
}

const OBSERVABILITY_EVENT_TYPES = new Set([
  "route_selected", "plan_created", "plan_updated", "step_started",
  "permission_checked", "step_evaluated", "step_completed", "review_decided",
  "answer_reviewed", "tool_retry", "tool_recovered", "node_completed",
  "execution_completed"
]);

function isObservabilityEvent(event: ChatStreamEvent): event is ExecutionProgressEvent {
  return OBSERVABILITY_EVENT_TYPES.has(event.type);
}

const NODE_LABELS: Record<string, string> = {
  router: "路由选择", direct_agent: "直接执行", planner: "生成计划",
  select_step: "选择步骤", permission_gate: "权限检查", executor: "执行任务",
  tools: "调用工具", evaluator: "结果评估", replan: "更新计划",
  compose_answer: "组织回答", reviewer: "审核回答"
};

function progressLabel(event: ExecutionProgressEvent): string {
  switch (event.type) {
    case "route_selected": return `选择执行模式：${event.mode || "unknown"}`;
    case "plan_created": return "执行计划已生成";
    case "plan_updated": return "执行计划已更新";
    case "step_started": return `开始步骤 ${event.step_id || ""}`.trim();
    case "step_evaluated": return `评估步骤：${event.action || "完成"}`;
    case "step_completed": return `步骤完成：${event.status || "success"}`;
    case "permission_checked": return `权限检查：${event.action || "完成"}`;
    case "review_decided": return event.required ? "回答需要审核" : "无需额外审核";
    case "answer_reviewed": return `回答审核：${event.action || "完成"}`;
    case "tool_retry": return `重试工具 ${event.tool_name || ""}`.trim();
    case "tool_recovered": return `工具恢复 ${event.tool_name || ""}`.trim();
    case "execution_completed": return "执行完成";
    case "node_completed": return NODE_LABELS[event.node_name || ""] || event.node_name || "节点完成";
    default: return event.type;
  }
}

function ExecutionTimeline({ message, devMode }: { message: ChatMessage; devMode: boolean }) {
  const progress = message.progress || [];
  if (!progress.length) return null;
  const isRunning = progress[progress.length - 1]?.type !== "execution_completed";

  return (
    <section className="execution-timeline" aria-label="Agent 执行过程">
      <div className="execution-title">
        <strong>执行过程</strong>
        <span className={isRunning ? "execution-running" : "execution-finished"}>
          {isRunning ? "进行中" : "已完成"}
        </span>
      </div>
      <ol>
        {progress.map((item, index) => (
          <li key={`${item.type}-${item.timestamp}-${index}`}>
            <span className="execution-dot" />
            <div className="execution-event">
              <div>
                <span>{progressLabel(item)}</span>
                {typeof item.duration_ms === "number" ? <time>{item.duration_ms.toFixed(0)} ms</time> : null}
              </div>
              {devMode && item.reason ? <small>{item.reason}</small> : null}
              {devMode ? (
                <details>
                  <summary>详情</summary>
                  <pre>{JSON.stringify(item, null, 2)}</pre>
                </details>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {devMode && message.executionTrace ? (
        <details className="execution-raw">
          <summary>完整 execution trace</summary>
          <pre>{JSON.stringify(message.executionTrace, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
}

function CopyableToken({ value, isAbbreviated = false }: { value: string; isAbbreviated?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    if (isAbbreviated) return;

    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      className={isAbbreviated ? "copyable-token abbreviated" : "copyable-token"}
      onClick={handleCopy}
      title={isAbbreviated ? "当前内容已被 Agent 缩略，无法复制完整地址或哈希" : "点击复制完整地址或哈希"}
      disabled={isAbbreviated}
    >
      <span className="copyable-token-value">{value}</span>
      <span className="copyable-token-action">{isAbbreviated ? "需完整" : copied ? "已复制" : "复制"}</span>
    </button>
  );
}

function isCopyableTokenValue(value: string): boolean {
  return /^0x[a-fA-F0-9]{64}$/.test(value)
    || /^0x[a-fA-F0-9]{40}$/.test(value)
    || /^0x[a-fA-F0-9]{4,16}\.\.\.[a-fA-F0-9]{4,16}$/.test(value);
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const inlineRegex = /(\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))|(https?:\/\/[^\s<>)\]]+)|(`([^`]+)`)|(0x[a-fA-F0-9]{64}|0x[a-fA-F0-9]{40}|0x[a-fA-F0-9]{4,16}\.\.\.[a-fA-F0-9]{4,16})|(\*\*(.+?)\*\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = inlineRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const markdownLinkText = match[2];
    const markdownLinkUrl = match[3];
    const bareUrl = match[4];
    const inlineCode = match[6];
    const token = match[7];
    const boldText = match[9];

    if (markdownLinkUrl) {
      nodes.push(
        <a
          className="inline-link"
          href={markdownLinkUrl}
          target="_blank"
          rel="noreferrer"
          key={`link-${match.index}`}
        >
          {markdownLinkText}
        </a>
      );
    } else if (bareUrl) {
      nodes.push(
        <a
          className="inline-link"
          href={bareUrl}
          target="_blank"
          rel="noreferrer"
          key={`url-${match.index}`}
        >
          {bareUrl}
        </a>
      );
    } else if (inlineCode) {
      if (isCopyableTokenValue(inlineCode)) {
        nodes.push(
          <CopyableToken
            value={inlineCode}
            isAbbreviated={inlineCode.includes("...")}
            key={`code-token-${match.index}`}
          />
        );
      } else {
        nodes.push(<code className="inline-code" key={`code-${match.index}`}>{inlineCode}</code>);
      }
    } else if (token) {
      nodes.push(
        <CopyableToken
          value={token}
          isAbbreviated={token.includes("...")}
          key={`token-${match.index}`}
        />
      );
    } else if (boldText) {
      nodes.push(<strong key={`bold-${match.index}`}>{boldText}</strong>);
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function isMarkdownTableSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function parseMarkdownTable(lines: string[], startIndex: number): {
  headers: string[];
  rows: string[][];
  nextIndex: number;
} | null {
  if (startIndex + 1 >= lines.length || !isMarkdownTableSeparator(lines[startIndex + 1])) {
    return null;
  }

  const splitRow = (line: string) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const headers = splitRow(lines[startIndex]);
  const rows: string[][] = [];
  let index = startIndex + 2;

  while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
    rows.push(splitRow(lines[index]));
    index += 1;
  }

  return { headers, rows, nextIndex: index };
}

function renderMarkdownTable(table: { headers: string[]; rows: string[][] }, key: string): ReactNode {
  return (
    <div className="markdown-table-wrap" key={key}>
      <table className="markdown-table">
        <thead>
          <tr>
            {table.headers.map((header, headerIndex) => (
              <th key={`head-${headerIndex}`}>{renderInlineMarkdown(header)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={`row-${rowIndex}`}>
              {table.headers.map((_header, cellIndex) => (
                <td key={`cell-${rowIndex}-${cellIndex}`}>
                  {renderInlineMarkdown(row[cellIndex] || "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderMarkdownContent(content: string): ReactNode[] {
  const lines = stripReasoningBlocks(content).split("\n");
  const nodes: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const language = trimmed.replace(/^```/, "").trim();
      const codeLines: string[] = [];
      index += 1;

      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }

      if (index < lines.length && lines[index].trim().startsWith("```")) {
        index += 1;
      }

      nodes.push(
        <pre className="markdown-code-block" key={`code-block-${index}`}>
          {language ? <span className="markdown-code-lang">{language}</span> : null}
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      const level = Math.min(headingMatch[1].length, 4);
      const headingContent = renderInlineMarkdown(headingMatch[2]);
      if (level === 1) {
        nodes.push(<h2 className="markdown-heading markdown-heading-1" key={`heading-${index}`}>{headingContent}</h2>);
      } else if (level === 2) {
        nodes.push(<h3 className="markdown-heading markdown-heading-2" key={`heading-${index}`}>{headingContent}</h3>);
      } else {
        nodes.push(<h4 className="markdown-heading markdown-heading-3" key={`heading-${index}`}>{headingContent}</h4>);
      }
      index += 1;
      continue;
    }

    const table = parseMarkdownTable(lines, index);
    if (table) {
      nodes.push(renderMarkdownTable(table, `table-${index}`));
      index = table.nextIndex;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ""));
        index += 1;
      }

      nodes.push(
        <ul className="markdown-list" key={`ul-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`li-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>
      );
      continue;
    }

    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+[.)]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+[.)]\s+/, ""));
        index += 1;
      }

      nodes.push(
        <ol className="markdown-list" key={`ol-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`oli-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>
      );
      continue;
    }

    nodes.push(<p key={`line-${index}`}>{renderInlineMarkdown(trimmed)}</p>);
    index += 1;
  }

  return nodes.length ? nodes : [<p key="empty">&nbsp;</p>];
}

function loadStoredUser(): UserResponse | null {
  const raw = window.localStorage.getItem(AUTH_USER_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserResponse;
  } catch {
    window.localStorage.removeItem(AUTH_USER_STORAGE_KEY);
    return null;
  }
}

function storeAuthState(auth: AuthTokenResponse): void {
  window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, auth.access_token);
  window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(auth.user));
}

function clearStoredAuthState(): void {
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(AUTH_USER_STORAGE_KEY);
}

export default function App() {
  const [authToken, setAuthToken] = useState(
    () => window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || ""
  );
  const [currentUser, setCurrentUser] = useState<UserResponse | null>(() => loadStoredUser());

  const [threadId, setThreadId] = useState(() => makeThreadId(loadStoredUser()?.username));
  const [memoryKey, setMemoryKey] = useState(() => makeMemoryKey(loadStoredUser()?.username));
  const [debug, setDebug] = useState(false);
  const [devMode, setDevMode] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: newId("welcome"),
      role: "assistant",
      content:
        "你好，我是 ChainCloud Agent 前端。你可以登录用户、绑定 memory_key，然后直接向后端 /chat 发送消息。"
    }
  ]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [sideLoading, setSideLoading] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [pendingPermission, setPendingPermission] = useState<PermissionRequest | null>(null);
  const [pendingClarification, setPendingClarification] = useState<ClarificationRequest | null>(null);
  const [pendingMonitorDraft, setPendingMonitorDraft] = useState<MonitorDraftRequest | null>(null);
  const [clarificationValues, setClarificationValues] = useState<Record<string, string>>({});
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const AUTO_SUMMARIZE_THRESHOLD = 8;

  const userPrefix = useMemo(
    () => sanitizeKeyPrefix(currentUser?.username),
    [currentUser]
  );

  const visibleMemories = useMemo(
    () => memories.filter((item) => memoryBelongsToCurrentUser(item, currentUser)),
    [memories, currentUser]
  );

  const activeMemoryKey = useMemo(() => {
    const key = memoryKey.trim();
    if (!key) return undefined;
    return visibleMemories.some((item) => item.memory_key === key) ? key : undefined;
  }, [memoryKey, visibleMemories]);

  const memoryKeyExists = Boolean(activeMemoryKey);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!authToken) return;

    getCurrentUser(authToken)
      .then((user) => {
        setCurrentUser(user);
        window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user));
      })
      .catch(() => {
        setAuthToken("");
        setCurrentUser(null);
        clearStoredAuthState();
      });
  }, [authToken]);

  async function refreshSideData() {
    setSideLoading(true);
    try {
      const [memoryRes, toolsRes] = await Promise.allSettled([
        getMemories(authToken),
        getTools()
      ]);

      if (memoryRes.status === "fulfilled") {
        const rawMemoryResponse = memoryRes.value as any;
        const memoryItems = Array.isArray(rawMemoryResponse)
          ? rawMemoryResponse
          : Array.isArray(rawMemoryResponse.memories)
            ? rawMemoryResponse.memories
            : Array.isArray(rawMemoryResponse.items)
              ? rawMemoryResponse.items
              : [];

        setMemories(memoryItems);
      }
      if (toolsRes.status === "fulfilled") {
        const rawTools = toolsRes.value.tools ?? toolsRes.value.registered_tools ?? [];
        setTools(rawTools);
      }
    } finally {
      setSideLoading(false);
    }
  }

  useEffect(() => {
    void refreshSideData();
  }, [authToken]);

  async function handleLoginSuccess(auth: AuthTokenResponse) {
    storeAuthState(auth);
    setAuthToken(auth.access_token);
    setCurrentUser(auth.user);
    setThreadId(makeThreadId(auth.user.username));
    setMemoryKey(makeMemoryKey(auth.user.username));
    setPendingPermission(null);
    setPendingClarification(null);
    setPendingMonitorDraft(null);
    setClarificationValues({});
    setMessages((prev) => [
      ...prev,
      {
        id: newId("system"),
        role: "system",
        content: `已登录用户：${auth.user.display_name || auth.user.username}`
      }
    ]);
  }

  function handleLogout() {
    clearStoredAuthState();
    setAuthToken("");
    setCurrentUser(null);
    setThreadId(makeThreadId());
    setMemoryKey(makeMemoryKey());
    setPendingPermission(null);
    setPendingClarification(null);
    setPendingMonitorDraft(null);
    setClarificationValues({});
    setMessages((prev) => [
      ...prev,
      { id: newId("system"), role: "system", content: "已退出登录，当前为未登录体验模式。" }
    ]);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || loading || pendingPermission || pendingClarification) return;

    const userMessage: ChatMessage = {
      id: newId("user"),
      role: "user",
      content: trimmed
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    const assistantId = newId("assistant-stream");
    let progressEvents: ExecutionProgressEvent[] = [];
    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "正在思考...", progress: [] }
    ]);

    try {
      await streamChat(
        {
          thread_id: threadId,
          message: trimmed,
          memory_key: activeMemoryKey,
          debug,
          trace_max_chars: 500
        },
        async (event) => {
          if (event.type === "error") throw new Error(event.message);
          if (isObservabilityEvent(event)) {
            const progressEvent: ExecutionProgressEvent = {
              ...event,
              timestamp: typeof event.timestamp === "string"
                ? event.timestamp
                : new Date().toISOString()
            };
            progressEvents = [...progressEvents, progressEvent];
            setMessages((prev) => prev.map((message) =>
              message.id === assistantId ? { ...message, progress: progressEvents } : message
            ));
          }
          if (event.type === "permission_required") {
            setPendingPermission(event);
            setMessages((prev) => prev.filter((item) => item.id !== assistantId));
          } else if (event.type === "clarification_required") {
            setPendingClarification(event);
            setClarificationValues({});
            setMessages((prev) => prev.filter((item) => item.id !== assistantId));
          } else if (event.type === "monitor_draft_required") {
            setPendingMonitorDraft(event);
            setMessages((prev) => prev.filter((item) => item.id !== assistantId));
          } else if (event.type === "status") {
            setMessages((prev) => prev.map((message) =>
              message.id === assistantId && message.content.endsWith("...")
                ? { ...message, content: event.content }
                : message
            ));
          } else if (event.type === "delta") {
            setMessages((prev) => prev.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: message.content.endsWith("...")
                      ? event.content
                      : message.content + event.content
                  }
                : message
            ));
            await nextPaint();
          } else if (event.type === "done") {
            const finalMessages = buildAssistantMessages(
              event.reply, event.trace, progressEvents, event.execution_trace
            );
            setMessages((prev) => {
              const index = prev.findIndex((message) => message.id === assistantId);
              if (index < 0) return [...prev, ...finalMessages];
              return [...prev.slice(0, index), ...finalMessages, ...prev.slice(index + 1)];
            });
          }
        },
        authToken
      );

      // 自动总结：当对话消息数（仅 user + assistant）超过阈值时触发
      const conversationCount = messages.filter(
        (m) => m.role === "user" || m.role === "assistant"
      ).length + 2; // +2 包括刚加入的 user 和 assistant
      if (conversationCount >= AUTO_SUMMARIZE_THRESHOLD && !summarizing && !activeMemoryKey) {
        void handleAutoSummarize();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev.filter((item) => item.id !== assistantId),
        { id: newId("error"), role: "error", content: message }
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handlePermissionDecision(decision: "approve" | "cancel") {
    if (!pendingPermission || loading) return;
    const permission = pendingPermission;
    setLoading(true);
    try {
      const response = await decidePermission(threadId, permission, decision, authToken);
      setPendingPermission(response.permission_required || null);
      setPendingClarification(response.clarification_required || null);
      if (response.clarification_required) setClarificationValues({});
      setMessages((prev) => [
        ...prev,
        ...(decision === "approve" && response.reply
          ? buildAssistantMessages(response.reply, response.trace)
          : decision === "cancel"
            ? [{ id: newId("system"), role: "system" as const, content: response.reply }]
            : [])
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { id: newId("error"), role: "error", content: `审批失败：${message}` }
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleMonitorDraftDecision(decision: "confirm" | "cancel") {
    if (!pendingMonitorDraft || loading) return;
    const draft = pendingMonitorDraft;
    setLoading(true);
    try {
      const response = await decideMonitorDraft(threadId, draft, decision, authToken);
      setPendingMonitorDraft(response.monitor_draft_required || null);
      if (response.reply) {
        setMessages((prev) => [...prev, ...buildAssistantMessages(response.reply, response.trace)]);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { id: newId("error"), role: "error", content: `监控任务操作失败：${message}` }
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleClarificationDecision(decision: "submit" | "cancel") {
    if (!pendingClarification || loading) return;
    const clarification = pendingClarification;
    setLoading(true);
    try {
      const response = await submitClarification(
        threadId, clarification, clarificationValues, decision, authToken
      );
      setPendingClarification(response.clarification_required || null);
      if (!response.clarification_required) setClarificationValues({});
      setMessages((prev) => [
        ...prev,
        ...(decision === "submit" && response.reply
          ? buildAssistantMessages(response.reply, response.trace)
          : decision === "cancel"
            ? [{ id: newId("system"), role: "system" as const, content: response.reply }]
            : [])
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { id: newId("error"), role: "error", content: `补充信息失败：${message}` }
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleAutoSummarize() {
    return handleManualSummarize(true);
  }

  async function handleManualSummarize(isAuto: boolean = false) {
    const key = memoryKey.trim() || `${userPrefix}-memory-${threadId}`;
    setSummarizing(true);
    try {
      const record = await summarizeMemory(
        {
          thread_id: threadId,
          memory_key: key,
          metadata: {
            source: "chaincloud-agent-web",
            username: currentUser?.username || null,
            user_id: currentUser?.user_id || null
          }
        },
        authToken
      );
      setMemoryKey(record.memory_key);
      await refreshSideData();
      setMessages((prev) => [
        ...prev,
        {
          id: newId("system"),
          role: "system",
          content: `${isAuto ? "已自动生成" : "已生成"}长期记忆：${record.memory_key}`
        }
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { id: newId("error"), role: "error", content: `${isAuto ? "自动总结" : "总结"}记忆失败：${message}` }
      ]);
    } finally {
      setSummarizing(false);
    }
  }

  function handleNewChat() {
    setThreadId(makeThreadId(currentUser?.username));
    setMemoryKey(makeMemoryKey(currentUser?.username));
    setPendingPermission(null);
    setPendingClarification(null);
    setClarificationValues({});
    setMessages([
      {
        id: newId("welcome"),
        role: "assistant",
        content: "已开启新会话。需要延续旧上下文时，可以在左侧选择已有 memory_key。"
      }
    ]);
  }

  if (!currentUser) {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <main className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <button
          className="sidebar-toggle"
          onClick={() => setSidebarCollapsed((prev) => !prev)}
          title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
        >
          {sidebarCollapsed ? "›" : "‹"}
        </button>

        <div className="brand">
          <div className="brand-logo">C</div>
          <div>
            <h1>ChainCloud Agent</h1>
            <p>Web Console</p>
          </div>
        </div>

        <div className="current-user-card sidebar-user-card">
          <div className="user-avatar">{currentUser.username.slice(0, 1).toUpperCase()}</div>
          <div className="user-meta">
            <strong>{currentUser.display_name || currentUser.username}</strong>
            <span>@{currentUser.username}</span>
          </div>
          <button className="ghost" onClick={handleLogout}>
            退出
          </button>
        </div>

        <button className="primary full" onClick={handleNewChat}>
          新建对话
        </button>

        <section className="panel">
          <div className="panel-title">会话配置</div>
          {devMode ? (
            <>
              <label>
                Thread ID
                <input value={threadId} onChange={(event) => setThreadId(event.target.value)} />
              </label>
              <label>
                Memory Key
                <input
                  placeholder="可手动输入或从下方选择"
                  value={memoryKey}
                  onChange={(event) => setMemoryKey(event.target.value)}
                />
              </label>
              <p className="hint">
                当前命名前缀：<code>{userPrefix}</code>
              </p>
            </>
          ) : null}
          <p className="hint">
            {memoryKey.trim()
              ? memoryKeyExists
                ? "当前 memory_key 已存在，后续聊天会携带这条长期记忆。"
                : "当前 memory_key 还未创建，首次聊天不会携带它；对话消息数达到阈值后会自动总结。"
              : "未绑定 memory_key，本次聊天只使用普通 thread 上下文。"}
          </p>
          <label className="checkbox-row">
            <input type="checkbox" checked={debug} onChange={(event) => setDebug(event.target.checked)} />
            返回 trace 调试信息
          </label>
          <label className="checkbox-row">
            <input type="checkbox" checked={devMode} onChange={(event) => setDevMode(event.target.checked)} />
            开发者模式
          </label>
        </section>

        <section className="panel">
          <div className="panel-row">
            <div className="panel-title">长期记忆</div>
            <button className="ghost" onClick={refreshSideData} disabled={sideLoading}>
              刷新
            </button>
          </div>
          <p className="hint">
            当前对话已 {messages.filter((m) => m.role === "user" || m.role === "assistant").length} 轮 ·
            {activeMemoryKey ? "已绑定记忆" : `达到 ${AUTO_SUMMARIZE_THRESHOLD} 轮自动总结`}
          </p>
          <button
            className="secondary full"
            onClick={() => void handleManualSummarize(false)}
            disabled={summarizing || messages.filter((m) => m.role === "user" || m.role === "assistant").length < 2}
            style={{ marginBottom: 8 }}
          >
            {summarizing ? "正在总结..." : "总结当前对话"}
          </button>
          <div className="memory-list">
            {visibleMemories.length === 0 ? <p className="empty">暂无当前用户的 memory 记录</p> : null}
            {visibleMemories.slice(0, 8).map((item) => (
              <button
                className={item.memory_key === memoryKey ? "memory-card active" : "memory-card"}
                key={item.memory_key}
                onClick={() => setMemoryKey(item.memory_key)}
              >
                <strong>{memoryTitle(item)}</strong>
                <span>{item.summary}</span>
                {devMode ? <small className="memory-key">{item.memory_key}</small> : null}
              </button>
            ))}
          </div>
        </section>

        <section className="panel compact">
          <div className="panel-title">工具状态</div>
          <details className="tool-details">
            <summary className="tool-summary">当前检测到 {tools.length} 个工具</summary>
            <div className="tool-list">
              {tools.length === 0 ? <p className="empty">暂无工具信息</p> : null}
              {tools.map((tool) => (
                <div className="tool-card" key={tool.name}>
                  <strong>{tool.name}</strong>
                  {tool.description ? <span>{tool.description}</span> : null}
                </div>
              ))}
            </div>
          </details>
        </section>
      </aside>

      <section className="chat-area">
        <header className="chat-header">
          <div>
            <h2>ChainCloud Agent</h2>
            {devMode ? (
              <p>
                thread: <code>{threadId}</code>
                {memoryKey ? (
                  <>
                    {" "}
                    · memory: <code>{memoryKey}</code>
                  </>
                ) : null}
              </p>
            ) : null}
          </div>
          <div className="header-actions">
            <span className="status user-status">用户：{currentUser.username}</span>
            {debug ? <span className="status debug">debug trace on</span> : null}
            {summarizing ? <span className="status">自动总结中...</span> : null}
          </div>
        </header>

        <div className="messages">
          {messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-role">{roleLabel(message.role)}</div>
              <div className={isChartContent(message.content) ? "message-bubble chart-bubble" : "message-bubble"}>
                {isImageContent(message.content) ? (
                  <img
                    src={extractImageUrl(message.content)}
                    alt="generated"
                    style={{ maxWidth: "100%", borderRadius: 12, display: "block" }}
                  />
                ) : isChartContent(message.content) ? (
                  <iframe
                    className="chart-frame"
                    src={extractChartUrl(message.content)}
                    title="chart"
                  />
                ) : (
                  renderMarkdownContent(message.content)
                )}
                <ExecutionTimeline message={message} devMode={devMode} />
                {message.trace?.length ? (
                  <details className="trace">
                    <summary>查看 trace</summary>
                    {message.trace.map((item, index) => (
                      <pre key={`${item.event}-${index}`}>{JSON.stringify(item, null, 2)}</pre>
                    ))}
                  </details>
                ) : null}
              </div>
            </article>
          ))}
          {pendingPermission ? (
            <article className="permission-card">
              <div className="permission-card-header">
                <strong>操作需要你的确认</strong>
                <span className={`risk-badge risk-${pendingPermission.risk_level}`}>
                  {pendingPermission.risk_level.toUpperCase()}
                </span>
              </div>
              <dl>
                <dt>为什么需要确认</dt>
                <dd>{pendingPermission.reason}</dd>
                <dt>具体要执行什么</dt>
                <dd>{pendingPermission.operation_summary}</dd>
                <dt>工具</dt>
                <dd>{pendingPermission.tool_name}</dd>
                <dt>预计影响</dt>
                <dd>{pendingPermission.estimated_impact}</dd>
              </dl>
              <div className="permission-actions">
                <button type="button" className="primary" disabled={loading} onClick={() => void handlePermissionDecision("approve")}>确认执行</button>
                <button type="button" disabled={loading} onClick={() => void handlePermissionDecision("cancel")}>取消</button>
              </div>
            </article>
          ) : null}
          {pendingMonitorDraft ? (
            <article className="permission-card monitor-draft-card">
              <div className="permission-card-header">
                <strong>监控任务预览</strong>
                <span className="draft-version">v{pendingMonitorDraft.version}</span>
              </div>
              <p>{pendingMonitorDraft.summary}</p>
              <dl>
                <dt>类型</dt><dd>{pendingMonitorDraft.draft.rule_type}</dd>
                <dt>协议</dt><dd>{pendingMonitorDraft.draft.protocol || "未限制"}</dd>
                <dt>链</dt><dd>{pendingMonitorDraft.draft.chain || "未限制"}</dd>
                <dt>地址</dt><dd>{pendingMonitorDraft.draft.address || "未限制"}</dd>
                <dt>Token</dt><dd>{pendingMonitorDraft.draft.token || "未限制"}</dd>
                <dt>金额阈值</dt>
                <dd>{pendingMonitorDraft.draft.min_amount_usd != null
                  ? `${pendingMonitorDraft.draft.min_amount_usd.toLocaleString()} USD`
                  : pendingMonitorDraft.draft.min_amount != null
                    ? pendingMonitorDraft.draft.min_amount.toLocaleString()
                    : "未设置"}</dd>
                <dt>通知方式</dt><dd>{pendingMonitorDraft.draft.notification_channel}</dd>
              </dl>
              {pendingMonitorDraft.missing_fields.length ? (
                <ul className="draft-errors">
                  {pendingMonitorDraft.missing_fields.map((item) => (
                    <li key={item.field}>{item.reason}</li>
                  ))}
                </ul>
              ) : null}
              <div className="permission-actions">
                <button type="button" className="primary" disabled={loading || !pendingMonitorDraft.can_confirm || !authToken} onClick={() => void handleMonitorDraftDecision("confirm")}>确认创建</button>
                <button type="button" disabled={loading} onClick={() => document.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus()}>修改</button>
                <button type="button" disabled={loading || !authToken} onClick={() => void handleMonitorDraftDecision("cancel")}>取消</button>
              </div>
              <small>修改时直接在下方输入框描述变更，未提及字段将保持不变。</small>
            </article>
          ) : null}
          {pendingClarification ? (
            <article className="permission-card clarification-card">
              <div className="permission-card-header">
                <strong>还需要你补充一些信息</strong>
              </div>
              <p>{pendingClarification.reason}</p>
              {pendingClarification.missing_state.map((item) => (
                <label className="clarification-field" key={item.field}>
                  <span>{item.question}</span>
                  <small>{item.reason}；格式：{item.expected_format}</small>
                  <input
                    value={clarificationValues[item.field] || ""}
                    onChange={(event) => setClarificationValues((prev) => ({
                      ...prev,
                      [item.field]: event.target.value
                    }))}
                  />
                </label>
              ))}
              <div className="permission-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={loading || pendingClarification.missing_state.some(
                    (item) => !(clarificationValues[item.field] || "").trim()
                  )}
                  onClick={() => void handleClarificationDecision("submit")}
                >继续</button>
                <button type="button" disabled={loading} onClick={() => void handleClarificationDecision("cancel")}>取消</button>
              </div>
            </article>
          ) : null}
          <div ref={bottomRef} />
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            placeholder="输入你的问题，例如：帮我分析这个地址的链上风险..."
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <button className="primary" disabled={
            loading || !input.trim() || Boolean(pendingPermission) || Boolean(pendingClarification)
          }>
            {loading ? "发送中..." : "发送"}
          </button>
        </form>
      </section>
    </main>
  );
}
