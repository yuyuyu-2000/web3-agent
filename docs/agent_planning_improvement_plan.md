# Agent Planning 能力改进计划

## 1. 背景与现状

ChainCloud-AI 当前的核心执行链路是基于 LangGraph 的单一 ReAct 工具循环：

```text
用户问题 → Agent 判断并调用工具 → 工具返回 → Agent 再判断
                                      ↑              ↓
                                      └────循环──────┘
                                             ↓
                                      Answer Composer
```

当前 `AgentState` 主要保存 `messages`，图中的核心流程为：

```text
agent → tools → agent → compose_answer
```

现有 `answer_composer` 虽然包含最终答案组织逻辑，但它解决的是回答详略、证据呈现和最终渲染问题，并不负责：

- 将复杂目标拆解成多个步骤；
- 管理步骤之间的依赖关系；
- 跟踪任务执行进度；
- 判断每一步是否真正完成；
- 在工具失败或信息变化后重新规划；
- 在中断恢复后继续未完成任务。

因此，对于单工具查询或简单问答，当前结构足够轻量；但面对多数据源核验、多阶段链上分析、报告生成等复杂任务时，容易出现目标漂移、重复调用、遗漏步骤以及失败后无法有效恢复等问题。

## 2. 改进目标

本次改进建议引入“简单请求走快速路径，复杂请求进入规划路径”的分层架构。

主要目标：

1. 保留当前简单请求的响应速度和较低成本；
2. 为复杂请求生成结构化、可执行、可追踪的计划；
3. 将计划和执行进度纳入 LangGraph State 与 checkpoint；
4. 支持步骤验证、有限重试和有限重新规划；
5. 为工具调用设置次数、时间和副作用约束；
6. 保持现有 `/chat` API 的向后兼容；
7. 不向用户暴露模型的隐藏思维链，只展示可理解的任务步骤和进度。

## 3. 推荐总体架构

> 当前实施决策（2026-08-07）：第一版暂不引入 Router。所有新请求都先经过
> Planner，简单请求生成单步骤计划，复杂请求生成多步骤计划。待积累计划耗时、
> 成本和步骤数量等真实数据后，再决定是否增加 direct/planned 路由。

第一版实际链路为：

```text
用户请求 → Planner → Select Step → Executor ↔ Tools
                              ↑           ↓
                              └─ Complete Step
                                      ↓
                              Answer Composer
```

第一版包含结构化计划、依赖校验、单步执行、工具调用预算、checkpoint 状态、
副作用步骤确认和流式进度事件。第一版不包含模型驱动的独立 Evaluator、动态
Replan、并行步骤和 Router。

以下带 Router 的架构作为后续演进方向保留。

```text
                         ┌── simple ──→ Agent/Tools ─────────┐
用户请求 → 任务分类 Router                              Answer Composer
                         └── complex → Planner → Executor ──┤
                                               ↑       ↓    │
                                               └ Evaluator ┘
```

各模块职责如下：

| 模块 | 职责 |
|---|---|
| Router | 判断请求走直接执行还是规划执行路径 |
| Planner | 将复杂目标转换为结构化计划，不直接执行工具 |
| Executor | 一次执行一个计划步骤，并记录结构化结果 |
| Evaluator | 检查步骤是否满足成功标准，决定继续、重试或重规划 |
| Answer Composer | 基于计划、步骤结果和证据生成最终回答 |
| Checkpoint | 保存对话、计划和执行进度，支持恢复 |

## 4. 状态模型改进

当前状态主要包含 `messages`。建议增加独立的结构化任务状态，避免系统只能从消息历史中推测执行进度。

示意定义：

```python
class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    execution_mode: Literal["direct", "planned"]
    plan: Plan | None
    current_step: int
    step_results: list[StepResult]
    replanning_count: int
    tool_call_count: int
    status: Literal[
        "running",
        "waiting_confirmation",
        "completed",
        "failed",
        "partial",
    ]
    final_evidence: list[Evidence]
```

这里的 `plan`、`step_results` 和计数器应作为独立状态进入 checkpoint，而不是只写入自然语言消息。

## 5. 结构化计划设计

计划应使用稳定的数据结构，而不是一段自由文本。

示例：

