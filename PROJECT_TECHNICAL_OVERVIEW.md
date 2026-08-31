# ChainCloud AI 当前项目技术说明

> 更新日期：2026-08-22
> 适用范围：当前 `Chaincloud-AI-main` 仓库  
> 文档目标：用于项目交接、技术复盘、性能评估和面试展示。本文以当前代码为准，省略部分实现细节，但重点展开 Agent 框架、Memory、上下文工程、后台异步通知与 Evaluation Framework。

## 1. 项目概述

ChainCloud AI 是一个面向 TRON 链上数据分析、结构化业务数据库查询和持续监控的全栈 Agent 应用。系统使用 FastAPI 提供 API，使用 LangGraph 编排 Agent 状态图，通过 OpenAI 兼容模型完成路由、规划、工具调用、步骤评估和回答生成，并通过 React/Vite 提供 Web 交互界面。

当前项目的重点不只是“把问题发给大模型”，而是构建一条可恢复、可审计、有权限控制和性能观测的 Agent 执行链路：

```text
用户请求
  → 鉴权与 Memory 召回
  → Direct / Planned 路由
  → Minimal Sufficient Planner、Permission Gate、State Validation
  → 工具执行、Raw Result 持久化与确定性 StepResult Fast Path
  → Machine Validator（shadow）、LLM Step Evaluator、Replan
  → Answer Composer（execution state / draft 分离）、Reviewer
  → 保存 Checkpoint、Trace 和工具证据
  → 返回答案或等待确认/澄清
```

项目当前真实业务数据范围是：

- PostgreSQL `public.justlend`：JustLend 协议市场事件；
- PostgreSQL `public.croas_chain`：跨链充值、提现和处理记录；
- TRON 公共节点交易查询；
- 配置的 TRON Full/Solidity Node HTTP API；
- 基于查询结果生成的 Plotly HTML 图表和 Dashboard；
- APScheduler 定时 Agent 任务；
- PostgreSQL 后台交易监控和飞书通知。

必须明确：当前数据库不是 TRON 全链数据库。数据库查询结果只能描述当前可访问的 JustLend 和跨链数据，不能据此断言 TRON 全链存在或不存在某类交易。

## 2. 技术栈

| 层级 | 技术 | 主要职责 |
|---|---|---|
| 前端 | React、TypeScript、Vite | 登录、聊天、流式执行状态、权限确认、图表展示 |
| API | FastAPI、Pydantic、Uvicorn | 请求校验、鉴权、路由、应用生命周期 |
| Agent | LangGraph、LangChain Core | 状态图、节点路由、工具循环、Checkpoint |
| 模型 | `langchain-openai` | OpenAI 或兼容 OpenAI 协议的模型服务 |
| 数据库 | PostgreSQL、psycopg | 业务查询、Checkpoint、Memory、用户和监控状态 |
| 链上访问 | TRON HTTP API | 交易本体、交易回执、节点只读请求 |
| 可视化 | pandas、Plotly | 时间序列、柱状图、饼图、双轴图和 Dashboard |
| 后台任务 | APScheduler | 定时 Agent 任务、周期监控扫描 |
| 可观测性 | AgentState execution trace | 节点、工具、错误、权限、上下文和性能事件 |
| 测试评估 | pytest、自研 Evaluation Framework | 单元测试、离线评估、性能统计、对比实验 |

## 3. 项目目录

```text
Chaincloud-AI-main/
├── src/chaincloud_agent_service/
│   ├── main.py                    # FastAPI 生命周期和服务装配
│   ├── config.py                  # 环境变量配置
│   ├── api/routes/                # chat、auth、memory、monitoring 等 API
│   ├── agent/
│   │   ├── graph.py               # LangGraph 总编排
│   │   ├── state.py               # AgentState
│   │   ├── routing/               # Direct / Planned 路由
│   │   ├── planning/              # Plan 生成与验证
│   │   ├── evaluation/            # LLM Evaluator 与 Machine Validator
│   │   ├── review/                # 最终回答 Reviewer
│   │   ├── answer_composer/       # 最终答案合成
│   │   ├── step_result_fast_path.py # 确定性 StepResult 构造
│   │   ├── tool_results.py        # Raw Result、canonical facts 与引用
│   │   ├── context_builder.py     # 统一上下文预算
│   │   ├── rolling_summary.py     # 滚动摘要
│   │   ├── tool_recovery.py       # 工具错误分类与重试
│   │   └── permission.py          # 确定性权限规则
│   ├── tools/                     # PostgreSQL、TRON、图表、Scheduler 等工具
│   ├── memory/                    # 长期 Memory 服务和存储
│   ├── monitoring/                # 后台扫描、规则匹配、事件状态
│   ├── notification/              # 飞书通知
│   ├── observability/trace.py     # 生产 execution trace
│   └── evaluation/                # 离线 Evaluation Framework
├── frontend/chaincloud-agent-web/ # React Web 客户端
├── config/                        # 数据库说明、回答风格等上下文
├── eval/test_cases.jsonl          # 当前项目 30 条评估用例
├── eval_results/                  # JSON 和 Markdown 评估报告
├── tests/                         # 自动化测试
└── docs/                          # 专题设计文档
```

