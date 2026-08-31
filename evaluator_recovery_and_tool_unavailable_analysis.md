# Evaluator Recovery Decision 与工具不可用恢复设计分析

## 1. 结论摘要

当前项目的实际情况可以概括为：

1. **Evaluator 确实承担一部分 Recovery Decision**，但它只负责 `StepResult` 产生之后的步骤级语义恢复决策，并不是整个 Agent 的统一 Recovery Controller。
2. **当前 `state_validation` 发现工具不可用时不会进入 Evaluator**，而是进入 `blocked_missing_state`，最终按照 `fail` 路径结束。
3. 如果为 `state_validation` 增加结构化的 `blocked_tool_unavailable`，仅增加返回字段还不够；还必须修改 Graph 路由、Evaluator 输入上下文和恢复决策模型。
4. 即使把工具不可用信息接入现有 Evaluator，Evaluator 也只能给出较粗粒度的 `retry/replan/partial/fail` 建议，不能仅凭当前输入保证规划建议正确。
5. 更合理的职责划分是：
   - 确定性、可机器证明的 fallback 选择由代码实现的 Recovery Policy 处理；
   - 是否还能满足步骤语义、是否需要重新规划等问题由 Evaluator 判断；
   - 真正的新计划仍由 Planner/Replanner 生成。

---

## 2. Evaluator 当前是否承担 Recovery Decision

当前 Evaluator 的结构化输出动作是：

```text
pass | retry | replan | partial | fail
```

其中以下动作明显属于恢复决策：

- `retry`：当前步骤目标仍合理，可以通过修正参数、补充查询或使用 fallback 再次执行；
- `replan`：当前步骤或后续路径已经不适用，需要重新规划剩余任务；
- `partial`：无法完全恢复，但已有可靠结果，可以保留部分结果并降级结束；
- `fail`：没有可靠结果，并且继续执行已无意义。

因此，可以将当前 Evaluator 定位为：

> **步骤级语义恢复决策者。**

典型流程如下：

```text
ToolResult(argument_error)
    ↓
Executor 读取结构化错误
    ↓
Executor 修正参数，或生成失败的 StepResult
    ↓
Evaluator
    ├─ retry
    ├─ replan
    ├─ partial
    └─ fail
```

但是，Evaluator 并不是所有恢复行为的统一入口。当前项目采用的是分散式 Recovery：

| 故障类型 | 当前负责处理的组件 |
|---|---|
| timeout、429、连接错误、502/503 | `RecoveringToolNode` 自动重试 |
| 参数错误、Schema 错误 | Executor 先尝试语义修复 |
| StepResult 不满足成功标准 | Evaluator 决定 `retry/replan/partial/fail` |
| Permission 错误 | 直接进入 `permission_failure` |
| State Validation 缺少参数 | 进入用户澄清流程 |
| State Validation 发现工具不可用 | 直接进入失败路径 |
| 工具调用预算耗尽 | 直接进入 Reviewer/Composer |
| Planner 输出非法 | Planner Validator 带反馈重试一次 |

所以更准确的表述是：

> 当前项目是分散式 Recovery 架构；Evaluator 只承担执行结果产生后的步骤级语义恢复决策。

---

## 3. 工具出错后是否一定由 Evaluator 发现

不一定。多数情况下，Executor 会先看到并处理工具错误。

工具执行之后，Graph 的主要路由为：

```text
tools
  ↓
deterministic_step_result
  ├─ 命中 → machine_step_validator → evaluator
  └─ 拒绝 → executor
```

当工具调用存在未解决错误时，deterministic fast path 会因为 `last_tool_errors` 而拒绝直接构造确定性 `StepResult`，随后回到 Executor。

Executor 能看到：

- `last_tool_errors`；
- 错误类型；
- 错误是否可重试；
- Evaluator 上一轮反馈；
- 当前 PlanStep 声明的 fallback；
- 参数修复、Schema 查询及权限约束。

Executor 可以采取以下动作：

- 修改参数后再次调用原工具；
- 在确实发生 Schema 错误后查询表结构，再修正查询；
- 调用 Planner 声明的 fallback；
- 补充其他必要查询；
- 判断无法继续，生成失败或部分步骤结果。

只有当 Executor 不再生成 ToolCall，而是生成当前步骤的总结时，`complete_step` 才会创建 `StepResult` 并交给 Evaluator。

因此，当前职责关系是：

```text
ToolNode
负责处理瞬时、可机械恢复的底层故障
        ↓
Executor
负责实施具体的语义恢复操作
        ↓
Evaluator
负责验收恢复结果，并决定是否再次执行、重新规划或终止
```

