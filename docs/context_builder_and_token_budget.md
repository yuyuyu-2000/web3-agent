# ContextBuilder 与 Token Budget 实现说明

## 1. 配置

统一上下文预算通过以下环境变量控制：

| 配置 | 默认值 | 含义 |
| --- | ---: | --- |
| `MODEL_CONTEXT_WINDOW` | 128000 | 模型完整上下文窗口 |
| `MAX_INPUT_TOKENS` | 96000 | 单次模型调用允许的最大输入 |
| `RESERVED_OUTPUT_TOKENS` | 8000 | 为模型输出预留的 token |

初始化时会强制执行：

```text
effective_max_input_tokens = min(
    MAX_INPUT_TOKENS,
    MODEL_CONTEXT_WINDOW - RESERVED_OUTPUT_TOKENS
)
```

token 计数优先使用 `tiktoken`。模型没有已知 encoding 时使用
`cl100k_base`；运行环境不能加载 tokenizer 时，使用保守的 UTF-8
估算器，并在 trace 的 `token_counter` 字段注明 `estimate`。

## 2. 调用链

```text
POST /chat
  -> LangGraph AgentState / checkpoint
  -> router_node
       -> ContextBuilder.router -> decide_route -> model.invoke
  -> planner_node / replan_node
       -> ContextBuilder.planner -> create_plan -> model.invoke
  -> direct_agent_node
       -> ContextBuilder.executor(direct_executor) -> bound model.invoke
  -> executor_node
       -> ContextBuilder.executor(planned_executor) -> bound model.invoke
  -> compose_answer_node
       -> ContextBuilder.answer_composer -> model.astream
  -> reviewer_node
       -> ContextBuilder.reviewer -> model.invoke
  -> context audit 写入 AgentState.context_events
  -> debug.execution_trace.context_events
```

Evaluator 当前只消费一个结构化 `PlanStep + StepResult`，不读取增长的会话历史，
因此不属于本阶段要求的六类 ContextBuilder 场景。长期记忆总结使用独立、已有固定
输入上限的 Memory LLM；本阶段没有改变其显式总结和显式召回方式。

## 3. 场景上下文

| 场景 | 必需上下文 | 可裁剪上下文 |
| --- | --- | --- |
| router | 路由规则、当前请求、工具名称 | 最近 8 条非工具消息 |
| planner | 规划规则、当前目标、有限工具目录 | 最近 8 条非工具消息 |
| direct_executor | 静态系统规则、当前请求、权限边界 | 长期记忆、最近 12 条消息 |
| planned_executor | 静态系统规则、当前请求、当前 Step、成功标准、权限状态、当前步骤执行消息和必要证据 | 长期记忆、最近 12 条其他消息 |
| answer_composer | 回答规则、当前请求、执行摘要、当前任务工具证据 | 长期记忆、最新草稿 |
| reviewer | 审查规则、当前请求、执行摘要、回答草稿 | 无 |

Planner 的工具描述仍限制为每项最多 300 字符。Planned Executor 通过当前 Step
Prompt 只注入该步骤依赖的 `dependency_results`，不会无差别注入全部步骤结果。

## 4. 优先级和裁剪

优先级数字越小，越先获得预算：

1. System、安全和权限规则；
2. 当前用户请求；
3. 当前 Plan、Step、成功标准和关键执行状态；
4. 当前步骤必要工具证据；
5. 用户约束、`clarified_state` 和权限状态（包含在关键执行状态中）；
6. 显式长期记忆；
7. 最近对话或回答草稿；
8. 较早历史摘要预留接口。

1～5 类内容在相应场景中标记为 `protected`。如果受保护内容本身超过预算，
ContextBuilder 抛出 `ContextBudgetError`，不会静默删除安全规则、当前请求、权限边界
或必要证据。可选内容按优先级分配剩余预算；同一 recent history 类别优先保留较新消息。

Rolling Summary 尚未实现，但通用 `ContextPart(category="summary", priority=8)` 已可供
后续接入，无需修改预算算法。

## 5. Trace

每次构建生成一条 `context_events` 事件：

```json
{
  "type": "context_build",
  "scene": "planned_executor",
  "model": "gpt-4o-mini",
  "token_counter": "tiktoken",
  "model_context_window": 128000,
  "max_input_tokens": 96000,
  "reserved_output_tokens": 8000,
  "total_tokens": 4210,
  "category_tokens": {
    "system": 1800,
    "current_request": 80,
    "critical_state": 620,
    "dependency_evidence": 1300,
    "memory": 210,
    "recent_history": 200
  },
  "trimmed": [],
  "remaining_input_tokens": 91790
}
```