## 4. 服务启动与运行时装配

FastAPI 应用入口是 `chaincloud_agent_service.main:app`，项目默认使用 `8001` 端口：

```bash
uv run uvicorn --app-dir src chaincloud_agent_service.main:app \
  --reload --host 0.0.0.0 --port 8001
```

应用启动时依次完成：

1. 从 `.env` 和系统环境变量加载 `Settings`；
2. 检查模型 API Key；
3. 创建 Memory Store、Embedding Provider 和 MemoryService；
4. 创建用户存储和 AuthService；
5. 根据 `DATABASE_URL` 选择内存或 PostgreSQL LangGraph Checkpointer；
6. 编译 Agent Graph，并把生产工具绑定给模型；
7. 启动 APScheduler；
8. 如果 Monitoring 开启，则初始化 MonitorStore、交易源和 MonitorWorker；
9. 挂载 `/chat`、`/memory`、`/monitor`、`/tools`、`/charts` 等路由。

Monitoring 开启时，应用启动会连接监控数据库和交易数据库。因此 PostgreSQL 未启动、端口错误或表映射错误都可能导致应用在 `Application startup` 阶段退出。

## 5. Agent 核心框架

### 5.1 AgentState

`AgentState` 是系统的核心共享状态，也是 LangGraph Checkpoint 的主要载体。它不仅保存消息，还保存执行控制和可观测数据。

主要字段可以分为以下几组：

| 状态组 | 示例字段 | 作用 |
|---|---|---|
| 会话 | `messages`、`user_id` | 用户消息、模型消息、用户身份 |
| 路由 | `requested_mode`、`execution_mode`、`route_reason` | Direct/Planned 决策 |
| Plan | `plan`、`current_step_id`、`step_results` | 多步骤计划与结果 |
| 权限 | `pending_permission`、`permission_action` | 等待确认和批准范围 |
| 澄清 | `state_validation`、`clarified_state` | 缺失状态和用户补充 |
| 恢复 | `tool_call_count`、`step_retry_count`、`replanning_count` | 重试、预算和重规划 |
| Memory | `active_recalled_memories`、`recalled_memory_keys` | 自动召回结果 |
| 压缩 | `conversation_summary`、`summarized_until` | 滚动摘要状态 |
| 监控草稿 | `pending_monitor_draft`、`monitor_draft_version` | 监控规则草稿和确认 |
| Trace | `node_events`、`tool_events`、`decision_events` | 性能与执行审计 |

这种设计让 Agent 可以在以下情况下从 Checkpoint 恢复：

- 用户确认副作用操作；
- 用户补充缺失字段；
- 多轮修改 Monitoring 草稿；
- 同一 `thread_id` 继续对话；
- 服务重启后恢复 PostgreSQL Checkpoint。

### 5.2 Direct 与 Planned

系统提供三种请求模式：

```text
auto     自动判断
direct   强制直接执行
planned  强制先规划再执行
```

Direct 路径适合概念解释、简单问答和单工具查询。它减少 Planner 调用和整体延迟，但不适合复杂依赖和高风险副作用操作。

Planned 路径适合：

- 多数据源或多工具任务；
- 数据库查询后继续链上核验；
- 图表和报告生成；
- 存在步骤依赖的分析；
- 需要 Permission Gate 的副作用操作；
- 需要步骤级成功标准、Retry 或 Replan 的任务。

Planner 输出结构化 Plan，每个步骤包含目标、成功标准、依赖、建议工具、Fallback、预计工具调用数和是否需要确认。Validator 检查步骤 ID、依赖关系和工具引用，避免无效计划直接进入执行。

当前 Planner 遵循 **Minimal Sufficient Plan**：正常路径中的每一步都必须直接贡献于用户目标、成功标准、必要证据或权限边界。简单任务通常只保留一个步骤；多工具任务按动态数据依赖拆分，而不是按工具数量机械拆步。已有 trusted schema facts 时直接规划目标查询，`postgres_list_tables` 和 `postgres_table_schema` 仅作为真实 schema/type 错误后的 recovery，不再作为默认前置步骤。最终汇总、跨来源比较和报告撰写由 Answer Composer 负责，Planner 不再额外创建只做“总结”的步骤。