---

## 4. 当前 State Validation 工具不可用路径

当前 `state_validation` 会检查 PlanStep 中的 `suggested_tools` 是否都存在于当前可用工具集合中。

如果发现工具不可用，它会产生类似以下决策：

```text
action = MISSING
resolution = fail
reason = 计划引用了当前不可用的工具
```

Graph 路由为：

```text
state_validation
  ├─ VALID → executor
  └─ MISSING → blocked_missing_state
                    ├─ clarification → 暂停，等待用户补充
                    ├─ partial → review/composer
                    └─ fail → review/composer
```

当前不存在以下路由：

```text
blocked_missing_state → evaluator
```

另外，Evaluator 当前要求的输入为：

```python
evaluate_step(
    step: PlanStep,
    result: StepResult,
)
```

State Validation 产生的是 `StateValidationDecision`，并不是 `StepResult`，也不会设置 `candidate_step_result`。

因此，如果只让 State Validation 返回：

```json
{
  "type": "blocked_tool_unavailable",
  "fallback_candidates": ["tool_b"],
  "recoverable": true
}
```

但不改变 Graph 路由，执行结果仍然是：

```text
blocked_missing_state → fail/partial
```

Evaluator 根本看不到这些新增字段。

---

## 5. 将 blocked_tool_unavailable 接入现有 Evaluator 的可行性

### 5.1 可以给出粗粒度建议

如果把 State Validation 的阻塞信息转换成一个失败的 `StepResult`，例如：

```json
{
  "step_id": "step_2",
  "status": "failed",
  "summary": "计划主工具当前不可用",
  "error": {
    "error_type": "blocked_tool_unavailable",
    "unavailable_tools": ["tool_a"],
    "fallback_candidates": ["tool_b"],
    "recoverable": true
  }
}
```

同时，Evaluator 可以从 `PlanStep` 中看到：

- `suggested_tools`；
- `fallback_tools`；
- `critical`；
- `success_criteria`；
- `depends_on`。

理论上，Evaluator 可以输出：

```json
{
  "action": "retry",
  "reason": "主工具不可用，但存在已声明的可用 fallback",
  "feedback": "使用 fallback tool_b 重新执行当前步骤",
  "confidence": 0.9
}
```

或者：

```json
{
  "action": "replan",
  "reason": "当前主工具和 fallback 均不可用",
  "feedback": "根据剩余可用能力重新规划未完成任务",
  "confidence": 0.8
}
```

因此，从能力上说，Evaluator 可以选择 `retry`、`replan`、`partial` 或 `fail`。

### 5.2 不能保证建议正确

现有 Evaluator 实际只看到：

```json
{
  "step": "PlanStep",
  "candidate_result": "StepResult"
}
```

它看不到完整的运行上下文，包括：

- 当前可用工具目录及每个工具的能力描述；
- `fallback_candidates` 是否真的已注册；
- fallback 是否与主工具语义等价；
- fallback 是否能完整满足 `success_criteria`；
- 完整 Plan 及尚未执行的后续步骤；
- 所有已完成步骤的结构化结果；
- 当前全局工具调用预算；
- 当前步骤剩余调用预算；
- 当前权限状态；
- 某个 fallback 是否会引入新的副作用或确认要求；
- 当前 Step 已重试多少次；
- 当前任务已 Replan 多少次。

虽然后续 `evaluator_node` 会使用代码限制最大 retry 和 replan 次数，但 LLM Evaluator 作出决定时并不知道这些预算。

这可能导致以下错误建议：

- 推荐一个当前同样不可用的 fallback；
- 对无法通过重复执行恢复的能力缺失返回 `retry`；
- 对可以直接切换 fallback 的情况不必要地返回 `replan`；
- 推荐语义相似、但权限边界不同的替代工具；
- 忽略当前步骤对后续步骤的依赖影响；
- 在仍有可执行独立步骤时过早选择 `partial`；
- 在没有任何可靠结果时错误地认为可以降级完成。

因此：

> 把 `blocked_tool_unavailable` 交给现有 Evaluator，可以得到恢复方向，但不能保证得到正确、可执行且符合权限约束的具体规划建议。

---

## 6. 当前 EvaluationDecision 表达能力的限制

当前 `EvaluationDecision` 结构为：

```python
class EvaluationDecision(BaseModel):
    action: Literal["pass", "retry", "replan", "partial", "fail"]
    reason: str
    feedback: str
    confidence: float
```

其中 `feedback` 是自然语言，不能明确表达具体恢复操作，例如：

