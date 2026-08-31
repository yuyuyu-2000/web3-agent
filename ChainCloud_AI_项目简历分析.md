# ChainCloud AI 项目简历分析（基于当前仓库代码）

> 审阅口径：以当前工作区代码和测试为准，README/docs 仅作辅助证据。审阅日期：2026-08-23。  
> 测试结果：`199 passed, 1 failed`；唯一失败是 `eval/test_cases.jsonl` 已有 34 条用例，而测试仍断言固定为 30 条。当前工作区本身存在未提交修改，因此本文不把历史报告中的指标直接包装成生产效果。

## 一、这个项目到底是做什么的

### 1. 业务背景

ChainCloud AI 是一个面向链上数据运营、研究和风控人员的对话式分析与监控平台。业务人员通常需要在 PostgreSQL/ClickHouse 业务库、TRON/Ethereum 节点和外部网页之间反复查询，再整理结论、生成图表，或持续盯住某个地址和大额交易。项目把这些操作统一成自然语言入口，并补充了后台调度、持续监控和消息通知能力。

必须注意数据边界：仓库内明确描述的 PostgreSQL 业务数据主要是 `justlend`（JustLend 市场事件）和 `croas_chain`（跨链充值、提现及处理记录），它不是完整的 TRON 全链数据库，不能用数据库查询结果证明全链不存在某类交易。

### 2. 用户可以完成什么

- 用自然语言查询 JustLend、跨链业务数据，以及已配置的 ClickHouse 数据源。
- 根据交易哈希查询 TRON 交易本体与回执，或调用 TRON/Ethereum 节点接口核验链上信息。
- 组合数据库查询与链上核验，完成“先找出业务记录，再拿交易哈希查链上状态”一类多步骤任务。
- 把查询结果生成柱状图、折线图、饼图、双轴图、价格分布图或多图 Dashboard，并在 Web 页面查看 HTML 图表。
- 创建一次性或 Cron 定时 Agent 任务，让系统按时自动执行一段分析提示并保存结果。
- 创建地址交易/大额交易监控规则；后台增量扫描新交易，命中规则后向用户配置的飞书 Webhook 发通知。
- 登录后保存对话摘要，并在后续提到“上次、继续、之前”时按用户召回相关长期记忆。
- 在 Web 端查看流式回答、工具执行进度、权限确认、缺失参数补充和开发模式 Trace。

### 3. 最核心的业务流程

典型分析流程是：用户登录并提出问题 → 系统决定简单直答还是拆成多步骤任务 → 查询业务数据库 → 从结果中提取交易哈希等结构化事实 → 调用链上节点核验 → 检查每一步是否满足目标，必要时重试或改计划 → 汇总证据、标注数据边界 → 返回文字和图表。

典型监控流程是：用户用自然语言描述监控需求 → 系统生成可修改草稿 → 用户确认具体版本 → 持久化监控规则 → APScheduler 周期触发后台扫描 → 使用增量游标读取新交易 → 匹配用户规则 → 持久化通知事件 → 异步于聊天请求发送飞书通知并记录成功/失败状态。

### 4. 用户真实可感知的功能

| 功能 | 用户感知 | 当前状态 |
|---|---|---|
| 对话查询与分析 | 输入问题，得到数据库/链上证据支持的回答 | ✅ 已实现 |
| 多数据源协同 | PostgreSQL、ClickHouse、TRON、Ethereum、网页搜索按配置协同 | ✅ 已实现；依赖外部配置 |
| 图表与 Dashboard | 返回可访问的 Plotly HTML 图表链接 | ✅ 已实现；缺少专门图表测试 |
| 定时 Agent 任务 | 指定日期或 Cron，后台执行提示并写入结果文件 | ⚠️ 代码完整，但仓库没有对应自动化测试，且任务存储为本地 JSON 文件 |
| 交易持续监控 | 地址/金额/链/Token 规则匹配新交易 | ✅ 已实现并有 worker/草稿测试 |
| 飞书通知 | 每用户配置 Webhook，命中规则后发送 | ⚠️ 发送代码已实现，未看到真实 Webhook 集成测试 |
| 长对话与恢复 | 同一 `thread_id` 延续对话，PG Checkpoint 可跨重启 | ✅ 已实现；同线程并发存在丢更新 |
| 长期记忆 | 手动保存/总结，按用户做语义召回 | ✅ 已实现并有多组测试；pgvector 不可用时语义能力会降级 |
| 流式执行过程 | Web 展示路由、计划、步骤、重试、审查等事件 | ✅ 已实现 |

