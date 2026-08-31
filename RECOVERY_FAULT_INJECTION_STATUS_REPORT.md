# Recovery / Fault Injection 现状测试报告

测试时间：2026-08-22（Asia/Shanghai）  
测试范围：只测试、记录和分析；未修改生产代码或测试框架代码。  
本轮数据集变更：在 `eval/test_cases.jsonl` 追加 4 个 Recovery case：`recovery_timeout_retry_003`、`recovery_429_backoff_004`、`recovery_invalid_arguments_005`、`recovery_permission_denied_006`。

## 结论表

| Case | 是否真正执行 | 结果 | Retry 次数 | 最终状态 | 原因 |
| ---- | ------ | -- | -------- | ---- | -- |
| `recovery_timeout_retry_003` | 组件层真实执行；HTTP E2E 未执行 | 组件层通过；HTTP E2E `skipped / unsupported` | 1 | 组件层 `success`；HTTP E2E `skipped` | `FaultInjectingTool` 在 attempt 1 注入 timeout，`RecoveringToolNode` 将其分类为可重试，记录 0.25 秒 backoff，attempt 2 成功；HTTP Adapter 明确不支持 `fault_injection`。 |
| `recovery_429_backoff_004` | 组件层真实执行；HTTP E2E 未执行 | 组件层通过；HTTP E2E `skipped / unsupported` | 1 | 组件层 `success`；HTTP E2E `skipped` | attempt 1 的 429 被分类为 `rate_limit/retryable=true`，记录 0.25 秒 backoff，attempt 2 成功；HTTP Adapter 无法注入故障。 |
| `recovery_invalid_arguments_005` | 组件层真实执行；HTTP E2E 未执行 | 组件层仅证明“不机械 retry”；参数修正闭环未测到；HTTP E2E `skipped / unsupported` | 0 | 组件层 `failed`；HTTP E2E `skipped` | `argument_error` 被正确分类为不可重试，原参数只调用 1 次。组件层到此返回结构化错误；参数修正属于上层 planned executor/evaluator 流程，本轮无法通过 HTTP 注入进入该闭环，因此不能宣称“修正后成功”。 |
| `recovery_permission_denied_006` | 组件层真实执行；HTTP E2E 未执行 | 组件层通过；HTTP E2E `skipped / unsupported` | 0 | 组件层 `permission_denied`；HTTP E2E `skipped` | `permission_error=true/retryable=false`，仅 1 次 attempt，无 backoff、无 fallback、底层工具未被调用；graph 的既有路由会转入 `permission_failure`，不会回到工具重试路径。 |

> “组件层真实执行”指使用项目现有 `FaultInjectingTool` 包装测试工具，并由生产 `RecoveringToolNode` 执行真实分类、重试和结果生成逻辑；底层业务工具为只读 diagnostic stub，没有访问真实 TRON/PostgreSQL。HTTP E2E 是否执行单独列出，避免把组件测试冒充线上端到端测试。

## 1. 当前为什么 Recovery Rate = N/A

`Recovery Rate` 来自 deterministic check 名称前缀为 `recovery` 的结果平均值。只有带 `fault_injection` 且故障类型为 `timeout` 或 `429` 的、**实际进入 evaluator 的 case** 才会生成该 check。

当前 `HttpAgentAdapter.unsupported_capabilities = {"fault_injection"}`。`EvaluationRunner` 在执行前发现 Recovery case 的 `required_capabilities` 包含 `fault_injection`，会把它们全部放进 `skipped_cases`，不会生成 `CaseResult`，因此没有任何 `recovery` check 可供聚合，`recovery_success_rate` 为 `null`，报告显示 `N/A`。

本轮 HTTP capability check 的直接结果：dataset Recovery cases 为 6，evaluated 为 0，skipped 为 6；6 个 case 的原因全部是 `adapter does not support: fault_injection`。

此前报告 `eval_results/run_20260822T090641Z.md` 也只执行了 direct/database/multi_tool/chart 各 1 个 case，没有 Recovery case，因此其 `Recovery Rate = N/A` 同样是“无可计算样本”，不是 0%，也不是 Recovery 全部失败。

## 2. 已经由现有测试证明的 Recovery 能力