这项约束减少了 schema discovery、额外统计、重复验证和 synthetic summary 等不直接产生新证据的调用，同时保留权限确认、Fallback 和真正的数据依赖。

### 5.3 Permission Gate

Permission Gate 使用确定性代码规则，不让 LLM 决定安全权限：

- `ALLOW`：只读 PostgreSQL、TRON 查询、知识检索等；
- `NEED_CONFIRM`：Scheduler、图表文件、Dashboard、监控规则增删改；
- `DENY`：绕过权限、导出私钥、删除所有数据等明显危险意图。

批准范围绑定为：

```text
step_id + tool_name
```

因此一次批准不能自动授权后续步骤或其他工具。等待确认时，当前状态写入 Checkpoint，用户批准后从准确节点恢复，不需要重新执行前面已经完成的步骤。

### 5.4 State Validation

State Validation 位于 Permission Gate 和 Executor 之间，用确定性规则检查任务是否具备执行条件，例如：

- Scheduler 是否提供日期或 Cron；
- 目标工具是否真实可用；
- 执行所需业务参数是否完整；
- 前置步骤是否已经产生依赖结果。

缺失状态时系统进入 `blocked_missing_state`，通过结构化 `clarification_required` 请求用户补充；不能补齐时进入 Partial 或 Failed，而不是让模型猜测参数。

State Validation 会读取前置步骤的结构化事实，例如把依赖结果中的交易哈希识别为后续 TRON 查询所需的 `txid`。字段匹配按语义和目标参数校验，交易哈希不会被误当成地址。这样，多工具链路可以把上一步的 canonical facts 正确绑定到下一步，而不依赖模型从 Raw JSON 中重新猜测。

### 5.5 工具循环与步骤评估

Executor 可以在预算内多次调用工具。工具返回后先持久化 Raw Result，并生成 structured facts、result contract、`result_id` 和 provenance。Planned 模式随后有两条 `StepResult` 构造路径：

1. **确定性 Fast Path**：仅限无未解决错误、实际和计划均为一次工具调用、单一终态结果、未截断、无歧义、facts/provenance 完整且 contract 明确允许的结果。当前白名单包括单行只读 PostgreSQL 结果和 canonical TRON transaction。代码直接构造可追溯的 `StepResult`，省去一次 Executor LLM 总结。
2. **LLM Executor Fallback**：多行结果、多工具调用、错误恢复、contract 不完整或任何不满足安全条件的情况，继续由 Executor 生成自然语言步骤摘要，再构造 `StepResult`。

确定性结果不是仅凭“工具调用成功”放行；`StepResult` 会保存 structured facts、dependency outputs、result references、provenance 和 tool calls，原始证据可通过 `result_id` 追溯。Fast Path 拒绝时只回退到原 Executor，不改变既有正确性路径。

`StepResult` 构造完成后，在线 LLM Evaluator 根据步骤成功标准返回：

```text
pass / retry / replan / partial / fail
```

其中：

- Tool 返回成功不等于任务成功；
- Retry 用于参数修正或补充查询；
- Replan 用于原执行路径已经不适用；
- Partial 用于已有可信结果但数据覆盖或外部能力不足；
- Permission 拒绝不能通过其他工具绕过。

此外，系统已经接入保守的 **Machine Step Validator**。它对白名单语义检查结果状态、错误、重试/恢复、计划工具名、完整引用、终态 contract、截断和歧义、structured facts、成功标准字段、单行/标量约束、交易哈希格式、交易/回执状态以及依赖参数绑定。决策只有 `pass / fail / unknown`；任何自然语言成功标准无法被当前版本完整证明时都返回 `unknown`。

当前 Machine Validator 运行在 **shadow mode**：每次结果和逐项 predicate 都写入 `decision_events`，但不改变路由，之后仍调用 LLM Evaluator。这为未来安全启用“machine pass 时跳过 LLM Evaluator”的 fast path 收集可审计数据，同时避免把“结构完整”误当成“步骤语义已成功”。

### 5.6 Answer Composer 与 Reviewer

最终答案不是简单返回最后一个模型消息。Answer Composer 会整合：

- 用户原问题；
- Plan 和 StepResult；
- 工具结构化事实；
- 数据范围和失败信息；
- 图表 URL；
- Partial/Degraded 状态。

Composer 现在显式分离两个输入通道：