## 二、完整技术架构与真实调用链

### 1. 服务启动与模块装配

`main.py` 的 FastAPI lifespan 负责加载配置，创建模型、MemoryService、AuthService 和 Checkpointer，编译 Agent 图，启动 APScheduler；启用 Monitoring 时还会建监控表、创建交易源和 Worker，并注册周期扫描任务。Checkpoint 有两种后端：配置 `DATABASE_URL` 时使用 `AsyncPostgresSaver`，否则使用进程内 `MemorySaver`。

运行时职责如下：

| 模块 | 职责 | 关键实现 |
|---|---|---|
| FastAPI/API | 鉴权、参数校验、流式 NDJSON、权限/澄清恢复接口 | `main.py`、`api/routes/chat.py` |
| LangGraph | 请求路由、步骤执行、权限、评估、重规划、回答审查 | `agent/graph.py` |
| Tools | 数据库、链上节点、搜索、图表、调度、监控规则 | `tools/registry.py` 及 `tools/*.py` |
| Checkpoint | 保存同一线程的消息和完整 AgentState | `persistence/checkpoint.py` |
| Long-term Memory | 摘要持久化、Embedding、按用户相似度召回 | `memory/service.py`、`memory/store.py` |
| Monitoring | 增量扫描、规则匹配、事件幂等和通知状态 | `monitoring/store.py`、`monitoring/worker.py` |
| Notification | 按用户和渠道解析目标，发送飞书 Webhook | `notification/service.py` |
| Scheduler | date/Cron 任务恢复、执行和结果落盘 | `tools/scheduler_runtime.py` |
| Observability/Eval | 节点、工具、决策、错误、上下文事件与离线指标 | `observability/trace.py`、`evaluation/` |

### 2. 从请求到返回的完整链路

1. `/chat` 或 `/chat/stream` 接收 `thread_id`、问题和 `auto/direct/planned` 模式；可使用用户 Token，也兼容静态 API Token。
2. 登录用户若显式指定 `memory_key`，系统校验记忆归属后注入；否则仅在问题出现历史指代信号时生成 Embedding，按 `user_id` 搜索候选记忆，并按相似度阈值选择最多 3 条。召回异常会降级，不阻断聊天。
3. API 把消息、用户 ID、召回结果和 Trace 上下文交给 LangGraph，并用 `thread_id` 读写 Checkpoint。
4. `prepare_request` 先按 Token 阈值尝试滚动摘要，同时识别待确认权限、待补充状态和监控草稿的确认/修改/取消。
5. Router 按三层策略选择 Direct 或 Planned：API 强制模式优先；其次使用确定性规则；模糊请求才调用模型分类，低置信度或失败时保守进入 Planned。
6. Direct 路径由绑定工具的模型直接循环调用工具，受单次和全局工具调用预算限制；结束后按风险决定是否进入 Reviewer。
7. Planned 路径由 Planner 生成结构化 `Plan/PlanStep`；Validator 检查步骤 ID、依赖、工具名等，无效输出重试一次，仍失败则降级为安全的单步骤计划。
8. 每一步先进入 Permission Gate。只读查询直接允许；Scheduler、图表/Dashboard、监控规则修改等副作用按 `step_id + tool_name` 请求用户确认；明显越权请求直接拒绝。
9. State Validation 检查工具是否存在、调度时间等必要参数和依赖结果。缺字段时保存阻塞状态并返回结构化澄清请求；用户补充后从 Checkpoint 的准确节点继续，而不是重跑前序步骤。
10. Executor 调用工具。`RecoveringToolNode` 把异常分为 timeout、429、服务不可用、权限、参数/Schema 等类别，仅对瞬时错误进行有上限的指数退避加抖动重试；所有结果生成结构化元数据和 Trace。
11. 大结果超过阈值时，原始结果写入文件型 Result Store，消息中仅保留 preview、哈希、路径、结构化 facts 和 `result_id`，避免巨量 JSON 挤占模型上下文，同时保持证据可追溯。
12. 满足严格白名单条件的单次、无歧义、未截断结果可由确定性 Fast Path 直接构造 `StepResult`；否则回到模型生成步骤摘要。Machine Validator 当前仅做 shadow 判定，不改变路由。
13. LLM Evaluator 对照该步骤的成功标准输出 `pass/retry/replan/partial/fail`。Retry、Replan 和总工具调用均有硬上限；非关键步骤失败可降级，关键步骤失败则终止或输出部分结果。
14. Answer Composer 使用用户问题、全部 StepResult、结构化证据、失败信息和数据边界生成最终答案；Planned 默认进入 Reviewer，Reviewer 最多要求有限次数修订。
15. 最终 AgentState、消息与执行 Trace 写入 Checkpoint；流式接口同时把状态、工具、回答 Token 等事件以 NDJSON 发给前端。