- 瞬时 timeout：生产 `RecoveringToolNode` 会进行有界重试，attempt 序列和 `recovered=true` 会写入 `tool_events`，最终可以成功。本轮真实注入也得到相同结果。
- 429/rate limit：分类为 `rate_limit` 且 `retryable=true`，按指数 backoff 公式等待后重试。本轮固定随机因子后记录到首次 delay 为 0.25 秒，并在第 2 次 attempt 成功。
- Invalid Arguments 的底层策略：分类为 `argument_error/retryable=false`，不会用原参数在工具层机械重试；返回结构化错误供上层处理。
- Permission Denied：分类为 `permission_error=true/retryable=false`，工具层不 retry；graph 已有 `tools_route -> permission_failure` 路由，权限错误不会进入常规 retry/fallback。
- 全局工具调用预算会限制 retry；返回值形式的 transient error 同样会 retry；非 transient error 不 retry。
- planned evaluator 的语义 retry 通路存在，现有 `test_planned_step_retries_after_evaluator_feedback` 证明 evaluator 反馈可令步骤重试并最终完成；但该测试没有注入 invalid arguments，也没有断言参数确实发生变化，所以不能把它当作 invalid-argument 参数修正闭环的完整证明。

测试命令与结果：

```text
.venv/bin/pytest -q tests/test_tool_recovery.py tests/test_evaluation_framework.py -k 'not dataset_has_thirty_valid_unique_cases' tests/test_agent_graph_routing.py::test_planned_step_retries_after_evaluator_feedback
.................
17 passed, 1 deselected in 1.72s
```

追加 4 个数据集 case 后，原有测试 `test_dataset_has_thirty_valid_unique_cases` 仍硬编码 `len(cases) == 30`，所以完整选定测试结果为 `17 passed, 1 failed`，实际读取到 34 个合法 case。该失败是数据集扩充导致的陈旧数量断言，不是 Recovery 运行失败；按本轮约束未修改测试框架代码。

## 3. 仅因测试环境无法注入而没有测到的场景

- timeout 的公开 HTTP Agent E2E 注入、重试与最终成功。
- 429 的公开 HTTP Agent E2E 注入、实际墙钟 backoff 与最终成功。
- invalid arguments 从 HTTP 请求进入 planned executor 后，由结构化错误触发 evaluator/LLM 修正参数，再次调用并成功的完整闭环。
- permission denied 经公开 HTTP Agent E2E 进入 `permission_failure`，并验证没有选择等价工具绕过权限的完整闭环。

上述场景不是因为服务不可达而跳过；runner 在发出 HTTP 请求前即根据 Adapter capability 判定 `fault_injection` unsupported。

## 4. 真正执行后失败的场景

只有 `recovery_invalid_arguments_005` 的**组件层最终状态**为 `failed`：故障真实注入，且正确地没有原参数机械 retry；但该执行层不负责语义参数修正，因此没有最终成功。这证明了“不机械 retry”，没有证明“进入参数修正并成功”。由于 HTTP E2E 被 Adapter 跳过，本轮没有证据把它定性为生产参数修正能力失败。

没有发现 timeout、429 或 permission-denied 策略在其实际执行层失败。也没有任何 Recovery HTTP E2E case 真正执行后失败——它们全部是执行前 `skipped / unsupported`。

## 5. 完整 Trace

以下 Trace 保留本轮要求的 `tool_events`、`decision_events`、`error_events`、最终状态及 retry/backoff 信息。`decision_events` 为空是实际结果：`RecoveringToolNode` 本身只产出 `tool_events`；上层 graph 才写 evaluator/permission 路由 decision。`error_events` 按生产 graph 的 `tool_node` 映射规则由本次真实 `tool_events` 生成。

### recovery_timeout_retry_003