- `execution_summary` 作为受保护的结构化执行状态，包含 mode、route、plan、全部 StepResult、status 和 failure reason；
- `draft` 只接收当前用户轮次中的真实独立回答草稿。

用于携带执行状态的 synthetic AIMessage 带有 `chaincloud_composer_role=execution_summary` 标记，不再被误选为 draft。Planned 模式下，若 Executor 输出已与 `StepResult.summary` 完全相同，则省略重复 draft；Direct 模式仍保留真实 Agent draft；Reviewer revision 则显式保留上一版答案和修订意见。Composer 调用失败时也使用同一份已筛选 draft 作为 fallback，避免重新拾取 synthetic summary。

Reviewer 检查最终答案与工具证据是否一致、是否越过数据边界、是否需要修改。复杂 Planned 任务默认进入 Reviewer；Direct 路径在风险较高或工具链较复杂时也可进入审查。

## 6. 当前工具与业务数据

### 6.1 PostgreSQL

`postgres_select` 只接受单条 `SELECT` 或 `WITH`，最多返回 500 行。当前业务表：

#### `public.justlend`

保存 JustLend 存款、借款、还款、赎回和清算等市场事件。重要约束：

- 默认业务日期使用 `day`；
- `occurred` 是 UTC+8 展示时间，不能替代默认日期过滤；
- 统一大额判断优先使用 `amount_usd`；
- `operation_type` 应以真实查询结果为准，不凭经验枚举。

#### `public.croas_chain`

保存跨链充值和提现记录。用户可能称其为 `cross_chain`，但真实表名是 `croas_chain`。重要约束：

- 链 ID 映射未经验证时不能猜测链名；
- 不同 Token 的原始数量不能直接比较；
- `withdraw_fee` 是字符串，计算前需验证和转换；
- 时间字段为字符串，使用前应确认格式。

### 6.2 TRON 工具

`get_tron_transaction` 接受 64 位十六进制 txid，固定调用两个只读接口：

```text
/wallet/gettransactionbyid
/wallet/gettransactioninfobyid
```

它合并交易本体与执行回执，适合查询手续费、Receipt、日志和内部交易。无效 txid 在本地拒绝，不发送网络请求；其中一个接口失败时保留另一个接口的 Partial 结果。

`tron_node_request` 用于配置的 Full/Solidity Node，只允许安全路径和 JSON POST，可查询最新区块、账户或执行只读合约调用。

### 6.3 图表与 Dashboard

项目支持：

- 柱状图；
- 时间序列；
- 饼图；
- 多线图；
- 双轴图；
- 数值区间分布图；
- 多图表 Dashboard。

图表写入 `charts/*.html`，FastAPI 通过 `/charts` 静态托管。部分图表可以读取最近一次 PostgreSQL 查询结果，减少把大量数组重复传回模型的成本。

## 7. Memory 系统

### 7.1 三种不同的“记忆”

项目中有三类容易混淆的状态：

| 类型 | 作用域 | 存储 | 主要用途 |
|---|---|---|---|
| Checkpoint 会话状态 | `thread_id` | 内存或 PostgreSQL | 保留消息和 Agent 执行状态 |
| 长期 Memory | `memory_key`、`user_id` | 内存或 PostgreSQL | 跨线程保存摘要和偏好 |
| Rolling Summary | 当前 `thread_id` | AgentState/Checkpoint | 压缩活跃上下文，控制 Token |

Checkpoint 不是长期 Memory；Rolling Summary 也不是知识库或用户长期偏好。

### 7.2 长期 Memory 生命周期

长期 Memory 支持：

- 手动保存、读取、列出和删除；
- 根据某个 thread 的 Checkpoint 历史生成摘要；
- `/chat` 显式指定 `memory_key`；
- 不指定 key 时进行受控的自动语义召回；
- Memory 所有权按登录用户隔离；
- 内存或 PostgreSQL 存储后端。

显式 Memory 流程：

```text
历史对话
  → /memory/summarize
  → LLM 生成长期摘要
  → MemoryStore 保存 MemoryRecord
  → 新 /chat 指定 memory_key
  → 以受限 SystemMessage 注入
```

### 7.3 自动召回

自动召回不是每次请求都执行 Embedding。系统先经过确定性 Gate：

- “继续之前的工作”“沿用我的偏好”等跨线程信号会触发召回；
- 当前价格、最新区块等独立实时查询走 Cheap Path，跳过召回；
- 显式 `memory_key` 优先于自动召回；
- 召回候选按用户隔离；
- 最多选择 3 条，并受相似度阈值和 Token Budget 限制；
- Embedding 或数据库异常时降级为不注入，不阻断 Agent。