### 3. Agent 如何规划、执行、判断与恢复

- **路由**：不是所有问题都规划。简单解释或单工具查询走 Direct，复杂依赖、副作用和高风险任务走 Planned，以减少不必要模型调用。
- **规划**：Planner 生成包含目标、成功标准、依赖、建议工具、Fallback 和确认要求的结构化步骤；Validator 阻止不存在的工具和非法依赖进入执行。
- **权限**：权限判断由确定性规则完成，而不是让模型自行决定。审批只绑定当前步骤和当前工具，不能顺带授权其他副作用。
- **状态校验**：执行前检查缺失参数，并能从上一步 `structured_facts` 中识别交易哈希作为下一步 txid；不会把交易哈希误当地址。
- **工具恢复**：瞬时异常原参数重试；参数、Schema、权限类错误不机械重试。上层 Evaluator 可以根据反馈重新执行或改计划。
- **结果判断**：工具调用成功不等于业务步骤完成。Evaluator 按步骤成功标准判断，支持部分成功、失败和重新规划。
- **恢复**：等待审批或澄清时，状态留在 Checkpoint；恢复接口先校验待处理的 step/tool，再从指定节点继续。

### 4. Memory、Checkpoint、上下文压缩分别解决什么

| 机制 | 解决的问题 | 真实实现与边界 |
|---|---|---|
| Checkpoint | 同一会话跨请求保存完整执行状态，支持审批/澄清后续跑 | PG 模式可跨重启；内存模式重启即丢失；同一 thread 并发写为 last-writer-wins |
| Long-term Memory | 跨线程复用用户偏好、项目背景、决策等长期信息 | 支持手动保存/对话总结、Embedding 与用户过滤；当前触发 Gate 偏保守，不为每轮都召回 |
| ContextBuilder | 为 Router/Planner/Executor/Composer/Reviewer 分配不同 Token 预算和优先级 | 保护系统约束、当前请求、关键状态和证据，裁剪低优先级历史 |
| Rolling Summary | 长对话超预算时保留目标、约束、实体、计划、错误和审批状态 | 原始消息仍保留在 Checkpoint，只缩短送入模型的 active context；有 30/50 轮确定性测试 |
| Tool Result Compression | 工具返回巨量 JSON 时避免上下文爆炸 | Raw Result 落盘，模型只接收预览、facts、哈希和引用；有阈值、引用完整性与压缩测试 |

### 5. 核心数据流和状态流

```text
浏览器
  └─ JWT / 静态 Token → FastAPI chat route
       ├─ user_id + query → Memory Gate → Embedding Search → SystemMessage
       └─ thread_id → LangGraph Checkpointer → AgentState
            ├─ Router → Direct Executor ─┐
            └─ Planner → Permission → State Validation → Executor
                                         ├─ PostgreSQL / ClickHouse
                                         ├─ TRON / Ethereum RPC
                                         ├─ Web Search
                                         ├─ Plotly HTML / Dashboard
                                         └─ Scheduler / Monitor Rule
                              Tool Result → Raw Store + facts + provenance
                                   → Evaluator → Retry / Replan / Partial / Fail
                                   → Composer → Reviewer → Checkpoint + NDJSON

APScheduler → MonitorWorker → 增量交易游标 → 规则匹配
            → Notification Event（唯一约束）→ 用户飞书 Webhook
            → sent / failed + attempts
```