```json
{
  "goal": "分析某地址近期资金流向及风险",
  "steps": [
    {
      "id": "step_1",
      "objective": "确认地址所属链及基础信息",
      "suggested_tools": ["ethereum_jsonrpc"],
      "depends_on": [],
      "success_criteria": "取得链上基础数据",
      "requires_confirmation": false
    },
    {
      "id": "step_2",
      "objective": "查询主要转入转出记录",
      "suggested_tools": ["clickhouse_select"],
      "depends_on": ["step_1"],
      "success_criteria": "获得可核验的交易明细",
      "requires_confirmation": false
    }
  ]
}
```

建议每个步骤至少包含：

- 唯一 ID；
- 步骤目标；
- 前置依赖；
- 建议工具；
- 成功标准；
- 是否需要用户确认；
- 可选的重试上限和预估成本。

计划只表达“要完成什么”和“如何判断完成”，具体工具参数由 Executor 在执行当前步骤时生成。

## 6. 复杂度路由 Router

并非所有请求都需要 planning。建议 Router 采用“确定性规则优先，模型判断补充”的方式，降低额外调用成本和错误路由概率。

### 6.1 直接执行路径

以下请求通常继续走当前快速路径：

- 单次数据库查询；
- 查询某笔交易或某个地址的单项信息；
- 简单知识问答；
- 单一工具可以可靠完成的问题；
- 用户明确要求快速、简短回答的问题。

### 6.2 规划执行路径

以下情况建议进入 Planner：

- 用户明确要求多阶段分析；
- 需要两个以上数据源交叉核验；
- 步骤存在前后依赖；
- 需要生成图表、报告或定时任务；
- 请求目标较模糊，需要识别关键缺失信息；
- 需要链上数据、内部数据库和公开信息联合分析；
- 第一次直接执行失败，需要升级到 planned 模式。

项目现有的答案复杂度判断可以作为参考，但任务复杂度和答案详略是两个不同概念，建议建立独立的 `task_router`，不要直接复用 Answer Composer 的复杂度逻辑。

## 7. Planner 职责

Planner 只负责制定计划，不直接执行工具。

主要职责：

- 理解用户的最终目标；
- 判断是否缺少必须由用户提供的信息；
- 将目标拆分为有依赖关系的步骤；
- 为每一步定义成功标准；
- 推荐可用工具；
- 标记可能产生副作用的步骤；
- 给出合理的步骤数和调用预算。

Planner 不应该：

- 直接执行工具；
- 直接创建定时任务；
- 在计划中生成未经验证的结论；
- 输出或保存模型的隐藏思维链；
- 制定无限步骤或没有终止条件的计划。

## 8. Executor 职责

Executor 每次只处理一个当前步骤，读取该步骤目标、依赖结果和允许使用的工具，然后生成具体工具调用。

每一步完成后输出统一的 `StepResult`：

```json
{
  "step_id": "step_2",
  "status": "success",
  "summary": "取得最近 30 天的主要转账记录",
  "evidence": [],
  "tool_calls": [],
  "error": null
}
```

建议的步骤状态包括：

- `pending`：尚未执行；
- `running`：正在执行；
- `success`：满足成功标准；
- `failed`：执行失败且不可继续；
- `partial`：取得部分有效结果；
- `skipped`：因前置条件变化而跳过；
- `waiting_confirmation`：等待用户确认副作用操作。

通过独立保存步骤结果，可以避免模型在每一轮重新阅读全部历史并猜测当前进度，也便于恢复、审计和最终答案生成。

## 9. 工具元数据与安全约束

当前工具注册主要提供工具列表。为了让 Planner 和 Executor 做出稳定决策，建议逐步补充工具元数据：

```text
read_only
cost
timeout
retryable
requires_confirmation
data_source
result_limit
```

示例分类：

| 工具类型 | 默认策略 |
|---|---|
| 数据库只读查询 | 可自动执行，但限制结果行数和超时 |
| 链上 RPC 查询 | 可自动执行，允许有限重试 |
| Web Search | 可自动执行，限制查询次数和结果数量 |
| 图表生成 | 可自动执行，限制输入规模 |
| 定时任务创建 | 需要用户确认 |
| 未来可能出现的写入类工具 | 默认需要用户确认 |

对于有副作用的工具，Planner 只能提出步骤，系统进入 `waiting_confirmation` 后由用户确认，Executor 才能继续执行。

## 10. Evaluator 与有限重规划

工具成功返回不代表任务已经完成。Evaluator 应依据当前步骤的 `success_criteria` 检查：

