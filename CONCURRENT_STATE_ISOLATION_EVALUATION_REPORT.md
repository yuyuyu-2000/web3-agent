# Concurrent State Isolation Evaluation 报告

测试日期：2026-08-22（Asia/Shanghai）  
测试范围：仅新增测试与分析报告；未修改 Agent 业务逻辑。  
测试文件：`tests/test_concurrent_state_isolation_evaluation.py`

## 总表

| Case | 是否并发执行 | 是否状态泄漏 | 最终结果 | 原因 |
|---|---|---|---|---|
| Case 1：不同 user + 不同 thread | 是，`asyncio.gather` | 否 | 通过；A/B 均 `completed` | Checkpoint 分别以 `thread-a`、`thread-b` 隔离。A 仅包含 USDT，B 仅包含 TRX；user_id、messages、answer 和 trace_id 均未交叉。 |
| Case 2：不同 user + 相同 thread_id，Memory recall | 是，`asyncio.gather` | Recall 结果无跨用户泄漏；但共享 checkpoint 存在归属覆盖边界 | Memory 隔离检查通过；共享 checkpoint 一致性不通过 | Memory Store 按 user_id 搜索，Alice 只召回 `alice-usdt`，Bob 只召回 `bob-trx`，每个请求返回的 `recalled_memory_keys` 和 `active_recalled_memories` 正确。但同一 thread 的最终 checkpoint 只能保留一个并发写入者，本次为 Alice，Bob 的 checkpoint memory 状态被覆盖。 |
| Case 3：不同 thread，pending permission + readonly | 是，`asyncio.gather` | 否 | 通过；权限线程 `waiting_confirmation`，只读线程 `completed` | pending_permission、permission_action 和 permission decision 仅存在于 `permission-thread`；`readonly-thread` 中均为空，也没有 permission gate event。 |
| Case 4：同 user + 同 thread_id 两个任务 | 是，`asyncio.gather` | 是，发生覆盖 | 失败：两个调用都返回 `completed`，但最终 checkpoint 只剩 BETA | 最终 messages、plan、step_results、trace_id、node/decision Trace 全部只包含 BETA；ALPHA 更新丢失。表现为同 thread 并发写的 last-writer-wins，而不是安全合并或串行化。 |

## 汇总结论

### Cross-thread State Leakage

未发现。Case 1 和 Case 3 中，不同 `thread_id` 的 messages、user_id、约束、Permission 和 Trace 均保持隔离。

结果：**PASS**。

### Cross-user Memory Leakage

在 Memory Store 查询以及每个请求即时返回的 recall 状态中未发现跨用户内容泄漏：

- Alice：`recalled_memory_keys=["alice-usdt"]`；
- Bob：`recalled_memory_keys=["bob-trx"]`；
- 两边的 `active_recalled_memories` 所有者均与当前 user_id 一致。

但不同用户共用相同 `thread_id` 时，Checkpoint 本身没有 user 维度的 namespace。本次最终 checkpoint 归 Alice，Bob 的 checkpoint recall 状态丢失。这不是“把 Alice Memory 返回给 Bob”的直接泄漏，但说明 shared thread 不能同时作为两个用户的可靠状态容器；后续继续复用该 thread 存在跨用户历史状态边界风险。

结果：**Recall 隔离 PASS；shared-thread checkpoint ownership WARN/FAIL**。

### Permission Leakage

未发现。进入 `pending_permission` 的线程不会影响另一个只读线程。

结果：**PASS**。

### Same-thread Concurrent Consistency

不安全。Case 4 的两个请求都向调用方返回 `completed`，但最终 checkpoint 只有 BETA：

- ALPHA Human/AI messages 丢失；
- 最终 plan 只有 `step_beta`；
- 最终 step_results 只有 BETA；
- `trace_id` 为 `trace-beta`；
- checkpoint 中 node_events/decision_events 只包含 `trace-beta`；
- tool_events 为空，因为本 case 的两个计划都没有工具调用，不是 Trace 遗失。

结果：**FAIL，last-writer-wins / lost update**。

### Concurrent Request Success Rate

有两种口径：