## 三、适合写简历的功能点逐项分析

### 功能 1：链上业务数据的多步骤查询与核验

1. **解决的问题**：业务数据库记录和链上最终状态分散，人工需要先查业务表、复制交易哈希，再访问节点核对。
2. **实现方式**：通过 Planned 执行把 PostgreSQL/ClickHouse 查询、结构化事实提取、TRON/Ethereum 节点核验和最终回答串联；State Validation 从前置结果绑定 txid，Evaluator 按每步成功标准决定继续、重试或重规划。
3. **技术难点**：多工具之间可靠传递参数；区分“工具成功”和“业务目标成功”；控制调用预算；对部分数据和失败场景给出可信边界。
4. **简历价值**：能把 Agent 技术落到清晰业务流程，体现编排、状态机和异构数据源整合能力，不是单轮 Tool Calling Demo。
5. **可能追问**：为什么要 Direct/Planned 两条路径？上一步 tx_hash 如何传给下一步？Evaluator 误判怎么办？怎样避免模型编造数据库覆盖范围？

状态：✅ 已实现。相关测试包括 routing、planning、state validation、graph routing、quality control、TRON lookup、ClickHouse 多数据源。

### 功能 2：后台持续交易监控与可靠飞书通知

1. **解决的问题**：用户无法持续人工刷新数据库，容易错过目标地址活动或大额交易。
2. **实现方式**：APScheduler 周期触发单个全局扫描任务；Worker 用 keyset 游标增量读取交易，再一次加载全部启用规则进行匹配。JustLend 使用 `(tx_seq,event_index)` 复合游标，保证同一交易的多个事件不被合并。匹配结果与新游标在同一数据库事务持久化，事件表以 `(rule_id,transaction_id)` 唯一约束去重；失败通知最多重试 5 次。PG advisory lock 避免多实例重复扫描。
3. **技术难点**：增量游标正确性、同交易多事件、规则数量与扫描次数解耦、并发实例互斥、消息幂等、通知失败状态管理、多用户 Webhook 路由。
4. **简历价值**：业务价值直接、系统设计内容丰富，明显区别于普通 Agent Demo，是最值得写的一条。
5. **可能追问**：为何用复合游标？事务在哪个范围？写事件后进程崩溃会怎样？为什么是 at-least-once 而非 exactly-once？Webhook 超时如何处理？

状态：✅ 扫描、规则匹配、游标、唯一约束、失败重试状态均已实现并有组件测试；⚠️ 真实飞书 Webhook 没有集成测试，不能声称生产投递成功率。

### 功能 3：长对话上下文压缩与证据可追溯

1. **解决的问题**：多轮对话和大查询结果会快速吃满模型上下文，简单截断又可能丢失用户约束、交易哈希或工具证据。
2. **实现方式**：ContextBuilder 对不同节点按优先级组装上下文；Rolling Summary 在 Token 阈值触发时把安全历史前缀压成结构化摘要，同时保留最近消息且不切断 AI Tool Call/Tool Result 配对；大工具结果写入 Result Store，只把 preview、facts、hash 和 result_id 送给模型。
3. **技术难点**：压缩边界、关键约束保留、摘要失败回退、主动/上下文超限后的被动压缩、原始证据与模型输入分离。
4. **简历价值**：体现对真实 Agent 成本、稳定性和可审计性的理解，比泛泛写“实现 Memory”更有说服力。
5. **可能追问**：为何不直接删除旧消息？怎样保证工具调用配对不被切断？摘要会不会幻觉？Raw Result 如何验证未被篡改？

状态：✅ 已实现并有 `test_context_builder.py`、`test_rolling_summary.py`、`test_tool_result_compression.py` 和 30/50 轮确定性评估支撑。⚠️ 长对话报告替换了外部 LLM，不能外推真实模型摘要的概率性保真率。

