# Deterministic StepResult Machine Step Validator Shadow Mode 实现与评测报告

## 1. 最终结论

本轮已完成 Machine Step Validator 的实现、shadow mode 接入、trace/metrics 统计、指定负向测试及完整 Evaluation。

当前结论：**实现可以继续以 shadow mode 运行，但暂不具备真正开启 evaluator fast path、跳过 LLM Step Evaluator 的条件。**

原因：

- 最终完整 run 中共有 4 个 deterministic StepResult 进入 Machine Validator；
- machine decision：pass 2、fail 0、unknown 2；
- 2 个 machine pass 后的 LLM decision 均为 pass，machine-pass/LLM-non-pass 冲突为 0；
- 但 machine-pass 样本只有 2 个，每种 result_kind 只有 1 个，统计证据严重不足；
- recovery Evaluation 仍因 HTTP adapter 不支持 fault injection 而跳过；
- 自然语言 success criteria 中仍有一部分无法机器证明，并被正确判为 unknown；
- 完整 run 存在模型规划和输出波动，不能用一次 0 冲突证明 false-pass 风险为 0。

当前路由保持不变：

```text
deterministic StepResult
  -> Machine Step Validator（shadow only）
  -> 正常调用现有 LLM Step Evaluator
  -> 使用 LLM decision 执行 pass/retry/replan/partial/fail
```

Machine decision 不参与路由、不会 complete step，也不会覆盖 LLM decision。

## 2. 实现内容

### 2.1 Machine Validator

新增：

- `src/chaincloud_agent_service/agent/evaluation/machine_validator.py`

输出结构：

```json
{
  "decision": "pass | fail | unknown",
  "reason": "...",
  "checked_predicates": [
    {"name": "...", "outcome": "pass | fail | unknown", "detail": "..."}
  ],
  "validator_version": "1.0.0-shadow",
  "result_kind": "tabular_query | canonical_transaction | unknown"
}
```

决策语义：

- `pass`：所有安全 predicate 通过，并且 success criteria 的每一个 clause 都属于当前版本可机器证明的模板且验证通过；
- `fail`：任一明确可验证 predicate 为 false；
- `unknown`：没有明确失败，但一个或多个 success-criteria clause 无法由当前版本机器证明。

Validator 是 fail-closed 的：只识别部分 criteria 时不会 pass；未知自然语言不得靠关键词猜 pass。

### 2.2 Shadow graph 节点

在 deterministic StepResult hit 后增加独立节点：

```text
tools
  -> deterministic_step_result
  -> machine_step_validator
  -> evaluator
```

fast-path reject 仍然无损回到 Executor summarization：

```text
deterministic_step_result reject
  -> executor
  -> complete_step
  -> evaluator
```

因此本轮没有实现、也没有暗中启用 LLM Evaluator 跳过。

### 2.3 Trace 与指标

每次 shadow decision 写入 `decision_events`：

- `decision_type=machine_step_validator`
- `action=pass|fail|unknown`
- `reason`
- `checked_predicates`
- `validator_version`
- `result_kind`
- `step_id`
- `shadow_mode=true`

独立 `node_events.node_name=machine_step_validator` 记录 latency。

`request_summary` 新增：

- `machine_validator_passes`
- `machine_validator_failures`
- `machine_validator_unknowns`
- `machine_pass_llm_non_pass_conflicts`

Evaluation aggregate 新增：

- machine pass/fail/unknown 总量；
- machine-pass/LLM-non-pass 冲突总量；
- 按 `result_kind` 的 machine pass、冲突数与冲突率。

## 3. 当前支持的机器验证

### 3.1 Result 安全条件

- `StepResult.status == success`；
- `StepResult.error` 为空；
- 没有 unresolved tool errors；
- 没有 retry/recovered/fallback tool event；
- 单一 tool result；
- 实际 tool name 在 `PlanStep.suggested_tools` 中；
- reference/provenance 存在；
- result_id、tool name、tool args、created_at、evidence source、raw location、content hash 完整；
- result contract 存在；
- terminal；
- 未 truncated；
- ambiguity 为空；
- structured facts complete；
- StepResult reference 中的 tool args 与持久化 ToolResultRecord 一致。

### 3.2 第一批 success criteria

- 显式 `required fields:` / `必填字段:` / `必须包含字段:` / `需要字段:`；
- required field 存在且非 null、非空字符串、非空 list/dict；
- `row_count = 1`、单行/一条/单条；
- scalar/标量条件；
- txid/tx_hash/交易哈希的 64 位十六进制格式；
- transaction status SUCCESS；
- receipt status SUCCESS；
- dependency binding：前序 `tx_hash`/`*_tx_hash` 与当前工具参数 `txid` 规范化后一致；
- tool name 与计划一致；
- tool args 与持久化 record 一致。