相关 trace 会记录：

```text
是否触发召回
跳过原因
候选数量
选中数量和 key
相似度与最终得分
召回耗时
```

Memory 只提供历史背景，不能代替本轮链上工具证据。例如 Memory 中保存的余额、行情或协议状态不能被当作当前事实。

### 7.4 Memory 安全与隔离

Memory API 与 Chat 共用鉴权。自动召回和显式注入都会校验 `user_id`，即使多个用户使用相同 `thread_id`，也不会注入其他用户的 Memory。Trace 中敏感字段会被脱敏。

## 8. 上下文工程

### 8.1 为什么需要统一 ContextBuilder

Agent 的输入来源很多：系统提示、数据库 schema、历史消息、Memory、Plan、工具结果、错误反馈和 Reviewer 上下文。如果各节点独立拼接，容易发生：

- Token 超限；
- 关键用户约束被旧工具结果挤掉；
- 大型 JSON 工具结果重复发送；
- 不同节点看到不一致的上下文；
- 无法解释为什么某段内容被裁剪。

ContextBuilder 将上下文统一表示为带优先级的 ContextPart，并在模型调用前执行预算分配。

### 8.2 Token Budget

核心预算关系为：

```text
可用输入预算 = min(MAX_INPUT_TOKENS,
                    MODEL_CONTEXT_WINDOW - RESERVED_OUTPUT_TOKENS)
```

默认配置包括：

| 配置 | 默认值 |
|---|---:|
| `MODEL_CONTEXT_WINDOW` | 128000 |
| `MAX_INPUT_TOKENS` | 96000 |
| `RESERVED_OUTPUT_TOKENS` | 8000 |
| `ROLLING_SUMMARY_TRIGGER_RATIO` | 0.70 |
| `ROLLING_SUMMARY_RECENT_MESSAGES` | 12 |

ContextBuilder 按场景保留必要内容，并以完整消息为边界裁剪，不截断半个 ToolMessage。当前 Planned Step 的工具请求和结果属于受保护上下文。

每次构建都会产生 `context_events`，记录：

- 场景；
- 最大输入 Token；
- 实际 Token；
- 各类别 Token；
- 被裁剪内容及原因。

### 8.3 工具结果三层生命周期

大型工具结果采用三层结构：

```text
Raw Tool Result
  → Structured Facts
  → Context Summary + result_id
```

#### Raw Result

所有进入该链路的完整结果先写入 `tool_results/`，保存工具名、参数、时间、SHA-256、证据来源和原始内容，便于审计和重新读取。Agent Graph 不再直接承载 TRON Raw JSON。

#### Structured Facts

确定性提取状态、标量、字段、数量和少量样例。TRON 交易结果使用固定 canonical transaction contract；Planned StepResult 保存这些 facts、dependency outputs、Raw Result 引用与 provenance。

#### Context Summary

超过压缩阈值的普通结果进入模型前替换为短摘要、Preview 和 `result_id`；canonical TRON 结果无论大小都以 canonical facts 进入上下文。错误结果保留关键错误载荷，避免压缩破坏 Retry、Permission 或 Fallback 判断。

这种方式减少 Token，但不丢失可追溯证据。

### 8.4 Rolling Summary

Rolling Summary 用于同一线程长期对话。当活跃消息达到预算比例时，系统生成 Task-aware JSON 摘要，同时保留最近消息。

摘要包括：

- 当前目标和用户约束；
- 关键地址、交易哈希、日期和数字；
- 当前 Plan、已完成和待完成步骤；
- 工具发现、失败尝试和未解决错误；
- Permission、澄清状态和开放问题。

完整消息仍保存在 Checkpoint 中，Rolling Summary 只改变“发给模型的活跃视图”。如果模型返回 context length error，还可以执行 Reactive Compact 后重试。

## 9. 错误恢复与降级

`RecoveringToolNode` 对工具异常进行结构化分类：

- Timeout、Connection Reset、429 等瞬时错误可以指数退避重试；
- 参数错误、Schema 错误不做机械重试，交给 Step Evaluator 修正；
- Permission Denied 永不自动重试；
- 全局工具调用预算耗尽时返回结构化 `budget_exhausted`；
- Fallback 工具只允许 Planner 明确声明的路径。

Trace 会记录每次 attempt、耗时、错误类型、是否可重试、是否恢复和是否使用 Fallback。最终结果分为：

```text
success / partial / degraded / failed
```

系统的目标不是让所有请求都显示成功，而是在数据不足、工具不可用或权限拒绝时提供可信的 Partial/Degraded 结果，并明确说明边界。

