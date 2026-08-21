# multi_001 / multi_003 工具完成后 180 秒 Timeout 性能根因分析

## 核心结论

`multi_001` 和 `multi_003` 的 180 秒 timeout 不是数据库或 TRON 工具本身造成的，也不能简单概括为“模型慢”。最新最终回归中，两项任务都在前 45 秒内完成了两个必要工具：

- `multi_001`：约 30.75 秒时两个工具全部完成，之后约 149.26 秒仍未返回最终响应；
- `multi_003`：约 44.47 秒时两个工具全部完成，之后约 135.53 秒仍未返回最终响应。

剩余时间消耗在 planned 路径的 LLM 质量控制链：TRON tool-result summarization、Step Evaluator、Answer Composer、Reviewer，以及这些节点之间对同一证据的重复编码和长输出生成。

最关键的上下文问题是：TRON raw result 约 8.36 KB，低于当前 16 KB 压缩阈值，因此原始 ToolMessage 被标记为 `compressed=false`。当前步骤的 Executor 在消费工具结果时，又通过 `dependency_evidence` 直接收到原始 ToolMessage；这条通道没有调用 `tool_message_for_context(..., compact_old=True)`，所以当前 step 的 tool-result summarization 会看到完整 transaction / receipt / logs / internal transactions。

后续 Evaluator、Composer、Reviewer通常不再逐字收到完整 raw JSON，但会反复收到它的重叠派生表示：

- Executor 生成的长摘要；
- `StepResult.evidence` 中最多 2000 字符的 raw 前缀；
- `structured_facts`；
- `context_summary` 及 preview；
- Answer Composer 的 execution summary、工具证据和 draft；
- Reviewer 的 execution summary、完整 answer draft。

因此，问题不是“压缩完全没工作”，而是“完整 raw 在最昂贵的即时 summarization 节点仍然存在，后续又以多种重叠表示重复进入多个 LLM 节点”。当前 token budget 只防止超上下文，并不针对 latency；这些调用的输入远低于 96K 上限，rolling summary 不会触发。

## 证据完整性与观测限制

### 最新最终回归

最新最终回归是 `run_20260821T024122Z`。当时评测文件曾记录：

- `multi_001`：`180003.756 ms`，`error=timed out`；
- `multi_003`：`180002.002 ms`，`error=timed out`；
- 两项 observation 均为 `execution_trace={}`。

timeout adapter 的异常分支只返回 `status=failed` 和 `error=timed out`，不保留服务端已经产生的 node events、context events、plan、StepResult 或 usage metadata。

### Checkpoint 无法补回

本次服务没有配置 `DATABASE_URL`，运行时使用 `MemorySaver`。评测服务停止后内存 checkpoint 消失，因此无法从服务端 checkpoint 恢复 timeout 请求的逐节点 state。

### 可用的三类证据

本报告使用：

1. 最新最终回归留下的四个持久化工具结果及其 `created_at`；
2. 最新回归中成功的 `multi_002` request start/duration，用于确定 `multi_003` 请求边界；
3. 同一 Planner 优化版本的上一轮 `multi_001` 完整成功 trace。该 trace 保留了 Planner、每次 Executor、每次 Evaluator、Composer、Reviewer 的准确开始时间和 duration，以及 Planner/Executor/Composer/Reviewer 的 context audit。它在补强最终 prompt 前仍多规划了一个重复 TRON step，所以不能冒充最终 timeout run，但能直接证明 planned LLM 链的节点级耗时形态。

### 无法伪造的字段

最新 timeout run 的逐次 LLM start/duration/input/output tokens已经丢失。即使上一轮完整 trace也没有保存每次 LLM 的 output tokens：

- `node_events` 保存节点开始时间和 duration；
- `context_events` 保存 ContextBuilder 估算的 input tokens，但 Step Evaluator没有 ContextBuilder audit；
- `request_summary` 只保存聚合 token usage；
- Planner/Evaluator/Reviewer 的响应不都作为普通 message 写入 `state.messages`，所以聚合 usage 也不是所有调用的可靠逐项拆分。