### 功能 4：副作用操作的确认、阻塞与断点恢复

1. **解决的问题**：模型可能未经用户确认创建任务、图表文件或监控规则，也可能在参数缺失时自行猜测。
2. **实现方式**：确定性 Permission Gate 将只读、副作用和危险意图分级；审批精确绑定 `step_id + tool_name`。State Validation 缺参时返回结构化澄清；状态写入 Checkpoint，审批或补参后从指定节点继续。
3. **技术难点**：安全规则不能依赖 LLM；授权范围要最小化；恢复时必须避免重复执行前序工具；取消/审批/澄清需要一致的状态迁移。
4. **简历价值**：体现安全边界和有状态工作流设计，适合后端/Agent 工程岗位。
5. **可能追问**：工具实际调用与计划工具不一致怎么办？如何防止替代工具绕过？Checkpoint 如何定位恢复节点？多用户能否审批同一 thread？

状态：✅ Gate、精确审批、澄清与恢复有代码和测试。⚠️ 当前审批接口没有校验 checkpoint 的 `user_id`，如果攻击者猜到其他人的 `thread_id`，会话归属边界不足；不能写“完善的多租户权限体系”。

### 功能 5：长期记忆与用户级召回

1. **解决的问题**：跨线程对话无法记住用户偏好、项目约束和历史决策，每次都要重复说明。
2. **实现方式**：支持手动保存和用模型总结线程消息；PostgreSQL/内存双 Store；语义召回前先用历史指代规则 Gate，之后用 Embedding 相似度检索并按用户过滤；召回异常自动降级，不阻断主请求。
3. **技术难点**：区分长期偏好与易过期事实、降低无关召回、用户隔离、旧表平滑迁移、Embedding/pgvector 不可用时降级。
4. **简历价值**：如果应聘 Agent 平台岗位值得写；若篇幅有限，优先级低于监控、长上下文和多工具链路。
5. **可能追问**：召回 Gate 为什么这样设计？阈值如何评估？如何删除或更新过期记忆？Memory 与 Checkpoint 有什么区别？

状态：✅ API、Store、总结、Embedding、用户过滤与测试均存在。⚠️ PostgreSQL 迁移异常被吞掉，旧 Schema 或 pgvector 不可用时写入仍成功但语义召回可能静默降级。

### 功能 6：自动化 Evaluation 与故障注入

1. **解决的问题**：Agent 输出具有随机性，仅靠普通断言难以衡量工具选择、参数、权限、恢复和性能。
2. **实现方式**：JSONL 用例定义 Ground Truth 和所需能力；Runner 支持适配器能力筛选、确定性检查、可选 LLM Judge、分类指标、延迟/Token/调用数统计和 Markdown/JSON 报告；故障代理可注入 timeout、429、参数错误和权限错误。
3. **技术难点**：把 Agent 质量拆成可测指标；区分 skipped 与 failed；从 Trace 聚合恢复和权限准确率；避免把组件测试包装成 HTTP E2E。
4. **简历价值**：适合强调工程质量和评测体系，但目前完整数据集测试有一个陈旧断言，应谨慎使用。
5. **可能追问**：Ground Truth 如何维护？LLM Judge 如何防偏差？fault injection 在哪一层？线上指标和离线指标如何对齐？

状态：⚠️ 框架和大量测试已实现，但数据集 34 条与测试硬编码 30 条不一致；Recovery HTTP Adapter 不支持 fault injection，部分报告仅证明组件层恢复，不能声称完整 E2E 故障恢复通过。

### 功能 7：图表和 Dashboard 生成

1. **解决的问题**：分析结果只返回表格不利于趋势和结构判断。
2. **实现方式**：使用 pandas/Plotly 生成多类交互式 HTML 图表，静态挂载 `/charts`；前端从流式回答和工具结果提取 URL 并展示。
3. **技术难点**：把查询结果转换成稳定的数据系列、文件路径与 URL 映射、多图布局、避免生成文件的副作用绕过确认。
4. **简历价值**：业务可感知，适合作为多工具分析链的一部分，不建议单独占核心亮点。
5. **可能追问**：图表数据从何而来？多用户文件名冲突和访问权限如何处理？如何清理旧文件？