```json
{
  "selected_fallback": "tool_b",
  "unavailable_tools": ["tool_a"],
  "retry_strategy": "switch_tool",
  "forbidden_tools": ["tool_a"]
}
```

这意味着，即使 Evaluator 建议使用 fallback，真正执行时仍然需要 Executor 从自然语言反馈中重新理解：

- 应调用哪个工具；
- 应该使用什么参数；
- 是否需要重新经过 Permission Gate；
- 是否需要保留或修改原步骤目标。

这种方式适合开放式语义修复，但不适合可以确定性表达的恢复指令。

---

## 7. 更合理的职责边界

不建议让 State Validation 自己执行 fallback，因为 State Validator 的职责应当是检查执行前状态，而不是执行工具。

也不建议把所有工具不可用恢复都交给 LLM Evaluator，因为部分恢复可以被代码确定性证明。

更合理的职责划分如下。

### 7.1 State Validator

负责发现执行前问题，并产生结构化事实：

```json
{
  "action": "BLOCKED",
  "error_type": "tool_unavailable",
  "stage": "pre_execution",
  "unavailable_tools": ["tool_a"],
  "declared_fallbacks": ["tool_b", "tool_c"],
  "available_fallbacks": ["tool_b"],
  "recoverable": true,
  "reason": "主工具当前未注册"
}
```

它只描述问题，不直接调用工具。

### 7.2 Deterministic Recovery Policy

负责处理能够由代码确定的恢复：

```text
主工具不可用
+ fallback 已由 Planner 声明
+ fallback 当前已注册
+ fallback 不违反权限规则
+ fallback 能力契约满足当前步骤类型
        ↓
retry_with_fallback
selected_tool = tool_b
```

这些信息能够机器验证时，不需要让 LLM 猜测是否应该切换工具。

### 7.3 Evaluator 或 Recovery Evaluator

只处理需要语义判断的问题：

- fallback 是否仍能满足自然语言 `success_criteria`；
- 缺少当前数据后是否还能生成可靠的部分答案；
- 当前步骤是否应重新定义；
- 后续步骤是否仍然成立；
- 应该 `replan`、`partial` 还是 `fail`。

### 7.4 Executor

根据经过验证的恢复指令真正执行：

- 修正参数；
- 调用指定 fallback；
- 补充查询；
- 遵守 Permission Gate 和调用预算。

### 7.5 Planner/Replanner

当恢复动作是 `replan` 时，真正生成新计划。Evaluator 只决定是否需要重新规划，并提供约束和反馈，不应该自己生成完整新计划。

推荐的概念流程为：

```text
State Validation
    ↓
blocked_tool_unavailable
    ↓
Deterministic Recovery Policy
    ├─ 已声明 fallback 可用且权限合法
    │      → retry_with_fallback
    │      → Permission Gate
    │      → Executor
    │
    ├─ 没有确定性 fallback，但可能存在替代路径
    │      → Recovery Evaluator
    │          ├─ replan → Replanner
    │          ├─ partial → Composer/Reviewer
    │          └─ fail → Composer/Reviewer
    │
    └─ 明确无任何可用能力且为关键步骤
           → fail
```

这体现了以下原则：

> 检测机制分层，错误表示统一，确定性恢复由代码完成，语义恢复由 Evaluator 决策，具体计划由 Planner 生成。

---

## 8. 如果让 Evaluator 参与，需要补充的内容

### 8.1 增加 Graph 路由

当前缺少：

```text
blocked_tool_unavailable → evaluator
```

可以选择：

```text
blocked_tool_unavailable → evaluator
```

或者建立职责更加明确的新节点：

```text
blocked_tool_unavailable → recovery_evaluator
```

后者更清晰，因为“验收已经执行的 StepResult”和“判断执行前能力缺失如何恢复”并不是完全相同的问题。

### 8.2 给恢复判断提供结构化上下文

至少应包含：

```json
{
  "failure": {
    "stage": "pre_execution",
    "error_type": "tool_unavailable",
    "unavailable_tools": ["tool_a"],
    "recoverable": true
  },
  "declared_fallbacks": ["tool_b", "tool_c"],
  "available_fallbacks": ["tool_b"],
  "rejected_fallbacks": [
    {
      "tool": "tool_c",
      "reason": "not_registered"
    }
  ],
  "remaining_tool_catalog": [],
  "permission_constraints": {},
  "retry_budget_remaining": 2,
  "replan_budget_remaining": 1,
  "completed_step_summaries": [],
  "remaining_plan_steps": []
}
```

### 8.3 使用结构化 Recovery Decision