因此下文对每次调用明确标注“直接可证”“同版本完整 trace 代理”或“N/A”。把 timeout observation 中的空 trace解释成 0 calls / 0 tokens 是错误的。

## 最新最终回归：工具完成时间与 Post-tool 时间

### multi_001

最终回归 `multi_001` 的两个后台结果：

| 工具 | 持久化完成时间（UTC） | 结果大小/作用 |
|---|---|---|
| `postgres_select` | 2026-08-21 02:34:44.278 | 直接查询 `public.justlend` 最大 `amount_usd` 事件 |
| `get_tron_transaction` | 2026-08-21 02:34:59.849 | 返回对应 TRON transaction 与 transaction_info |

下一个 case `multi_002` 的 thread ID 内嵌开始时间为 `2026-08-21 02:37:29.106 UTC`。由于 `multi_001` 客户端耗时为 180.004 秒且紧接着开始下一项，可反推出：

- `multi_001` 约在 `02:34:29.102 UTC` 开始；
- PostgreSQL 结果在开始后约 15.18 秒持久化；
- TRON 结果在开始后约 30.75 秒持久化；
- 两个必要工具全部完成时累计耗时约 30.75 秒；
- 从工具全部完成到客户端 timeout 约 149.26 秒；
- post-tool 阶段占端到端 timeout 窗口约 82.9%。

这里的“工具完成时间”采用 tool result store 的 `created_at`，它位于工具返回并完成结果处理之后，适合作为保守的完成边界。

### multi_003

最终回归 `multi_002` 的请求：

- 开始时间：2026-08-21 02:37:29.106 UTC；
- 客户端 latency：53.877 秒；
- 因此 `multi_003` 约在 `02:38:22.983 UTC` 开始。

`multi_003` 的后台结果：

| 工具 | 持久化完成时间（UTC） | 结果大小/作用 |
|---|---|---|
| `postgres_select` | 2026-08-21 02:38:51.355 | 查询 `public.justlend` 最大事件 |
| `get_tron_transaction` | 2026-08-21 02:39:07.454 | 返回对应 TRON transaction 与 transaction_info |

由此得到：

- PostgreSQL 结果在开始后约 28.37 秒持久化；
- TRON 结果在开始后约 44.47 秒持久化；
- 两个必要工具全部完成时累计耗时约 44.47 秒；
- 从工具全部完成到客户端 timeout 约 135.53 秒；
- post-tool 阶段占端到端 timeout 窗口约 75.3%。

### 直接回答

| 问题 | multi_001 | multi_003 |
|---|---:|---:|
| 工具全部完成时累计耗时 | 约 30.75s | 约 44.47s |
| 工具完成后到 timeout | 约 149.26s | 约 135.53s |
| post-tool 占 180s 比例 | 82.9% | 75.3% |

数据库和 TRON 网络调用不是 180 秒的主导项。即使把工具耗时降到零，按本次路径仍会留下约 136～149 秒的 LLM/编排尾部。

## 同版本完整 multi_001 LLM Execution Timeline

下面是上一轮 `multi_001` 成功 trace，时间均为 UTC。它与最终代码的差别只有之后补强的一条通用 Planner 规则；该轮仍产生第三个重复 TRON step。它是当前唯一完整保存的同版本节点级时间线。

### Timeline 总览