- 请求执行完成率：8/8 = **100%**。其中 Permission 请求按预期停在 `waiting_confirmation`，其余请求均完成，没有并发异常抛出。
- 并发一致性 case 通过率：3/4 = **75%**。Case 4 因最终 checkpoint 丢失 ALPHA 状态而失败。

“请求均返回成功”不能代表状态一致；Case 4 正是调用成功但持久状态错误的情况。

## 测试实现

- 使用真实 `compile_agent_graph`；
- 使用项目 `memory_checkpointer()` 创建的真实 LangGraph `MemorySaver`；
- 使用真实 `InMemoryMemoryStore` 与 `MemoryService`；
- 使用真实 `recalled_memory_keys`、`active_recalled_memories` state 字段；
- 使用真实 Permission Gate 和 `pending_permission`；
- 并发全部通过 `asyncio.gather`；
- 仅替换外部 ChatOpenAI 为确定性测试模型，并提供一个未实际执行的测试 side-effect tool；
- 未修改 Agent、Checkpoint、Memory、Permission 或 Trace 业务代码。

## Case 1 Trace 与最终 Checkpoint

```json
{
  "concurrent": true,
  "state_leakage": false,
  "request_results": ["completed", "completed"],
  "thread_a": {
    "user_id": "user-a",
    "trace_id": "trace-user-a",
    "status": "completed",
    "messages": [
      "human: 设置约束：只看 USDT，并复述该约束",
      "ai: EXEC:USDT",
      "ai: FINAL:USDT"
    ]
  },
  "thread_b": {
    "user_id": "user-b",
    "trace_id": "trace-user-b",
    "status": "completed",
    "messages": [
      "human: 设置约束：只看 TRX，并复述该约束",
      "ai: EXEC:TRX",
      "ai: FINAL:TRX"
    ]
  }
}
```

判断：没有 Cross-thread State Leakage。A 的 checkpoint/answer 不含 TRX，B 的 checkpoint/answer 不含 USDT。

## Case 2 Trace 与最终 Checkpoint

每个并发请求的 Memory recall：

```json
{
  "alice": {
    "user_id": "user-a",
    "selected_keys": ["alice-usdt"],
    "recalled_memory_keys": ["alice-usdt"],
    "active_recalled_memories": [
      {
        "memory_key": "alice-usdt",
        "summary": "Alice 私有约束：只看 USDT",
        "metadata": {"user_id": "user-a"},
        "user_id": "user-a"
      }
    ]
  },
  "bob": {
    "user_id": "user-b",
    "selected_keys": ["bob-trx"],
    "recalled_memory_keys": ["bob-trx"],
    "active_recalled_memories": [
      {
        "memory_key": "bob-trx",
        "summary": "Bob 私有约束：只看 TRX",
        "metadata": {"user_id": "user-b"},
        "user_id": "user-b"
      }
    ]
  },
  "memory_service_isolated": true,
  "per_request_result_isolated": true
}
```

相同 `shared-memory-thread` 的最终 Checkpoint：

```json
{
  "user_id": "user-a",
  "trace_id": "trace-memory-user-a",
  "recalled_memory_keys": ["alice-usdt"],
  "active_recalled_memories": [
    {
      "memory_key": "alice-usdt",
      "summary": "Alice 私有约束：只看 USDT",
      "metadata": {"user_id": "user-a"},
      "user_id": "user-a"
    }
  ],
  "messages": [
    "system: Alice 私有约束：只看 USDT",
    "human: 继续之前的 USDT 分析",
    "ai: EXEC:USDT",
    "ai: FINAL:USDT"
  ]
}
```

判断：Memory 查询和每个请求即时 state 未串，但最终 checkpoint 采用 last-writer-wins，只保留 Alice。本次调度下 Bob 的 checkpoint state 被覆盖；另一次调度可能相反，因此 shared thread 的最终 owner 不应被认为是确定的。

## Case 3 Trace 与最终 Checkpoint

Permission thread：