- 结果是否为空或明显不完整；
- 结果是否被截断；
- 是否取得了步骤要求的关键字段；
- 多个数据源之间是否存在冲突；
- 证据是否足以支持结论；
- 原计划的下一步是否仍然成立。

Evaluator 可输出：

| 结果 | 后续动作 |
|---|---|
| `pass` | 执行下一步 |
| `retry` | 修正参数后重试当前步骤 |
| `replan` | 保留已完成结果，重写剩余计划 |
| `partial` | 无法继续，但已有部分可靠结果 |
| `ask_user` | 缺少只能由用户补充的信息 |
| `fail` | 任务无法完成 |

重新规划时应保留：

- 原始用户目标；
- 已完成步骤；
- 已确认的证据；
- 失败原因；
- 剩余工具预算。

Planner 只重写尚未执行的部分，避免重复执行已完成步骤。

## 11. 执行预算与终止条件

为了防止复杂任务无限循环，建议设置明确的运行上限。初始默认值可以是：

- 最多 8 个计划步骤；
- 最多 15 次工具调用；
- 单步骤最多重试 2 次；
- 最多重新规划 2 次；
- 设置总执行超时；
- 禁止连续使用相同参数重复调用同一工具；
- 达到预算后返回 `partial`，并明确列出已完成和未完成部分。

这些值后续应通过实际 trace 和成功率数据调整，并允许通过配置覆盖。

## 12. Answer Composer 改进

最终答案不应再主要依赖完整消息历史，而应优先消费结构化执行结果：

```text
原始目标
+ 最终计划
+ StepResults
+ Evidence
+ 未完成项
+ 风险和限制
```

推荐最终回答区分：

- 已确认事实；
- 基于数据的推断；
- 数据源之间的冲突；
- 未能验证的部分；
- 后续建议。

项目已有 Answer Composer、Evidence 和 Renderer 基础，这一部分可以渐进改造，不需要整体推翻。

如果 planned 模式失败，Answer Composer 仍应基于已有 `StepResult` 生成部分回答，而不是简单返回通用错误。

## 13. API 兼容策略

现有 `/chat` API 可以保持兼容。后续可为请求增加可选字段：

```json
{
  "thread_id": "example-thread",
  "message": "分析这个地址近期资金流向",
  "planning": "auto"
}
```

建议支持：

- `auto`：默认，由 Router 自动判断；
- `off`：始终走当前快速路径；
- `force`：强制进入规划路径。

如果不传 `planning`，行为等同于 `auto`，以保证旧客户端不需要修改。

普通 `/chat` 响应也可以保持现有 `reply` 结构；计划详情只在 debug 模式或新的可选响应字段中提供。

## 14. 流式进度事件

当前流式接口主要返回宽泛的思考和工具执行状态。后续可以增加以下 NDJSON 事件：

```json
{"type":"plan_created","steps":[...]}
{"type":"step_started","step_id":"step_2","title":"查询资金流"}
{"type":"step_completed","step_id":"step_2"}
{"type":"plan_updated","reason":"原数据源无结果"}
{"type":"confirmation_required","step_id":"step_4"}
```

前端只展示用户可理解的步骤摘要，不展示内部提示词、隐藏推理过程或敏感工具参数。

为了保持兼容，现有 `status`、`delta`、`done` 和 `error` 事件可以继续保留。

## 15. Checkpoint 与中断恢复

planning 状态应随 LangGraph checkpoint 一起保存，至少包括：

- 当前执行模式；
- 当前计划；
- 当前步骤；
- 每一步的执行结果；
- 工具调用和重规划计数；
- 等待确认状态；
- 已收集证据。

恢复任务时，应先验证：

1. 当前计划是否存在；
2. 当前步骤是否已经完成；
3. 上次中断是否发生在工具调用之前或之后；
4. 有副作用的工具是否可能已经成功执行；
5. 剩余预算是否足够继续。

对于副作用工具，需要考虑幂等键或执行记录，避免恢复后重复创建任务。

## 16. 可观测性与评估指标

现有 trace 主要记录工具请求和结果。引入 planning 后，建议增加：

- 路由结果及原因；
- 计划版本；
- 每个步骤的开始、结束和耗时；
- Evaluator 决策；
- retry/replan 原因；
- 工具调用预算使用情况；
- 最终完成状态；
- 敏感参数脱敏后的错误摘要。

建议长期跟踪以下指标：