状态：⚠️ 功能代码完整，但没有专门自动化测试；图表目录是公共静态目录，没有用户级访问隔离和生命周期清理。

## 四、最值得写的 5 个亮点（按简历价值排序）

1. **持续交易监控与通知可靠性**：真实业务场景最强，同时能讲游标、事务、唯一约束、失败状态、分布式锁和多用户通知。
2. **数据库到链上节点的多工具分析闭环**：突出产品实际能力，也能展开讲规划、依赖参数传递、步骤评估和数据边界。
3. **长上下文与大工具结果压缩**：体现真实 Agent 性能/稳定性问题，且测试证据较充分。
4. **副作用确认与 Checkpoint 断点恢复**：能体现安全和状态机设计，但面试时必须主动说明会话归属隔离仍需补强。
5. **Evaluation/故障恢复体系**：体现质量工程；建议描述为“搭建框架和组件级故障注入”，不要声称所有 E2E 场景通过。

长期 Memory 可作为第五条的替代项：应聘 Agent 应用岗位时保留 Memory；应聘后端/平台/测试开发岗位时优先 Evaluation。

## 五、最终简历版本

### A. 项目名称

**ChainCloud AI｜链上数据分析与智能监控平台**

### B. 项目介绍（约 64 字）

面向链上运营与风控的对话式分析平台，可协同查询业务数据库和链上节点、生成可视化结果，并支持定时执行、交易持续监控及飞书告警。

### C. 核心技术栈

Python、FastAPI、LangGraph、PostgreSQL/pgvector、ClickHouse、APScheduler、React/TypeScript、Plotly、pytest

### D. 项目经历（推荐版）

1. **实现地址及大额交易后台持续监控，采用复合增量游标批量扫描 JustLend 事件，以事务同步落库匹配事件与扫描位点，并通过唯一约束、失败状态重试和 PG advisory lock 保证多实例下的幂等处理。**

2. **搭建从业务库查询到 TRON/Ethereum 节点核验的多工具分析链路，将前序结果提取为可追溯结构化事实并绑定后续参数，按步骤成功标准执行重试、降级或重规划，避免把工具调用成功误判为任务完成。**

3. **针对长对话和大查询结果挤占模型上下文的问题，设计分节点 Token 预算与滚动摘要机制，按安全边界保留目标和约束；超阈值原始结果落盘并以哈希、预览和引用传递，兼顾上下文成本与证据追溯。**

4. **为调度、图表及监控规则等副作用操作设计确定性权限确认与缺参阻塞流程，将授权精确绑定步骤和工具，并结合 Checkpoint 在用户审批或补充参数后从原节点恢复，避免重复执行已完成步骤。**

5. **搭建面向 Agent 的离线评估与故障注入框架，覆盖工具选择、参数、权限、Memory、恢复与延迟/Token 指标；对 timeout、429、参数和权限异常进行分类，只对瞬时故障实施有界退避重试并输出可审计 Trace。**

#### 可替换的 Memory 版本

如岗位更看重 Agent 应用，可用下条替换第 5 条：

**实现跨线程长期记忆的保存、对话总结与用户级语义召回，通过历史指代 Gate、Embedding 相似度阈值和用户归属过滤减少无关注入，并在向量服务异常时降级，避免记忆模块阻断主对话。**

## 六、“真正实现”与“设计上存在”清单

### ✅ 已真实实现且有代码/测试支撑

- Direct/Planned 路由、结构化规划与计划校验。
- Planned 步骤的 Permission Gate、State Validation、Evaluator、Retry/Replan/Partial/Fail。
- PostgreSQL 只读限制、ClickHouse 多数据源、TRON 交易查询及条件化工具注册。
- Checkpoint 状态保存、权限确认和澄清后恢复。
- ContextBuilder、滚动摘要、大 Tool Result 落盘压缩与证据引用。
- 长期 Memory 的保存、总结、显式注入、自动语义召回和用户归属过滤。
- 监控规则草稿创建/修改/版本哈希确认、规则用户归属校验。
- 监控增量扫描、JustLend 复合游标、规则匹配、事件唯一约束、通知失败状态和扫描 advisory lock。
- NDJSON 流式聊天与前端执行时间线。
- Trace 与离线 Evaluation 框架。