```json
{
  "thread_id": "permission-thread",
  "status": "waiting_confirmation",
  "permission_action": "NEED_CONFIRM",
  "pending_permission": {
    "action": "NEED_CONFIRM",
    "step_id": "permission_step",
    "tool_name": "add_scheduled_task",
    "risk_level": "high",
    "reason": "该操作具有副作用或未被识别为只读操作",
    "operation_summary": "创建定时任务",
    "estimated_impact": "将创建并持久化一个定时任务，之后会自动执行"
  },
  "decision_events": [
    {"trace_id": "trace-permission", "thread_id": "permission-thread", "decision_type": "router", "action": "planned"},
    {"trace_id": "trace-permission", "thread_id": "permission-thread", "decision_type": "permission_gate", "action": "need_confirm", "step_id": "permission_step"}
  ]
}
```

Readonly thread：

```json
{
  "thread_id": "readonly-thread",
  "status": "completed",
  "permission_action": null,
  "pending_permission": null,
  "decision_events": [
    {"trace_id": "trace-readonly", "thread_id": "readonly-thread", "decision_type": "router", "action": "direct"}
  ]
}
```

判断：Permission Leakage = false。只读线程没有 permission gate decision，也没有继承另一个线程的 pending state。

## Case 4 Trace 与最终 Checkpoint

两个并发调用的即时状态：

```json
{
  "alpha_result_status": "completed",
  "beta_result_status": "completed",
  "both_messages_preserved_in_final_checkpoint": false,
  "both_step_results_preserved_in_final_checkpoint": false,
  "state_overwritten": true
}
```

最终 Checkpoint：

```json
{
  "thread_id": "same-user-same-thread",
  "user_id": "user-a",
  "trace_id": "trace-beta",
  "messages": [
    "human: 并发任务 BETA",
    "ai: EXEC:BETA",
    "ai: FINAL:BETA"
  ],
  "plan": {
    "goal": "完成 BETA",
    "steps": [
      {
        "id": "step_beta",
        "objective": "执行 BETA",
        "success_criteria": "返回 BETA",
        "suggested_tools": [],
        "depends_on": [],
        "requires_confirmation": false,
        "critical": true,
        "fallback_tools": [],
        "estimated_tool_calls": 4,
        "budget_reason": ""
      }
    ]
  },
  "step_results": [
    {
      "step_id": "step_beta",
      "status": "success",
      "summary": "EXEC:BETA",
      "evidence": [],
      "structured_facts": [],
      "dependency_outputs": {},
      "result_references": [],
      "provenance": [],
      "tool_calls": [],
      "error": null
    }
  ],
  "pending_permission": null,
  "trace_ids_present": ["trace-beta"],
  "tool_events": []
}
```

完整 node/decision Trace（最终 checkpoint 中实际可见部分）：

```json
{
  "node_events": [
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "router", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "planner", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "select_step", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "permission_gate", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "executor", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "evaluator", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "select_step", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "compose_answer", "status": "success"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "node_name": "reviewer", "status": "success"}
  ],
  "decision_events": [
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "decision_type": "router", "action": "planned"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "decision_type": "permission_gate", "action": "allow", "step_id": "step_beta"},
    {"trace_id": "trace-beta", "thread_id": "same-user-same-thread", "decision_type": "evaluator", "action": "pass", "step_id": "step_beta"}
  ],
  "tool_events": []
}
```

失败归因：**同一 checkpoint namespace 的并发写覆盖 / lost update**。没有发现 messages reducer 能把两个独立并发 graph run 安全合并；plan、step_results 和 Trace 等普通 state 字段也采用最后提交版本。当前边界应视为：相同 `thread_id` 的请求需要调用方串行化，不能假设 Agent Graph 自带同-thread 并发一致性。

## 测试结果

```text
.venv/bin/pytest -q tests/test_concurrent_state_isolation_evaluation.py -vv
1 passed in 1.38s

.venv/bin/pytest -q tests/test_concurrent_state_isolation_evaluation.py tests/test_memory_store.py tests/test_automatic_memory_recall.py tests/test_permission_gate.py
22 passed in 1.79s
```

评测 pytest 通过表示四个 case 均成功执行、基础隔离断言成立并产出结果；它没有把 Case 4 的不一致伪装成通过。Case 4 的业务判定明确为失败，详细最终 checkpoint 和 Trace 已如上保留。