## 10. Scheduler

Scheduler 解决“按时间执行 Agent 任务”：

- `date`：在某个 ISO 8601 时间执行一次；
- `cron`：按照小时、分钟等 Cron 参数重复执行。

定时任务保存 Prompt，触发后以 `scheduled:<task_id>` 作为 thread ID 调用 Agent Graph。创建定时任务会修改持久化状态，因此必须通过 Permission Gate。

Scheduler 与 Monitoring 的区别：

| 模块 | 触发条件 | 例子 |
|---|---|---|
| Scheduler | 到达指定时间 | 每天 08:00 生成 JustLend 摘要 |
| Monitoring | 新数据满足规则 | 出现超过 10 万美元交易时通知 |

## 11. 后台异步监控与通知

### 11.1 整体链路

```text
用户提出监控需求
  → Agent 生成结构化 Monitor Draft
  → 校验地址、阈值、链、Token 和通知渠道
  → 用户修改或确认最新草稿
  → 正式规则写入 PostgreSQL
  → APScheduler 周期调用 MonitorWorker
  → 增量读取交易表
  → 匹配启用规则
  → 创建 Notification Event
  → 飞书 Webhook 发送
  → 保存 sent / failed 状态
```

这里的“异步”表示扫描和通知在 FastAPI 请求之外由后台 Scheduler 执行，用户不需要保持页面连接。当前没有引入 Celery、Redis Queue 等独立任务队列，部署简单，但后台任务仍与 Web 服务进程生命周期相关。

### 11.2 Monitor Draft

正式创建规则前，Agent 先生成 Checkpoint-friendly 草稿：

```text
rule_type: address_transaction | large_transaction
address
min_amount
min_amount_usd
chain
token
notification_channel
protocol
```

规则约束：

- 地址交易监控必须提供地址；
- 大额交易监控必须提供 Token 数量或 USD 阈值；
- 金额必须大于 0；
- 当前通知渠道只支持飞书；
- 草稿带 Version 和 SHA-256 Hash；
- 用户确认旧版本时拒绝创建，防止修改后误确认；
- 其他用户不能确认不属于自己的草稿。

### 11.3 MonitorStore

Monitoring 使用 PostgreSQL 保存：

| 表 | 作用 |
|---|---|
| `monitor_rules` | 正式监控规则 |
| `monitor_notification_events` | 命中事件和发送状态 |
| `monitor_scan_state` | 增量扫描游标 |
| `monitor_notification_configs` | 用户通知地址 |

规则按 `user_id` 隔离。工具执行时通过 ContextVar 绑定当前登录用户，防止跨用户查询或修改规则。

### 11.4 Worker 与增量扫描

Worker 使用单调 ID 游标：

```sql
WHERE id > last_processed_id
ORDER BY id ASC
LIMIT batch_size
```

默认首次启动只记录当前最大 ID，不处理全部历史数据，防止突然发送大量旧通知。`MONITOR_PROCESS_EXISTING=1` 时才处理已有记录。

多实例部署使用 PostgreSQL Advisory Lock，保证一轮扫描只由一个实例执行。Scheduler 设置 `max_instances=1` 和 `coalesce=True`，避免同一实例任务堆积。

### 11.5 规则匹配

当前规则支持：

- 地址等于 From 或 To；
- `amount >= min_amount`；
- `amount_usd >= min_amount_usd`；
- Chain、Token 精确过滤；
- 多个条件使用 AND。

Monitoring 交易源通过字段映射适配实际业务表。当前两张业务表结构不同，因此部署时必须准确配置 `MONITOR_TRANSACTION_TABLE` 和 `MONITOR_TRANSACTION_COLUMNS`，不能直接假设默认 `transactions` 表存在。

### 11.6 通知可靠性

命中事件先写数据库，再发送通知。唯一约束为：

```text
rule_id + transaction_id
```

因此同一规则和交易不会重复创建事件。通知状态包括：

```text
pending / failed / sent
```

失败事件在后续扫描重试，最多 5 次，并保存错误原因。飞书 Webhook 按用户保存，URL 必须满足官方地址前缀约束。

当前架构的边界：

- Scheduler 与 API 同进程，服务停止时不扫描；
- 没有独立消息队列；
- 通知目前只支持飞书；
- 需要依靠数据库唯一约束和事件状态实现幂等，而不是分布式消息 Exactly-once。

## 12. Observability 与 Trace

项目没有为评估另建一套 Trace。生产 AgentState 已记录：

```text
node_events
tool_events
decision_events
error_events
context_events
compact_events
memory_recall_events
tool_result_records
request_summary
```