| 顺序 | LLM call | 开始时间 | Duration | ContextBuilder input tokens | Output tokens | 主要输入组成 | 完整 TRON raw？ |
|---:|---|---|---:|---:|---:|---|---|
| 1 | Planner | 02:25:26.163 | 23.302s | 2,401 | N/A | Planner rules 1,071；current request/tool catalog 1,332 | 否，尚未调用工具 |
| 2 | Executor step_1 tool-call generation | 02:25:49.469 | 2.712s | 6,937 | N/A | Executor system 6,583；request 58；step critical state 300 | 否 |
| 3 | Executor step_1 tool-result summarization | 02:25:52.203 | 5.424s | 7,293 | N/A | 上述内容 + PostgreSQL raw dependency evidence 358 | 否，仅数据库行 |
| 4 | Step Evaluator step_1 | 02:25:57.631 | 2.723s | N/A | N/A | step definition + candidate StepResult；数据库摘要、最多 2K evidence、structured facts | 否 |
| 5 | Executor step_2 tool-call generation | 02:26:00.359 | 2.081s | 9,452 | N/A | system 6,583；request 58；critical state 1,613；recent history 1,202 | 否；只有上一步数据库 StepResult |
| — | TRON Tool | 02:26:02.443 | 4.161s | — | — | txid | 工具产生 raw |
| 6 | Executor step_2 tool-result summarization | 02:26:06.606 | 29.360s | 13,086 | N/A | system 6,583；critical state 1,613；recent history 1,202；dependency evidence 3,636 | 是，当前 step 的原始 TRON ToolMessage |
| 7 | Step Evaluator step_2 | 02:26:35.972 | 4.749s | N/A | N/A | step definition + candidate StepResult；Executor 长摘要 + 2K raw evidence + structured facts + refs | 否完整 raw；有重叠派生内容 |
| 8 | Executor step_3 duplicate tool-call generation | 02:26:40.727 | 11.461s | 13,820 | N/A | system 6,583；critical state 4,214；recent history 2,969 | 否完整 raw；包含前序 StepResult 和长摘要 |
| — | Duplicate TRON Tool | 02:26:52.191 | 2.666s | — | — | 同一 txid | 再次产生相同 raw |
| 9 | Executor step_3 tool-result summarization | 02:26:54.859 | 45.053s | 17,451 | N/A | system 6,583；critical state 4,214；recent history 2,969；dependency evidence 3,633 | 是，第二份完整 TRON ToolMessage |
| 10 | Step Evaluator step_3 | 02:27:39.915 | 13.099s | N/A | N/A | 第三步定义 + 更长 candidate StepResult | 否完整 raw；有摘要/evidence/facts 重复 |
| 11 | Answer Composer | 02:27:53.019 | 30.537s | 未在现存摘录中保留逐项值 | N/A | request + execution summary + 所有工具 evidence summary + 最后 Executor draft | 否完整 raw；多份重叠摘要 |
| 12 | Reviewer | 02:28:23.557 | 2.532s | 未在现存摘录中保留逐项值 | N/A | user request + 完整 answer draft + execution summary | 否完整 raw；再次包含 StepResults |

该请求的聚合记录为：

- 12 次 LLM calls；
- 3 次 tool calls；
- 82,204 aggregate input tokens；
- 13,314 aggregate output tokens；
- 95,518 total tokens；
- 179.934 秒服务端 duration；
- 179.953 秒客户端 latency。

必须强调：82,204/13,314 是 request summary 聚合值，不能准确拆到上述每一次调用。现有 trace schema没有为每个 node event保存 `usage_metadata`；Evaluator 也没有 context audit。所以上表 output tokens 必须为 N/A。

### 输入 token 的增长轨迹

可观测的 Executor input 从：

```text
6,937 → 7,293 → 9,452 → 13,086 → 13,820 → 17,451
```

这不是单纯由系统 prompt 固定成本导致：

- 每次 Executor 都重复约 6,583 tokens 的完整 Executor system prompt；
- dependency StepResult 进入 `critical_state`，从 300 增长到 1,613，再到 4,214 tokens；
- recent history 从 0 增长到 1,202，再到 2,969 tokens；
- TRON raw 作为当前 step dependency evidence 增加约 3,633～3,636 tokens；
- 重复 TRON step 令相同链上证据再经历一次 tool generation、raw summarization 和 Evaluator。