```json
{
  "case_id": "recovery_timeout_retry_003",
  "execution_layer": "FaultInjectingTool + RecoveringToolNode",
  "injection": {"tool": "get_tron_transaction", "error": "timeout", "occurrence": 1, "times": 1},
  "tool_events": [
    {"trace_id": "trace-recovery_timeout_retry_003", "thread_id": "thread-recovery_timeout_retry_003", "tool_call_id": "call-recovery_timeout_retry_003", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 1, "duration_ms": 0.006, "status": "error", "error_type": "timeout", "retryable": true, "recovered": false, "fallback_tool": null},
    {"trace_id": "trace-recovery_timeout_retry_003", "thread_id": "thread-recovery_timeout_retry_003", "tool_call_id": "call-recovery_timeout_retry_003", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 2, "duration_ms": 145.246, "status": "success", "error_type": null, "retryable": false, "recovered": true, "fallback_tool": null}
  ],
  "decision_events": [],
  "error_events": [
    {"trace_id": "trace-recovery_timeout_retry_003", "thread_id": "thread-recovery_timeout_retry_003", "source": "tool", "tool_name": "get_tron_transaction", "step_id": "step_1", "error_type": "timeout", "retryable": true}
  ],
  "retry_info": {"attempts": 2, "retry_count": 1, "configured_max_retries": 2, "backoff_delays_sec": [0.25], "fault_proxy_total_calls": 2, "underlying_tool_calls": 1},
  "structured_errors": [],
  "tool_result": {"status": "success", "txid": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "source": "diagnostic_stub"},
  "final_status": "success",
  "http_e2e": {"status": "skipped", "reason": "adapter does not support: fault_injection"}
}
```

判断：组件层通过。HTTP E2E 跳过是 Adapter 能力限制，不是 timeout recovery 失败。

### recovery_429_backoff_004

```json
{
  "case_id": "recovery_429_backoff_004",
  "execution_layer": "FaultInjectingTool + RecoveringToolNode",
  "injection": {"tool": "get_tron_transaction", "error": "429", "occurrence": 1, "times": 1},
  "tool_events": [
    {"trace_id": "trace-recovery_429_backoff_004", "thread_id": "thread-recovery_429_backoff_004", "tool_call_id": "call-recovery_429_backoff_004", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 1, "duration_ms": 0.006, "status": "error", "error_type": "rate_limit", "retryable": true, "recovered": false, "fallback_tool": null},
    {"trace_id": "trace-recovery_429_backoff_004", "thread_id": "thread-recovery_429_backoff_004", "tool_call_id": "call-recovery_429_backoff_004", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 2, "duration_ms": 0.184, "status": "success", "error_type": null, "retryable": false, "recovered": true, "fallback_tool": null}
  ],
  "decision_events": [],
  "error_events": [
    {"trace_id": "trace-recovery_429_backoff_004", "thread_id": "thread-recovery_429_backoff_004", "source": "tool", "tool_name": "get_tron_transaction", "step_id": "step_1", "error_type": "rate_limit", "retryable": true}
  ],
  "retry_info": {"attempts": 2, "retry_count": 1, "configured_max_retries": 2, "backoff_delays_sec": [0.25], "fault_proxy_total_calls": 2, "underlying_tool_calls": 1},
  "structured_errors": [],
  "tool_result": {"status": "success", "txid": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "source": "diagnostic_stub"},
  "final_status": "success",
  "http_e2e": {"status": "skipped", "reason": "adapter does not support: fault_injection"}
}
```

判断：组件层通过。backoff 由注入的 sleeper 捕获，未真实 sleep；这验证了调度的 delay 值和 retry 行为，不是实际外部服务的限流恢复耗时。HTTP E2E 因 Adapter 不支持注入而跳过。

### recovery_invalid_arguments_005

```json
{
  "case_id": "recovery_invalid_arguments_005",
  "execution_layer": "FaultInjectingTool + RecoveringToolNode",
  "injection": {"tool": "get_tron_transaction", "error": "argument_error", "occurrence": 1, "times": 1},
  "tool_events": [
    {"trace_id": "trace-recovery_invalid_arguments_005", "thread_id": "thread-recovery_invalid_arguments_005", "tool_call_id": "call-recovery_invalid_arguments_005", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 1, "duration_ms": 0.006, "status": "error", "error_type": "argument_error", "retryable": false, "recovered": false, "fallback_tool": null}
  ],
  "decision_events": [],
  "error_events": [
    {"trace_id": "trace-recovery_invalid_arguments_005", "thread_id": "thread-recovery_invalid_arguments_005", "source": "tool", "tool_name": "get_tron_transaction", "step_id": "step_1", "error_type": "argument_error", "retryable": false}
  ],
  "retry_info": {"attempts": 1, "retry_count": 0, "configured_max_retries": 2, "backoff_delays_sec": [], "fault_proxy_total_calls": 1, "underlying_tool_calls": 0},
  "structured_errors": [
    {"status": "error", "tool": "get_tron_transaction", "error_type": "argument_error", "retryable": false, "permission_error": false, "message": "invalid argument (eval injected)", "attempts": 1}
  ],
  "tool_result": {"status": "error", "error_type": "argument_error", "retryable": false, "permission_error": false, "attempts": 1},
  "final_status": "failed",
  "http_e2e": {"status": "skipped", "reason": "adapter does not support: fault_injection"}
}
```