请求级 Summary 包含：

- 总耗时；
- LLM 调用次数；
- Tool 调用和重试次数；
- Step Retry 和 Replan；
- Permission 检查；
- Fallback；
- Context Build 和裁剪；
- Rolling Summary 次数；
- Input/Output/Total Tokens；
- 最终状态。

Trace 会脱敏 API Key、Authorization、Password、Secret 等字段，但 Token 用量字段属于性能指标，不会被误当成凭据脱敏。

## 13. Evaluation 与性能测试

### 13.1 两类 Evaluator

项目中存在两个不同层次的 Evaluation：

1. 在线 Step Evaluator：运行在 Agent Graph 内，决定步骤 Pass、Retry 或 Replan；
2. 离线 Evaluation Framework：用统一数据集评价整个 Agent 请求，用于改造前后对比和项目展示。

离线 Eval 不修改 Agent 核心业务逻辑，通过公共 `/chat` API 和生产 execution trace 获取结果。

### 13.2 当前数据集

`eval/test_cases.jsonl` 当前有 30 条，覆盖：

| Category | 数量 |
|---|---:|
| Direct | 3 |
| Database | 5 |
| TRON | 4 |
| Multi-tool | 3 |
| Chart | 4 |
| Scheduler | 3 |
| Monitoring | 4 |
| Memory | 2 |
| Recovery | 2 |

数据集只测试当前真实能力。未启用的 Ethereum、ClickHouse、Web Search 和 Knowledge Base 不进入主成功率。

每条 case 可以定义：

```text
case_id
category
user_query
ground_truth
expected_tools
expected_arguments
expected_permission
required_facts / forbidden_facts
required_capabilities
fault_injection
tags
```

### 13.3 Ground Truth 隔离

Runner 只把以下内容发送给被测 Agent：

```text
user_query
planning mode
thread_id / multi-turn messages
```

Ground Truth、Expected Tools、参数约束和 Judge Rubric 只在 Agent 完成后由评估器读取。被测 Agent 无法通过读取答案标准获得高分。

### 13.4 Deterministic Evaluator

确定性检查包括：

- 是否调用预期工具；
- 不需要工具时是否真正没有调用；
- 参数是否满足 `eq/contains/regex/in/gte/lte/exists` 约束；
- Permission Gate 是否返回预期 Action；
- Required Facts 是否存在；
- Forbidden Facts 是否未出现；
- 禁止工具是否没有执行；
- 最终状态是否符合预期；
- 注入瞬时错误后是否恢复；
- Memory 是否召回预期 Key。

工具和参数来自生产 `tool_result_records`，权限和恢复来自 `decision_events/tool_events`，不是依赖回答文字猜测。

### 13.5 LLM-as-Judge 与人工复核

开放式答案可以设置 `judge=true`，由独立、未绑定工具的 Judge 模型检查语义质量。以下项目不交给 LLM 判断：

- 工具选择；
- 工具参数；
- Permission；
- 禁止操作；
- 是否真实执行写操作。

安全关键 case 使用 `human_review=true`，报告会单独标记，形成：

```text
Deterministic Evaluator
  + LLM-as-Judge
  + Optional Human Review
```

### 13.6 核心指标

正确性指标：

- Task Success Rate；
- Tool Selection Accuracy；
- Tool Argument Accuracy；
- Recovery Success Rate；
- Permission Gate Accuracy；
- Partial、Degraded、Failed Rate；
- Memory Retrieval Hit Rate/Accuracy。

性能指标：

- End-to-End P50/P95；
- 各节点平均/P95；
- 平均 LLM Calls；
- 平均 Tool Calls；
- Input/Output/Total Tokens；
- Tool Retry、Step Retry、Replan。

Token Usage 只在模型返回 Usage Metadata 时统计；不可用、历史脱敏或格式异常时显示 `N/A`，不会按 0 计算或让整次报告失败。

### 13.7 Fault Injection

为了可重复测试 Recovery，框架提供工具代理，可在指定调用次数注入：

- Timeout；
- 429；
- 参数错误；
- Permission Denied；
- Fallback Failure。

普通 HTTP Adapter 无法替换已经运行的服务端工具，因此自动跳过带 `fault_injection` capability 的 case；这些 case 只在测试构图或 Replay 环境进入成功率分母。目前普通 HTTP 评估会执行 28 条、跳过 2 条，并在报告列出原因。

### 13.8 Ablation

框架预留以下对比：

- Planner ON/OFF；
- Error Recovery ON/OFF；
- Memory Recall ON/OFF；
- Context Compression ON/OFF。