### ⚠️ 部分实现或证据不足

- **完整多用户隔离**：Memory 查询和监控规则按用户隔离，但 Checkpoint 只按客户端 `thread_id` 命名；同 thread 跨用户/并发会发生归属覆盖或丢更新。
- **Scheduler 可靠持久化**：任务与结果使用本地 JSON/JSONL，能重启恢复未来任务，但不适合多实例一致性，也没有对应自动化测试。
- **飞书通知生产可靠性**：Webhook 发送、失败记录和最多 5 次重试已实现，但没有真实飞书集成测试，也没有指数退避或死信队列。
- **图表安全与运维**：Plotly HTML 生成和前端展示已实现，但公共静态目录无用户隔离、清理策略和专门测试。
- **Machine Validator**：已接入 shadow mode，只记录 `pass/fail/unknown`，不会跳过 LLM Evaluator，不能写成已经优化线上调用量。
- **Recovery E2E**：timeout/429 等组件级重试已验证，但 Evaluation HTTP Adapter 不支持故障注入，不能声称所有端到端恢复场景均通过。
- **语义 Memory**：pgvector 迁移或 Embedding 失败时静默降级；持久化记忆仍可用，但语义召回未必可用。
- **Evaluator 稳定性**：有测试和硬上限，但仍依赖 LLM 判断；不能声称完全确定性或零误判。

### ❌ 只存在于文档/设计中，或当前不能作为已完成成果

- 完善的多租户会话隔离、同 thread 并发串行化或乐观锁。
- 生产级消息队列、Exactly-once 通知、死信队列和通知 SLA。
- Machine Validator 自动替代 LLM Evaluator 的线上 fast path。
- 所有 Recovery case 的公开 HTTP 端到端故障注入验证。
- 基于当前仓库证据得出的生产 QPS、P95 延迟下降、Token 成本下降百分比、通知成功率等量化收益。
- README 所称“长上下文仍是占位”已经过时；当前代码事实上已实现，但简历应以代码和测试为证据，而不是引用该段 README。

## 七、逐条简历描述的面试风险提示

### 第 1 条：交易监控与通知

- **最可能追问**：游标为何不只用 tx_seq；事件和游标是否同事务；通知失败是否会漏发/重复；多实例如何互斥；规则很多时复杂度如何优化。
- **重点阅读**：`monitoring/worker.py` 的 `PostgresTransactionSource.scan/_scan_justlend`、`MonitorWorker.run_once/_scan_and_notify`；`monitoring/store.py` 的 `scan_lock`、`persist_matches_and_cursor`、`pending_events`；`notification/service.py`。
- **容易被问穿**：通知不是严格 exactly-once；发送成功后、标记 sent 前崩溃可能重复投递；当前规则匹配是 `交易数 × 规则数`；真实飞书未做集成测试。
- **保留建议**：强烈保留。面试时用“数据库事件幂等 + at-least-once 通知”表述，不要说 exactly-once。

### 第 2 条：多工具分析链路

- **最可能追问**：Router 规则；Planner 输出校验；dependency facts 怎样绑定；Retry 与 Replan 区别；Partial 如何进入最终回答。
- **重点阅读**：`agent/graph.py`；`agent/routing/router.py`、`routing/rules.py`；`agent/planning/planner.py`、`validator.py`；`agent/state_validation.py`；`agent/evaluation/evaluator.py`；`tools/registry.py`。
- **容易被问穿**：多数规划和评估仍依赖模型；没有生产质量指标；数据库只覆盖特定业务表，不是全链；外部工具是否启用取决于环境配置。
- **保留建议**：保留，但一定准备一个具体案例，例如“查跨链记录 → 提取 deposit_tx_hash → TRON 节点查本体和回执 → 汇总差异”。

### 第 3 条：长上下文与 Tool Result 压缩

