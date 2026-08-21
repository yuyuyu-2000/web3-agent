已完成第一版 Planned Executor deterministic StepResult fast path。

实现内容：

- Tool Result 增加通用 capability contract：
  - `terminal`
  - `structured_facts_complete`
  - `truncated`
  - `ambiguity`
  - `provenance_complete`
  - `result_kind`
  - `deterministic_step_result_eligible`
- Planned 工具返回后先进入确定性 predicate：
  - 命中：代码直接构造 `StepResult`，进入现有 Step Evaluator。
  - 拒绝：无损回到原 Executor summarization。
- `StepResult` 新增：
  - `dependency_outputs`
  - `provenance`
- 增加 fast-path trace：
  - hit
  - reject
  - fallback
  - 拒绝原因
- Direct、Planner、Permission Gate、Composer、Reviewer 均未修改。

主要文件：

- [step_result_fast_path.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/step_result_fast_path.py)
- [tool_results.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/tool_results.py)
- [graph.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/graph.py)
- [models.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/planning/models.py)
- [trace.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/observability/trace.py)
- [test_step_result_fast_path.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/tests/test_step_result_fast_path.py)

## Evaluation 结果

有效新 run：

[run_20260821T084656Z.json](/Users/yuyu/Documents/code/Chaincloud-AI-main/eval_results/run_20260821T084656Z.json)

| 指标 | 修改前 | 修改后 | 变化 |
|---|---:|---:|---:|
| Task success | 82.14% | 82.14% | 无回归 |
| Multi-tool success | 100% | 100% | 无回归 |
| Tool selection | 96.43% | 100% | +3.57pp |
| Tool arguments | 100% | 100% | 无回归 |
| Permission accuracy | 100% | 100% | 无回归 |
| 平均 LLM calls | 5.571 | 4.679 | -16.0% |
| 总 LLM calls | 156 | 131 | -25 |
| 平均 input tokens | 29,293 | 22,380 | -23.6% |
| 总 input tokens | 615,161 | 469,989 | -145,172 |
| 平均 output tokens | 1,635 | 1,013 | -38.1% |
| 总 output tokens | 34,341 | 21,270 | -13,071 |
| P50 latency | 29.79s | 23.32s | -21.7% |
| P95 latency | 133.40s | 91.87s | -31.1% |

总量差异还包含模型路由、Reviewer 修订和输出长度的运行间波动。能够直接归因于 fast path 的部分是：

- 跳过 7 次 Executor summarization LLM call。
- 对应修改前 61,395 个 summarization input tokens。
- 对应修改前约 79.91 秒 Executor 节点耗时。
- deterministic 节点平均耗时仅 0.149 ms。

## Hit / reject / fallback

| 结果 | 次数 |
|---|---:|
| Fast-path hit | 7 |
| Reject | 2 |
| Fallback 到原 Executor | 2 |
| Fast-path 执行异常 | 0 |

命中：

- `db_003`：单行 PostgreSQL 聚合结果。
- `multi_001`：PostgreSQL 单行 + TRON canonical。
- `multi_002`：PostgreSQL 单行 + TRON canonical。
- `multi_003`：PostgreSQL 单行 + TRON canonical。

拒绝：

- `db_001`：`incomplete_structured_facts`
- `db_002`：`incomplete_structured_facts`

两者都是多行结果，符合“暂不覆盖 Top-N/多行 sample”限制，并成功 fallback 到原 summarization。

## Planned / multi-tool 对比

| Case | 修改前 LLM calls | 修改后 | 修改前 input | 修改后 | 修改前 latency | 修改后 |
|---|---:|---:|---:|---:|---:|---:|
| `db_003` | 7 | 6 | 18,371 | 9,111 | 28.82s | 18.22s |
| `multi_001` | 11 | 7 | 43,271 | 20,744 | 154.12s | 69.39s |
| `multi_002` | 9 | 7 | 42,013 | 19,548 | 80.90s | 62.42s |
| `multi_003` | 9 | 7 | 46,651 | 20,830 | 97.99s | 168.27s |

`multi_003` 虽然少了两次 Executor LLM call，但本轮 Planner 单独耗时 85.49 秒、Composer 47.31 秒，因此总 latency 反增。这是后置/前置模型长尾，不是 fast path 节点开销或 dependency failure。

三个 multi-tool case 中：

- 所有 fast-path StepResult 均被原 Step Evaluator 判定为 `pass`。
- PostgreSQL 的 `tx_hash` 成功传播到下一 TRON 步骤。
- 最终工具选择和参数检查全部通过。
- 数据库与链上事实、来源和覆盖范围仍完整出现在最终答案。
- Multi-tool success 保持 100%。

## Recovery 与权限

- Permission accuracy 保持 100%；fast path 位于 Permission Gate 之后，不能绕过确认。
- `recovery_001/002` 仍因 HTTP adapter 不支持 fault injection 被 Evaluation 框架跳过，因此没有新的线上 recovery rate 数据。
- 单元测试覆盖 error、retry、多次工具调用、未知 contract 和多行截断拒绝。
- 原有 recovery/permission 测试全部通过。

验证结果：

- 全量测试：`178 passed`
- Ruff：通过
- `git diff --check`：通过