最终 prompt 已消除第三个重复工具调用，但仍保留 step_2 的 TRON raw summarization、Evaluator、Composer 和 Reviewer，所以 post-tool latency 仍可能超过 135 秒。

## 最大的 3 个 LLM latency 节点

### 唯一完整同版本 trace 中的 Top 3

| 排名 | 节点 | Duration | 证据解释 |
|---:|---|---:|---|
| 1 | Executor step_3 duplicate TRON result summarization | 45.053s | 17,451 input tokens；包含第二份完整 TRON raw；还生成长整合摘要 |
| 2 | Answer Composer | 30.537s | 同时消费 execution summary、工具 evidence summary 和 Executor draft，再生成完整最终报告 |
| 3 | Executor step_2 necessary TRON result summarization | 29.360s | 13,086 input tokens；包含完整 transaction / receipt / logs / internal transactions |

Planner 为 23.302 秒，排名第四；第三个 Step Evaluator为 13.099 秒。

### 对最终两工具路径的解释

最终两工具路径已经删除排名第一的“重复 step_3 summarization”。因此最终 timeout 的最大三项无法从丢失 trace精确排序，但基于仍存在的语义链，候选主导项是：

1. 必要 TRON tool-result summarization；
2. Answer Composer；
3. Planner、TRON Step Evaluator 或 Reviewer中的一个，取决于当次模型生成与排队。

不能把上一轮的 45.053/30.537/29.360 秒直接宣称为最终 timeout run 的精确 Top 3；这些是同版本完整 trace 的实测证据。最终 run 的工具后 135～149 秒证明，删除重复 step 后其余节点仍能占满超时窗口。

## TRON Raw Result 到底被送进了哪些节点

### Executor：是，完整 raw

`process_tool_result()` 只有在：

```text
raw.size_bytes > TOOL_RESULT_COMPRESSION_THRESHOLD_BYTES
```

时才把 ToolMessage content 替换为 context summary。默认 threshold 为 16,000 bytes。

最终两个 TRON raw 文件大小约为 8,360 bytes，低于 threshold，因此：

```text
compressed = false
ToolMessage.content = raw_content
```

Planned Executor 构造 `recent_history` 时会压缩旧 ToolMessage，但另行构造的 `active_evidence` 是当前 step 开始后的原消息，并直接作为 `dependency_evidence` 传入 ContextBuilder。该列表未经过 `tool_message_for_context()`，所以消费 TRON 工具结果的 Executor summarization得到完整 raw JSON，包括：

- transaction.raw_data；
- signature；
- raw_data_hex；
- transaction ret；
- transaction_info receipt；
- log；
- internal_transactions。

这也是同版本 trace 中 dependency evidence 约 3.6K tokens、Executor summarization分别达到 29.36 秒和 45.05 秒的直接原因。

### Step Evaluator：不是完整 raw，但有大量重叠内容

`complete_step_node()` 构造 StepResult 时写入：

- Executor summary；
- 每个 ToolMessage 前 2000 字符作为 `evidence`；
- `structured_facts`；
- result references；
- tool call names。

Evaluator prompt 会把整个 `PlanStep` 和整个 candidate `StepResult` JSON 序列化。因此它通常不收到完整 8.36 KB raw，但收到：

- 可能已经非常详细的 Executor 链上摘要；
- raw JSON 的前 2000 字符；
- `structured_facts` 中的 txid、raw_data_hex 截断、字段列表、区块/费用等 scalar facts；
- result reference。

这足以重复编码大部分高层语义。Evaluator只需要判断 success criteria，实际却消费了接近作答级别的证据包。

### Answer Composer：不是完整 raw，但同一证据出现三次

Composer context 包含：

1. `execution_summary`：含完整 plan 和所有 StepResults；
2. `evidence`：每个 ToolMessage 经过 `tool_message_for_context(compact_old=True)` 得到 context summary；
3. `draft`：最后一个非 tool-call Executor AI summary。

对于 TRON 证据：