- **最可能追问**：Token 如何计算和分配；何时 proactive/reactive compact；怎样保证 ToolMessage 不成为孤儿；Raw Result 如何引用；摘要失败怎么办。
- **重点阅读**：`agent/context_builder.py`、`agent/rolling_summary.py`、`agent/tool_results.py`、`agent/tool_recovery.py`；对应的 context/rolling/tool compression 测试和 `LONG_CONTEXT_COMPRESSION_EVALUATION_REPORT.md`。
- **容易被问穿**：30/50 轮测试用了确定性替身模型和较低触发阈值；只能证明机制正确，不能证明真实 LLM 总能无损总结；File Result Store 的清理与访问控制不足。
- **保留建议**：掌握压缩边界和降级策略就保留；若只会说“做了摘要”，建议删掉。

### 第 4 条：权限与 Checkpoint 恢复

- **最可能追问**：为何不用 LLM 判权限；审批键怎样生成；`aupdate_state(..., as_node=...)` 如何恢复；怎样防止重复调用和替代工具绕过。
- **重点阅读**：`agent/permission.py`、`agent/state_validation.py`、`api/routes/chat.py` 的 `approve_permission/submit_clarification`、`agent/graph.py` 的相关条件边、`tests/test_permission_gate.py`。
- **容易被问穿**：审批和澄清接口虽要求鉴权/静态 Token，但未验证 checkpoint 中的 `user_id` 属于当前用户；同 thread 并发也不安全。
- **保留建议**：可以保留“操作级确认与恢复”，不要写“多租户权限系统”。如果无法解释 thread 归属漏洞，应从简历删除或先补实现。

### 第 5 条：Evaluation 与故障恢复

- **最可能追问**：指标如何计算；skipped 和 failed 区别；FaultInjectingTool 注入位置；为何参数错误不自动重试；LLM Judge 的可靠性。
- **重点阅读**：`evaluation/runner.py`、`deterministic.py`、`metrics.py`、`faults.py`、`adapters.py`、`eval/test_cases.jsonl`、`tests/test_evaluation_framework.py`、`tests/test_tool_recovery.py`、`RECOVERY_FAULT_INJECTION_STATUS_REPORT.md`。
- **容易被问穿**：当前全量测试有 1 个数据集数量断言失败；HTTP Adapter 会跳过 fault injection；invalid-argument 组件测试最终为 failed，只证明不机械重试，没有证明端到端自动修参成功。
- **保留建议**：若能主动讲清这些边界则保留，会显得真实且专业；若准备不足，换成 Memory 条目更安全。

### Memory 替换条目

- **最可能追问**：Memory 与 Checkpoint 区别；Gate、候选数、相似度阈值；用户隔离；过期事实处理；pgvector 不可用时行为。
- **重点阅读**：`memory/service.py`、`memory/store.py`、`memory/factory.py`、`api/routes/memory.py`、`api/routes/chat.py::_build_input_messages`，以及 automatic recall/memory route/store 测试。
- **容易被问穿**：召回策略是规则 Gate + 向量相似度，没有重排序模型；迁移和 Embedding 异常会被降级处理；相同 thread 的 Checkpoint 仍未按用户 namespace。
- **保留建议**：应聘 Agent 应用岗可保留；应聘纯后端岗优先保留监控和可靠性条目。

## 八、建议的面试讲述主线

不要按 Planner、Memory、Evaluator 名词逐个讲。建议用一个业务故事串起来：

“用户想找出某段时间的大额 JustLend 交易并持续关注。系统先查询业务库，必要时用交易哈希到 TRON 节点核验，再生成图表；如果用户把一次分析升级为持续监控，系统先给出规则草稿并要求确认，随后后台使用复合游标只扫描新增事件，命中后以唯一约束落通知事件并发送飞书。为了让长任务稳定运行，我又处理了上下文预算、大结果落盘、瞬时工具重试和 Checkpoint 恢复。当前边界是同 thread 并发和跨用户 namespace 仍需补强，通知是 at-least-once，而非 exactly-once。”

这条主线同时说明了产品做什么、技术难点在哪里，以及你对系统边界是否真正理解。