发生裁剪时，`trimmed` 会记录类别、原因和被排除内容的 token 数。请求摘要还提供
`context_builds` 和 `context_trimmed_items` 聚合指标。

## 6. 超预算裁剪示例

假设：

```text
model_context_window = 100
reserved_output_tokens = 20
max_input_tokens = 80
```

候选上下文为：

| 类别 | token | 属性 |
| --- | ---: | --- |
| system | 20 | protected |
| current_request | 10 | protected |
| current_step | 15 | protected |
| dependency_evidence | 20 | protected |
| memory | 10 | optional，优先级 6 |
| recent_history | 20 | optional，优先级 7 |
| summary | 15 | optional，优先级 8 |

受保护内容使用 65 token，只剩 15 token。构建结果会保留全部受保护内容，优先加入
10-token memory，然后仅在剩余 5-token 能容纳完整消息时加入 recent history；summary
最后考虑。无法容纳的消息记录为：

```json
{
  "category": "recent_history",
  "reason": "token_budget_exceeded",
  "tokens": 12
}
```

最终输入不超过 80 token，同时保留 20 token 输出空间。裁剪以完整消息为边界，
不会截断半条消息或生成不完整 ToolMessage；Planned Executor 的当前工具调用/结果对
被作为受保护的当前步骤执行上下文整体处理。

## 7. 本阶段边界

本实现没有加入：Rolling Summary、工具结果外部存储、Memory 自动召回或压缩质量
Evaluator。相关能力可分别接入 `summary`、`evidence`、`memory` ContextPart，而无需改变
现有场景入口和 token budget 核心。

## 8. Tool Result Compression

工具结果现在采用三层生命周期：

```text
tool.invoke(args)
  -> Raw Tool Result（完整内容写入 Result Store）
  -> Structured Facts（确定性提取高信号字段、数量、样例和标识）
  -> Context Summary（短摘要、preview、证据等级、result_id）
  -> ToolMessage / AgentState / StepResult
  -> ContextBuilder 按场景选择原文或摘要
```

### Raw Tool Result

默认保存在 `TOOL_RESULT_STORE_PATH=tool_results` 下，每个结果对应一个不可变 JSON
文件。文件包含完整返回、工具名称、工具参数、创建时间和证据来源。AgentState 中的
`tool_result_records` 保存 `result_id`、文件位置、SHA-256 和大小等索引；debug trace
会返回经过敏感字段脱敏的索引。`FileToolResultStore.read(result_id)` 可重新读取原文。

### Structured Facts

确定性提取器从 JSON 中保留：状态、标量字段、列表数量、字段名和最多两个有界样例。
文本和嵌套内容有独立长度/深度边界，防止“结构化 facts”本身再次膨胀。Planned
步骤完成时，这些 facts 与原始结果引用写入 `StepResult.structured_facts` 和
`StepResult.result_references`。

### Context Summary

当结果大于 `TOOL_RESULT_COMPRESSION_THRESHOLD_BYTES`（默认 16000 bytes），成功的
活跃 ToolMessage 会替换为短 JSON，其中包含工具名、状态、facts、result_id、reference、
必要 preview 和 evidence level。默认 preview 由 `TOOL_RESULT_PREVIEW_CHARS=1000` 控制。
小结果在当前调用链中仍保留原文，但同样持久化 Raw 并附带三层元数据。

错误 ToolMessage 不进行破坏性内容压缩，因此 retryable、permission error、fallback、
unresolved error 和 critical failure 判断仍读取原始错误载荷。

### 旧结果的 micro compact

ContextBuilder 不使用固定“最近 3 条”规则：

- Planned Executor 把当前步骤自 `step_message_start` 起的执行消息视为必要证据；
- 当前步骤依赖结果已经以 StepResult facts/reference 注入步骤 Prompt；
- 非当前步骤 ToolMessage 在模型视图中替换为 context summary/reference；
- Direct Executor 保留当前用户请求之后产生的工具结果，压缩此前请求的旧结果；
- Answer Composer 只选择当前请求之后的工具证据，并统一使用 summary/facts；
- 原 checkpoint 中的小 ToolMessage 可以继续用于审计，压缩发生在活跃模型视图；
- 已超过阈值的大结果在进入 checkpoint 前已经替换为摘要，Raw 始终可追溯。

所有工具摘要仍作为 ContextPart 进入统一 Token Budget。即使摘要总量过多，也会受到
场景优先级和总输入预算约束。

## 9. Rolling Summary

Rolling Summary 是当前 `thread_id` 的活跃上下文压缩，不是长期 Memory。长期 Memory
仍由 `memory_key`、MemoryStore 和显式召回管理；Rolling Summary 存在 LangGraph
AgentState/checkpoint 中，只负责让同一线程长期运行时不再向模型重复发送全部历史。