不应只依靠自然语言 `feedback`，可以增加类似结构：

```json
{
  "action": "retry_with_fallback",
  "selected_tool": "tool_b",
  "reason": "主工具不可用，已声明的等价 fallback 当前可用",
  "constraints": [
    "不得再次调用 tool_a",
    "必须重新经过 Permission Gate"
  ]
}
```

在真正执行之前，代码还应再次验证：

- `selected_tool` 是否已注册；
- 是否属于 Planner 声明或 Recovery Policy 允许的工具；
- 是否满足权限规则；
- 是否超过步骤或全局预算；
- 是否会绕过此前的 Permission/Guardrail 拒绝。

### 8.4 对恢复判断采用 Fail-closed

当前 `evaluate_step()` 在 Evaluator 调用或输出解析异常时会返回：

```python
EvaluationDecision(
    action="pass",
    reason="Evaluator 不可用，保留原有的非空结果判定",
    confidence=0.0,
)
```

这是 fail-open 行为。

对于普通非空执行结果，它是兼容性降级；但对于 `blocked_tool_unavailable`，如果复用这一逻辑，Evaluator 不可用时返回 `pass` 会有明显风险，因为当前步骤实际上没有获得成功执行结果。

虽然现有 `evaluator_node` 会根据 `candidate.status != "success"` 对部分错误的 `pass` 动作进行纠正，但预执行恢复不应该依赖这种间接保护。

更合理的处理是：

```text
Recovery Evaluator 不可用
    ↓
不执行未经确定性验证的替代工具
    ↓
有安全的 deterministic fallback → 使用 fallback
否则 → clarification / replan / partial / fail
```

即：恢复判断应当 fail-closed。

---

## 9. 对“正确规划建议”的准确理解

Evaluator 适合回答的问题是：

> 当前步骤应该继续重试、重新规划、保留部分结果，还是失败？

Evaluator 不适合单独回答的问题是：

> 当前系统具体应该调用哪个工具、使用什么参数，并生成完整的新计划。

原因是：

1. Evaluator 当前没有完整工具目录和运行约束；
2. `EvaluationDecision` 只包含粗粒度动作和自然语言反馈；
3. 工具选择必须经过 Registry、权限、预算和 capability contract 的代码校验；
4. 完整的新计划属于 Planner/Replanner 的职责；
5. 让 Evaluator 同时验收、选工具和规划，会扩大职责并降低可审计性。

因此，合理的协作方式是：

```text
Evaluator：决定是否需要 replan
    ↓
Replanner：根据可用工具目录、已完成结果和约束生成剩余计划
    ↓
Plan Validator：验证工具、依赖和结构是否合法
    ↓
Permission Gate / State Validation
    ↓
Executor：执行新计划
```

---

## 10. 最终判断

当前项目中：

> Evaluator 承担 `StepResult` 产生后的步骤级 Recovery Decision，包括 `retry`、`replan`、`partial` 和 `fail`；但它不处理 State Validation 的工具不可用，因为该路径不会进入 Evaluator。

对于以下设计：

```text
state_validation
    ↓
blocked_tool_unavailable
    + fallback_candidates
    + recoverable
```

结构化错误方向是合理的，但不能仅增加这些字段。要让 Evaluator 实际参与，还需要：

1. 增加从工具不可用阻塞到恢复决策节点的 Graph 路由；
2. 为 Evaluator 提供工具可用性、fallback、权限、预算、完整计划和已完成结果；
3. 将自然语言反馈升级为可验证的结构化 Recovery Decision；
4. 在执行恢复动作前进行确定性校验；
5. 对 Evaluator 不可用或结果不确定的情况采用 fail-closed；
6. 保持 Planner/Replanner 负责真正生成新计划。

最终推荐的职责分配是：

| 组件 | 推荐职责 |
|---|---|
| State Validator | 发现并结构化描述执行前能力或状态缺失 |
| Deterministic Recovery Policy | 选择可机器证明安全、可用的 fallback |
| Evaluator/Recovery Evaluator | 判断 `retry/replan/partial/fail` 的语义方向 |
| Planner/Replanner | 生成合法的剩余执行计划 |
| Plan Validator | 验证工具引用、依赖关系和计划结构 |
| Permission Gate | 检查替代方案的权限和副作用 |
| Executor | 执行已验证的恢复动作 |

一句话总结：

> **Evaluator 可以决定恢复方向，但不应独自选择未经验证的替代工具或生成完整计划；确定性 fallback 由代码选择，语义恢复由 Evaluator 判断，重新规划由 Planner 完成。**