- execution summary 已含 Executor summary、2K evidence、structured facts；
- evidence context又含 context summary、key facts和最多 300 字符 preview；
- draft再次自然语言复述 transaction/receipt/log/internal transaction 结论。

所以不是一份完整 raw 被逐字送三次，而是同一事实以结构化 JSON、摘要 JSON和自然语言 draft 三种形式重叠输入。Composer再把这些内容生成一遍完整最终答案。

### Reviewer：不是完整 raw，但再次重复执行摘要与答案

Reviewer payload包含：

- user request；
- Answer Composer 的完整 answer draft；
- execution summary。

execution summary中又包含所有 StepResults。Reviewer因此同时看到“最终答案中的链上事实”和“StepResults中的相同链上事实”，再进行一次完整性/证据审查。

### 直接答案

| 节点 | 完整 TRON raw | 重叠派生内容 |
|---|---|---|
| Executor tool-call generation | 否 | 上一步数据库 StepResult |
| Executor TRON result summarization | 是 | raw + 旧 history/context |
| Step Evaluator | 否 | Executor summary + raw 前2K + structured facts |
| Answer Composer | 否 | execution summary + context summary + draft，三路重复 |
| Reviewer | 否 | 完整 answer + execution summary，两路重复 |

所以“TRON raw 是否被重复送入所有节点”的精确回答是：完整 raw 主要直接进入即时 Executor summarization，并非逐字进入每个后续节点；但其内容被转换成多个重叠表示，在 Evaluator、Composer、Reviewer中重复传输和再生成。上一轮额外 step 还让完整 raw 被工具重新获取并再次完整送入第二次 Executor summarization；最终 prompt 已消除这一层重复。

## structured facts / context summary 为什么没有充分降低上下文

### 1. 8.36 KB 小于 16 KB，初始压缩根本未触发

当前机制按 byte threshold 判断，不按字段复杂度、日志数量、预计 tokens或下游节点用途判断。TRON JSON 虽不足 16 KB，但包含高熵 hex、签名、logs和 internal transactions，对模型处理成本并不低。

### 2. 当前 step dependency evidence 绕开压缩

`planned_executor` 对 history中的 ToolMessage会 compact，但 `active_evidence` 从当前 step消息切片后直接注入 `dependency_evidence`。最需要压缩的“刚返回的 TRON raw”恰好走了未压缩通道。

### 3. structured facts 不是最小链上摘要

通用 `extract_structured_facts()` 对 dict：

- 保留顶层 scalar；
- 保留嵌套 dict 的字段列表；
- 保留嵌套 scalar values；
- 字符串最多截到 500 字符。

因此 `raw_data_hex` 仍可能保留 500 字符；`transaction_info_fields` 会列出 receipt/log/internal_transactions，但不会提炼 success、energy、核心 transfer等真正针对任务的最小事实。这种通用 facts既丢掉部分可解释细节，又保留不少高 token、低语义密度内容，迫使 Executor从 raw自行总结。

### 4. StepResult evidence 固定截 raw 前 2000 字符

截取“前 2000 字符”不是语义压缩。TRON JSON前部通常含 raw_data、signature、hex input；真正关键的 receipt result、block、fee、log摘要可能更靠后。它既重复 raw，又未必覆盖最有用证据。

### 5. Composer 同时消费 summary、facts和 draft

context summary没有替换其他表示，只是新增了一条证据通道。execution summary、evidence summary、Executor draft同时存在，导致压缩后的内容与未压缩派生摘要叠加，而不是单一 canonical evidence view。

### 6. Reviewer 再次获得 execution summary

Reviewer已有最终 answer，却又收到完整 execution summary。对于成功、低风险、证据一致的 planned case，这会重复大部分事实。

### 7. Rolling summary 触发条件与本问题不匹配

系统最大 input budget约 96K，rolling summary主要在接近 90% 容量时触发。同版本 Executor输入最高 17,451 tokens，Composer也远未接近 86K 左右触发线。因此：