TRON identifier 比较会统一大小写并移除可选 `0x` 前缀。

### 3.3 Unknown 边界

criteria 会按中文/英文分号、句号和换行拆分。任一 clause 不属于受支持模板，最终 decision 即为 unknown，即使其他 predicate 全部通过。

例如：

```text
交易哈希格式正确；形成可信且有洞察力的业务结论
```

第一项可机器验证，第二项不可机器验证，因此整体为 unknown，不会 pass。

## 4. 负向测试覆盖

新增 `tests/test_machine_step_validator.py`，覆盖：

| 场景 | 预期 | 已验证 |
|---|---|---|
| txid dependency 不一致 | fail | 是 |
| `deposit_tx_hash` 与 txid 一致 | pass | 是 |
| receipt FAILED，但 criteria 要求 SUCCESS | fail | 是 |
| required field 缺失 | fail | 是 |
| empty result / row_count=0，但要求单行 | fail | 是 |
| truncated result | fail | 是 |
| ambiguity 非空 | fail | 是 |
| tool args 与持久化 record 不一致 | fail | 是 |
| StepResult error/failed | fail | 是 |
| unresolved tool error | fail | 是 |
| retry/recovered event | fail | 是 |
| tool name 不在计划中 | fail | 是 |
| 完全不可证明的自然语言 criteria | unknown | 是 |
| criteria 只有部分 clause 可证明 | unknown | 是 |
| 支持的 required fields/hash/status criteria | pass | 是 |

测试结果：

- Machine Validator 专项：13 passed；
- 全量测试：**191 passed**；
- Ruff：通过；
- `git diff --check`：通过。

## 5. Shadow 调试中发现并修复的问题

第一次完整 shadow run：`run_20260821T122227Z`。

- machine decisions：3；
- pass 0、fail 2、unknown 1；
- 两个 fail 都发生在 `multi_002`；
- LLM 对应均判 pass。

根因是数据库字段名为 `deposit_tx_hash`，validator 初版只识别 `txid` 和 `tx_hash`：

- step_1 被误判 identifier 缺失；
- step_2 被误判 dependency binding 找不到前序 tx hash。

修复后，受控 identifier 别名支持 `*_tx_hash`，并增加回归测试。

第二次完整 shadow run：`run_20260821T124421Z`。

- machine decisions：5；
- pass 5、fail 0、unknown 0；
- machine-pass/LLM-non-pass 冲突 0。

进一步审计发现：该版本只要识别到 criteria 中部分关键词就可能 pass，没有证明每个自然语言 clause 都已覆盖。这不符合“无法机器证明必须 unknown”的要求。

因此再次收紧为逐 clause fail-closed，并补充 partially-supported criteria 测试。最终结果以第三次完整 run 为准。

## 6. 最终完整 Evaluation

最终有效 run：

- `eval_results/run_20260821T130800Z.json`
- `eval_results/run_20260821T130800Z.md`

数据集：30 cases，其中 28 evaluated，2 skipped。

跳过：

- `recovery_001`：adapter does not support `fault_injection`；
- `recovery_002`：adapter does not support `fault_injection`。

### 6.1 总体质量

| 指标 | 最终 run |
|---|---:|
| Task success | 78.57% |
| Tool selection accuracy | 92.86% |
| Tool argument accuracy | 100% |
| Permission accuracy | 100% |
| Partial rate | 0% |
| Degraded rate | 3.57% |
| Failed rate | 17.86% |
| Recovery success | N/A |

总体指标受模型路由、规划和回答波动影响。Machine Validator 是 shadow-only，不参与 action 或 routing，因此不能把 task success 波动归因于 machine decision。

### 6.2 Machine decision

| 指标 | 数值 |
|---|---:|
| deterministic hits / machine decisions | 4 |
| machine pass | 2 |
| machine fail | 0 |
| machine unknown | 2 |
| machine-pass/LLM-non-pass 冲突 | 0 |
| machine-pass 与 LLM-pass 一致率 | 100%（2/2） |

逐 step：

| Case | Step | result_kind | Machine | LLM Evaluator | 结果 |
|---|---|---|---|---|---|
| `multi_001` | step_1 | tabular_query | pass | pass | 一致 |
| `multi_001` | step_2 | canonical_transaction | pass | pass | 一致 |
| `multi_002` | step_1 | tabular_query | unknown | pass | 保守 fallback 到 LLM |
| `multi_002` | step_2 | canonical_transaction | unknown | pass | 保守 fallback 到 LLM |

两个 unknown 的 reason 均为：存在当前 validator version 无法机器证明的 success-criteria clause。这是预期安全行为。