当前 HTTP API 可以真实切换 Planner。其他开关尚未全部暴露为 Request-scoped 配置时，Runner 返回 `not_supported`，不会伪造实验结果。后续应在测试构图中映射到重试次数、Memory Recall 和 Rolling Summary 配置。

### 13.9 运行方式

启动后端后执行：

```bash
python -m chaincloud_agent_service.evaluation.cli \
  --dataset eval/test_cases.jsonl \
  --endpoint http://127.0.0.1:8001/chat \
  --output-dir eval_results
```

启用 Judge：

```bash
python -m chaincloud_agent_service.evaluation.cli \
  --dataset eval/test_cases.jsonl \
  --endpoint http://127.0.0.1:8001/chat \
  --judge-model gpt-4o-mini \
  --output-dir eval_results
```

输出：

```text
eval_results/run_<timestamp>.json
eval_results/run_<timestamp>.md
```

JSON 保存完整 Observation、Checks、Trace Summary 和指标；Markdown 用于快速展示总体、性能、Category、Case 和人工复核结果。

## 14. 自动化测试

项目测试覆盖：

- Direct/Planned 路由；
- Planner、Evaluator、Reviewer；
- Permission Gate 和确认恢复；
- State Validation；
- 工具错误分类和重试；
- ContextBuilder 和 Token Budget；
- Tool Result Compression；
- Rolling Summary；
- Memory 存储、召回和用户隔离；
- Monitoring Worker；
- Monitor Draft 版本确认；
- TRON 交易查询；
- Evaluation Framework；
- Auth、API 和 Checkpoint。

当前回归基线：

```text
160 passed
Ruff: All checks passed
```

运行：

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

单元测试验证代码级行为；Evaluation Dataset 验证模型、Prompt、工具和完整 Agent 链路。二者不能互相替代。

## 15. 当前优势

从工程和面试展示角度，当前项目的主要亮点是：

1. Agent 不是单次 Prompt，而是可恢复的 LangGraph 状态机；
2. Direct/Planned 分层兼顾简单请求延迟和复杂任务成功率；
3. Permission 与 State Validation 使用确定性规则；
4. 工具错误具有分类、重试、预算、Fallback 和 Partial 语义；
5. Memory、Checkpoint 和 Rolling Summary 职责明确；
6. 上下文使用统一 Token Budget，工具原始证据可追溯；
7. Monitoring 包含草稿确认、用户隔离、增量扫描、幂等事件和通知重试；
8. Evaluation 复用生产 Trace，支持正确性和性能指标；
9. Ground Truth 与被测 Agent 隔离，安全 case 支持人工复核；
10. 数据范围约束明确，避免把局部业务数据夸大为全链事实。

## 16. 当前边界与后续方向

当前仍存在以下边界：

- Monitoring 与 Web API 同进程，不是独立 Worker 服务；
- Scheduler 本地任务存储和生产多实例一致性仍可加强；
- Knowledge Base 当前关闭，尚不能评价 RAG 检索质量；
- HTTP Fault Injection 和部分 Ablation 开关需要测试构图支持；
- Eval Runner 当前串行执行，完整 28 条真实请求可能耗时较长；
- TRON 公共节点和模型服务仍受外部网络稳定性影响；
- Monitoring 交易源需要针对 `justlend` 或 `croas_chain` 配置准确字段映射；
- 真实评估应固定数据库快照或记录数据版本，避免 Ground Truth 随数据变化。

推荐后续优先级：

1. 给 Eval Runner 增加进度输出和安全并发；
2. 建立固定数据库测试快照和已知 TRON txid fixture；
3. 在测试构图中正式接入 Fault Injection；
4. 暴露 Request-scoped Ablation 开关；
5. 将 Monitoring Worker 独立部署并增加运行指标；
6. 如果启用 Knowledge Base，再建立独立 RAG Dataset；
7. 为 Evaluation 建立持续基线，保存改造前后 JSON 报告。

## 17. 总结

ChainCloud AI 当前已经具备一个中等复杂度生产型 Agent 的主要工程要素：状态编排、工具执行、权限控制、错误恢复、上下文压缩、长期记忆、后台监控通知、可观测性和离线评估。

项目最重要的可信性原则是：

```text
回答必须来自当前工具证据；
局部数据库不能被表述成 TRON 全链；
副作用操作必须确认；
失败和数据不足必须显式降级；
性能改造必须通过可重复 Evaluation 验证。
```

这套设计使项目既能支持当前 JustLend、跨链与 TRON 分析场景，也为未来扩展更多数据源、知识库和独立后台任务留下了清晰接口。