- 没有 context overflow；
- ContextBuilder不会主动丢弃 protected evidence；
- rolling summary不会触发；
- 但 13K～17K input + 长 output 已足以产生数十秒 latency。

当前机制优化的是“能否放进上下文”，不是“是否以最低 latency完成任务”。

### 8. Output 长度是独立成本

同版本请求总 output tokens为 13,314。虽然无法逐节点拆分，但 Executor summaries和Answer Composer都会生成长篇报告式文本；Evaluator/Reviewer也调用模型。即使 input压缩，多个节点重复生成相同事实仍会产生显著 decode latency。

## 哪些 LLM Calls 语义必要，哪些可合并或条件执行

以下只做分析，不提出本轮代码修改。

### 语义上必要

| 调用 | 必要性 | 原因 |
|---|---|---|
| Planner | 有条件必要 | 用户要求跨数据库与链上动态依赖；需要明确 `tx_hash → txid` 顺序。但对已知固定两步模板，未必每次都需要高成本自由生成。 |
| Executor step_1 tool-call generation | 必要 | 需要生成目标 SQL和选择字段。 |
| Executor step_2 tool-call generation | 必要 | 需要从 dependency取 tx_hash并调用TRON工具。 |
| 一次最终自然语言生成 | 必要 | 用户要求比较数据库与链上结果、说明来源和覆盖边界。 |

### 可以显著缩短或结构化替代

| 调用 | 空间 | 依据 |
|---|---|---|
| Executor step_1 tool-result summarization | 可短路/结构化 | PostgreSQL结果已被 deterministic structured facts提取；只需形成依赖字段和简短 StepResult，不需要报告式摘要。 |
| Executor step_2 TRON raw summarization | 必要取证，但不必消费完整 raw或生成长报告 | 可由工具层先生成任务相关 canonical receipt facts，再由单个最终生成节点解释。 |
| Step Evaluator step_1 | 可规则化或跳过 | SQL成功、row_count=1、tx_hash格式有效时，success criteria可由确定性检查完成。 |
| Step Evaluator step_2 | 可条件执行 | txid匹配、transaction/receipt存在、关键字段齐全时可规则判定；只有工具失败、结果冲突或证据缺失时调用LLM。 |
| Answer Composer | 必要，但应是唯一报告式生成 | 当前已经承担最终作答，应避免上游Executor先生成同等详细的报告。 |
| Reviewer | 可条件执行或轻量化 | 当前 planned无条件review。若只读、工具成功、无冲突、Composer引用结构化证据，可跳过或使用确定性审查；风险/冲突/低置信度时再调用。 |

### 可合并的语义链

当前链路为：

```text
TRON raw
→ Executor 长摘要
→ Evaluator 审摘要
→ Composer 再写最终答案
→ Reviewer 再审最终答案和StepResults
```

语义上最有合并空间的是：

```text
TRON raw
→ deterministic canonical facts
→ 一次 Answer Composer 最终生成
→ 仅在风险/冲突时 Reviewer
```

这不是取消证据质量，而是把“事实提取/完整性检查”尽可能交给确定性逻辑，把报告式自然语言生成集中到一个节点。

### 不应删除

- 两个工具之间的 dependency boundary；
- Permission Gate，即使只读时开销极小；
- 工具失败、schema mismatch、txid不一致、链上查无结果时的 recovery/evaluation；
- 高风险、证据冲突或模型推断较多时的 Reviewer。

## 为什么最终仍达到 180 秒

综合节点和上下文证据，最终 timeout 的形成机制是：