判断：真实验证通过的是“避免原参数机械 retry”。`fault_proxy_total_calls=1`、`retry_count=0`、无 backoff；底层 stub 甚至没有被调用。未验证到“参数修正后成功”，原因是语义修正发生在上层 planned executor/evaluator，而 HTTP Adapter 无法把 fault injection 送入该流程。此处组件最终失败不能外推为生产 E2E 修正失败。

### recovery_permission_denied_006

```json
{
  "case_id": "recovery_permission_denied_006",
  "execution_layer": "FaultInjectingTool + RecoveringToolNode",
  "injection": {"tool": "get_tron_transaction", "error": "permission_denied", "occurrence": 1, "times": 1},
  "tool_events": [
    {"trace_id": "trace-recovery_permission_denied_006", "thread_id": "thread-recovery_permission_denied_006", "tool_call_id": "call-recovery_permission_denied_006", "tool_name": "get_tron_transaction", "step_id": "step_1", "attempt": 1, "duration_ms": 0.004, "status": "error", "error_type": "permission_error", "retryable": false, "recovered": false, "fallback_tool": null}
  ],
  "decision_events": [],
  "error_events": [
    {"trace_id": "trace-recovery_permission_denied_006", "thread_id": "thread-recovery_permission_denied_006", "source": "tool", "tool_name": "get_tron_transaction", "step_id": "step_1", "error_type": "permission_error", "retryable": false}
  ],
  "retry_info": {"attempts": 1, "retry_count": 0, "configured_max_retries": 2, "backoff_delays_sec": [], "fault_proxy_total_calls": 1, "underlying_tool_calls": 0},
  "structured_errors": [
    {"status": "error", "tool": "get_tron_transaction", "error_type": "permission_error", "retryable": false, "permission_error": true, "message": "permission denied (eval injected)", "attempts": 1}
  ],
  "tool_result": {"status": "error", "error_type": "permission_error", "retryable": false, "permission_error": true, "attempts": 1},
  "final_status": "permission_denied",
  "http_e2e": {"status": "skipped", "reason": "adapter does not support: fault_injection"}
}
```

判断：组件层通过。没有 retry、没有 backoff、没有 fallback，且底层工具调用数为 0；因此未发生权限绕过。HTTP E2E 跳过，故不能用本轮结果声称已端到端验证所有可能的替代工具选择。

## HTTP Evaluation skip Trace

这是 EvaluationRunner 对全部 Recovery case 的完整 capability 判定结果；原有两个 case 也一并列出：

```json
{
  "run_id": "recovery_http_adapter_capability_check",
  "dataset_cases": 6,
  "evaluated_cases": 0,
  "skipped_cases": [
    {"case_id": "recovery_001", "reason": "adapter does not support: fault_injection"},
    {"case_id": "recovery_002", "reason": "adapter does not support: fault_injection"},
    {"case_id": "recovery_timeout_retry_003", "reason": "adapter does not support: fault_injection"},
    {"case_id": "recovery_429_backoff_004", "reason": "adapter does not support: fault_injection"},
    {"case_id": "recovery_invalid_arguments_005", "reason": "adapter does not support: fault_injection"},
    {"case_id": "recovery_permission_denied_006", "reason": "adapter does not support: fault_injection"}
  ],
  "metrics": {
    "overall": {
      "cases": 0,
      "task_success_rate": 0.0,
      "tool_selection_accuracy": null,
      "tool_argument_accuracy": null,
      "recovery_success_rate": null,
      "permission_gate_accuracy": null,
      "partial_rate": 0.0,
      "degraded_rate": 0.0,
      "failed_rate": 0.0
    }
  },
  "results": []
}
```

由于 case 在 Adapter capability gate 即被跳过，HTTP 层不存在可保留的 `tool_events`、`decision_events` 或 `error_events`；空 `results` 本身就是完整 Trace 状态，而不是 Trace 丢失。