### 6.3 按 result_kind 冲突率

| result_kind | machine pass | machine-pass/LLM-non-pass | 冲突率 |
|---|---:|---:|---:|
| tabular_query | 1 | 0 | 0% |
| canonical_transaction | 1 | 0 | 0% |
| 合计 | 2 | 0 | 0% |

这里的 0% 不能解释为已经证明安全：每种类型只有一个 machine-pass 样本。

### 6.4 Machine Validator latency

| 指标 | 数值 |
|---|---:|
| 调用次数 | 4 |
| 平均 latency | 0.4115 ms |
| P95 latency | 0.5923 ms |

开销相对于 LLM Evaluator 极小。最终 run 中 LLM Evaluator 平均 4,100.037 ms、P95 6,625.280 ms；但 shadow mode 仍正常支付该成本。

### 6.5 Shadow mode 路由验证

最终 run 中每个 machine decision 后均存在正常 LLM evaluator decision：

- machine pass 没有直接 complete step；
- machine unknown 没有直接 fail/retry；
- 最终 step action 完全由现有 LLM Evaluator 决定；
- LLM calls 没有因 Machine Validator 被减少。

## 7. 是否具备开启 evaluator fast path 的条件

结论：**暂不具备。**

已经满足：

- shadow-only 接入正确；
- machine decision 可审计；
- validator version 可追踪；
- predicate 明细完整；
- 不可证明 criteria 能返回 unknown；
- 指定负向单元测试均通过；
- 已观察到 machine pass 与 LLM pass 一致；
- runtime latency 小于 1 ms；
- tool/provenance/contract/dependency/status 基础验证已具备。

尚未满足：

- machine-pass 样本仅 2 个；
- 每个 result_kind 仅 1 个 pass 样本；
- recovery_001/002 未实际运行；
- 没有 fault-injection 下的 retry/recovery shadow 实证；
- 没有足够的 empty/null/FAILED receipt/错误参数真实流量样本；
- PlanStep.success_criteria 仍是自由自然语言字符串，大量条件只能 unknown；
- 完整 Evaluation 的 planner 输出存在 run-to-run 波动，deterministic hits 从历史 7 次变化为本轮 4 次；
- 尚未建立足够规模的 shadow 冲突置信区间和人工审计样本。

## 8. 开启前建议门槛

继续保持 shadow mode，至少达到：

1. 累积每个可直通 result_kind 至少 100 个 machine-pass 样本；高风险类型建议 500+。
2. machine-pass/LLM-non-pass 冲突为 0；任何冲突必须人工审计并归零后重新计数。
3. fault-injection recovery Evaluation 实际运行并通过。
4. 对 empty/null、truncated、ambiguity、FAILED status、错误 dependency、错误 args、retry/recovered/fallback 建立集成级 trace 测试，而不只单元测试。
5. 将 Planner success criteria 逐步结构化，例如增加可选 `machine_criteria` schema；自由自然语言继续 unknown。
6. 按 validator version 和 result_kind 做灰度、kill switch 与持续采样 LLM 审计。
7. 真正开启时只允许：

```text
deterministic StepResult
AND machine decision == pass
AND validator version/result_kind 在启用白名单
AND no retry/recovery/fallback/error/ambiguity/truncation
  -> complete step

otherwise
  -> current LLM Step Evaluator
```

## 9. 文件清单

新增：

- `src/chaincloud_agent_service/agent/evaluation/machine_validator.py`
- `tests/test_machine_step_validator.py`
- `machine_step_validator_shadow_mode_report.md`

修改：

- `src/chaincloud_agent_service/agent/evaluation/__init__.py`
- `src/chaincloud_agent_service/agent/graph.py`
- `src/chaincloud_agent_service/observability/trace.py`
- `src/chaincloud_agent_service/evaluation/metrics.py`

评测产物：

- 最终：`eval_results/run_20260821T130800Z.json`、`eval_results/run_20260821T130800Z.md`
- 调试 run：`run_20260821T122227Z`、`run_20260821T124421Z`

## 10. 最终状态

Machine Step Validator 已完整实现并处于 shadow mode。现有 LLM Step Evaluator 没有被跳过，当前运行语义未改变。实现已能在可证明时 pass、明确反例时 fail、自然语言或部分不可证明时 unknown；trace 和 Evaluation 已能统计决策、冲突、result_kind 与 latency。

由于最终 run 只有 2 个 machine-pass 样本且 recovery 没有实测，**本轮不建议开启 evaluator fast path**。下一阶段应继续积累 shadow 数据并结构化 PlanStep criteria，而不是直接删除或绕过 LLM Evaluator。