1. Planner本身可能消耗数秒到二十余秒；同版本完整 trace为 23.30 秒。
2. 前两个必要工具在 30.75～44.47 秒内完成。
3. TRON result低于byte threshold，当前 step Executor直接消费完整raw。
4. Executor把raw转换成长自然语言StepResult；同版本必要TRON summarization实测29.36秒。
5. Step Evaluator重新读取摘要、raw前2K和structured facts；完整trace中为4.75秒，复杂重复step曾达13.10秒。
6. Composer同时读取execution summary、context summary和draft，再生成完整答案；同版本实测30.54秒。
7. Planned模式无条件进入Reviewer；Reviewer再次读取完整答案和execution summary。
8. 每个节点都可能有模型端queue/prefill/decode波动。即使工具路径固定为两步，剩余节点串行执行，没有全局deadline感知或基于剩余时间的条件降级。
9. 180秒客户端deadline到达后，adapter丢弃全部部分trace，使“工具早已完成、卡在后处理”的事实在正式评测中表现成零工具失败。

这里“模型端波动”只是最后一个放大因素；根因结构是多个串行LLM质量节点、完整raw进入即时summarization、后续证据多路重复和报告式文本重复生成。

## 最终回答

### 工具全部完成时累计耗时

- `multi_001`：约 30.75 秒；
- `multi_003`：约 44.47 秒。

### 工具完成后到 timeout

- `multi_001`：约 149.26 秒；
- `multi_003`：约 135.53 秒。

### 最大的三个LLM latency节点

唯一完整同版本 trace中的实测Top 3：

1. 重复TRON step的Executor result summarization：45.05秒；
2. Answer Composer：30.54秒；
3. 必要TRON step的Executor result summarization：29.36秒。

最终两工具prompt已经删除第一项重复step；最终timeout run的精确Top 3因trace丢失不可恢复，不能伪造。剩余主要候选是必要TRON summarization、Composer，以及Planner/Evaluator/Reviewer中的一个。

### TRON raw是否被重复送入所有节点

不是逐字完整地送入所有节点。完整raw直接进入当前step的Executor summarization；后续Evaluator、Composer、Reviewer收到的是raw前缀、structured facts、context summary、Executor draft、execution summary和最终answer等重叠派生表示。上一轮重复step曾让完整raw被再次获取并再次进入Executor；最终工具路径已消除该重复。

### 压缩为何不充分

- 8.36KB低于16KB threshold，初始ToolMessage不压缩；
- 当前step `dependency_evidence`绕开旧消息压缩；
- structured facts保留高token hex却未形成任务专用receipt摘要；
- StepResult固定复制raw前2K；
- Composer同时使用execution summary、evidence summary和draft；
- Reviewer再次使用answer和execution summary；
- 13K～17K上下文远低于rolling-summary触发线，所以容量控制不会介入latency问题。

### 必要与可优化调用

必要的是两次工具参数生成和一次最终回答生成；Planner对动态依赖任务有价值。数据库结果长摘要、每步无条件LLM Evaluator、完整raw驱动的TRON长摘要、planned无条件Reviewer均存在结构化替代、合并、跳过或条件执行空间。

## 根因排序

1. **串行重复语义处理**：Executor summary → Evaluator → Composer → Reviewer对同一证据多次理解和再生成。
2. **当前TRON evidence未压缩**：raw低于byte threshold且dependency evidence绕过compact路径。
3. **证据表示叠加而非替换**：raw前缀、facts、summary、draft、execution summary同时存在。
4. **报告式output重复生成**：Executor和Composer都生成长文本，Reviewer再读一遍。
5. **Reviewer/Evaluator缺少条件门控**：成功、只读、证据一致时仍调用LLM。
6. **容量阈值不等于latency预算**：96K context和90% rolling-summary触发无法控制13K～17K调用的实际耗时。
7. **模型端queue/prefill/decode波动**：放大上述结构成本，但不是充分根因。
8. **timeout观测丢失**：不制造耗时，却掩盖真实卡点并把已完成工具错误显示成0调用。

本轮不修改代码。下一步若要验证而非推断，应首先让timeout-safe trace按每次LLM call保存 `start_time/duration/input_tokens/output_tokens/context categories`，并持久化到请求生命周期之外；否则任何timeout run的逐调用精确timeline都会在客户端deadline处丢失。