- 简单请求被错误路由到 planned 模式的比例；
- 复杂任务完成率；
- 平均计划步骤数；
- 平均工具调用次数；
- 单步骤重试率；
- 重规划率；
- 任务部分完成率；
- 用户取消率；
- 模型调用成本和整体耗时。

## 17. 测试策略

### 17.1 单元测试

- Router 对简单、复杂和边界请求的分类；
- Planner 输出的结构化校验；
- 无效步骤 ID、循环依赖和超限计划拒绝；
- Executor 只执行当前步骤；
- Evaluator 各种决策分支；
- 工具调用计数和重试上限；
- 副作用工具确认；
- Answer Composer 对完整、部分和失败结果的渲染。

### 17.2 图流程测试

- direct 路径保持当前行为；
- planned 路径完整执行；
- 工具失败后重试；
- 重试失败后 replan；
- 达到预算后 partial 结束；
- 等待确认后从 checkpoint 恢复；
- 服务重启后继续未完成任务。

### 17.3 回归测试

- 现有 `/chat` 请求格式继续可用；
- 现有流式事件继续可用；
- memory 注入不受影响；
- Answer Composer 现有轻量/详细风格不受影响；
- trace 脱敏规则继续生效；
- PostgreSQL 和内存 checkpoint 均能运行。

## 18. 推荐实施阶段

### 第一阶段：最小可用 Planning

1. 定义 `Plan`、`PlanStep` 和 `StepResult`；
2. 扩展 LangGraph State；
3. 增加 Planner，所有请求均生成计划；
4. 简单请求使用单步骤计划；
5. 增加单步骤 Executor；
6. Executor 暂时复用现有 Agent 和 ToolNode；
7. 加入步骤和工具调用上限；
8. 保持 `/chat` 请求及响应兼容；
9. 记录 Planner 的耗时和计划规模，为后续 Router 提供依据。

第一阶段应先解决任务拆解、进度保存和有界执行问题，不急于实现复杂的自动反思。

### 第二阶段：可靠性增强

1. 增加 Evaluator；
2. 支持有限 replan；
3. 对工具错误进行分类；
4. 增加副作用工具确认机制；
5. 完善 checkpoint 中断恢复；
6. 扩展 planning trace；
7. 基于结构化结果改进 Answer Composer。

### 第三阶段：产品化能力

1. 前端展示计划和步骤进度；
2. 支持用户取消任务；
3. 支持用户修改剩余计划；
4. 支持长任务后台运行；
5. 建立任务执行统计和成本指标；
6. 根据真实数据优化 Router 和默认预算。

## 19. 建议的模块边界

后续实现时可以考虑以下目录结构：

```text
agent/
├── graph.py
├── state.py
├── routing/
│   ├── router.py
│   └── complexity.py
├── planning/
│   ├── models.py
│   ├── planner.py
│   ├── prompts.py
│   └── validator.py
├── execution/
│   ├── executor.py
│   ├── evaluator.py
│   ├── budget.py
│   └── errors.py
└── answer_composer/
```

具体拆分可以在实现时根据代码量调整。重点是保持职责边界清晰，避免继续把路由、规划、执行和答案生成全部集中到 `graph.py`。

## 20. 需要避免的问题

- 不要只在系统提示词中加入“请先制定计划”；
- 不要让所有请求都经过 Planner；
- 不要把计划只保存为普通消息；
- 不要让 Planner 直接执行工具；
- 不要让 Planner 和 Executor 共用一个无约束提示词；
- 不要允许无限重试或无限 replan；
- 不要将模型隐藏思维链作为计划返回前端；
- 不要让有副作用的工具被复杂计划静默执行；
- 不要因某一步失败而丢弃所有已取得的可靠结果；
- 不要一次性重写现有工具和 API，应采用渐进式演进。

## 21. 最终建议

推荐采用：

```text
Router
+ 结构化 Planner
+ 单步骤 Executor
+ Evaluator
+ 有限 Replan
+ 执行预算
+ Checkpoint 恢复
```

同时保留当前 ReAct 工具循环作为简单请求的快速路径。

这一方案与项目现有的 LangGraph、ToolNode、checkpoint、Answer Composer 和 trace 结构能够自然衔接，可以分阶段落地，不需要重写现有工具体系。第一阶段优先实现“结构化计划、步骤执行、进度持久化和明确终止条件”，在积累真实执行数据后，再逐步增加自动评估、重规划和前端任务管理能力。