### 状态字段

| 字段 | 用途 |
| --- | --- |
| `conversation_summary` | task-aware JSON 线程摘要 |
| `summarized_message_ids` | 已覆盖消息的稳定 ID/指纹 |
| `summarized_until` | 已覆盖的 append-only 消息前缀游标 |
| `summary_version` | 每次成功压缩递增 |
| `summary_updated_at` | 最近成功更新时间 |
| `compact_failure_count` | 连续失败次数 |
| `compact_events` | proactive/reactive 压缩 trace |

完整 `messages` 不会被删除或替换，仍由 checkpoint 保存。模型活跃视图从
`messages[summarized_until:]` 开始，并组合 `conversation_summary`。

### 自动触发规则

每次请求在 Router 之前检查：

```text
active_message_tokens >= MAX_INPUT_TOKENS * ROLLING_SUMMARY_TRIGGER_RATIO
```

默认比例为 0.70。此外，如果活跃消息加预计静态上下文达到输入上限的 90%，也会触发。
判断使用与 ContextBuilder 相同的 token counter，不使用字符数。

相关配置：

| 配置 | 默认值 |
| --- | ---: |
| `ROLLING_SUMMARY_TRIGGER_RATIO` | 0.70 |
| `ROLLING_SUMMARY_RECENT_MESSAGES` | 12 |
| `ROLLING_SUMMARY_REACTIVE_RECENT_MESSAGES` | 4 |
| `ROLLING_SUMMARY_MAX_INPUT_TOKENS` | 32000 |
| `ROLLING_SUMMARY_MAX_FAILURES` | 3 |

一次 proactive compact 最多发起一次摘要调用并推进一个安全前缀批次，避免单次请求产生
无界摘要调用。后续请求仍超过阈值时会继续滚动推进。

### Task-aware schema

摘要必须输出以下 JSON 字段：

```text
current_goal
confirmed_user_constraints
important_entities
important_numbers
current_plan
completed_steps
pending_steps
important_tool_findings
failed_attempts
unresolved_errors
permissions_approvals
clarified_state
decisions_made
open_questions
```

摘要输入同时包含旧 summary、待压缩消息以及当前 Plan、StepResult、错误、权限和
clarified state。Prompt 明确禁止编造并要求完整保留地址、交易哈希、时间范围和数字。

### 保留与归档边界

正常 proactive compact：

- 保留最近 12 条原始消息；
- Planned Step 正在执行时，不越过 `step_message_start` 压缩当前步骤证据；
- 较早安全前缀只进入 `conversation_summary`，原文仍在 checkpoint；
- Tool Result Raw 文件、Structured Facts 和引用继续独立保留。

reactive compact 更激进，通常只保留最近 4 条，但同样不会越过当前执行步骤的安全边界。
ContextBuilder 的模型输入顺序为 System、长期 Memory、Rolling Summary、recent messages、
当前请求/执行状态/必要 evidence；实际可选内容仍受统一优先级预算控制。
其中 summary 内的目标、用户约束、实体、重要数字、权限、澄清状态和未解决错误会拆成
受保护的 `summary_constraints`（优先级 5）；其余较早历史摘要保持优先级 8，可以在
极端预算压力下被裁剪。

### Reactive Compact

Router、Planner/Replan、Direct Executor、Planned Executor、Answer Composer 和 Reviewer
遇到以下错误时触发：

```text
context_length_exceeded
maximum context length
context window
prompt too long
too many tokens
```

流程为：

```text
第一次模型请求失败
  -> reactive compact
  -> 使用新 summary + 更小 recent window 重建 ContextBuilder
  -> 重试一次
  -> 再失败则向上抛出，不继续重试
```

### 失败保护

摘要调用或 JSON/schema 校验失败时：

- 不返回新的 `conversation_summary`，因此旧摘要不会被覆盖；
- 不修改或删除 `messages`；
- 增加 `compact_failure_count`；
- 写入失败 compact event；
- 连续失败达到默认 3 次后打开熔断器，不再调用摘要模型；
- 任意一次成功会把失败计数归零。

### Token 对比示例

使用本地保守 token counter 测试 100 条较长中文历史消息：

```text
compact 前活跃历史：83,702 token
task-aware summary + 最近 12 条：10,180 token
下降：87.8%
```

这只是可重复的合成样例，实际降幅由消息长度、摘要内容、工具结果压缩比例和模型
tokenizer 决定。每次真实压缩的 `active_tokens_before`、`active_tokens_after`、覆盖范围、
版本和触发模式都会写入 `compact_events`。
