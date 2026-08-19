# Agent Evaluation Framework

评估框架复用生产 `AgentState` 的 `execution_trace`，不修改 Agent 图和工具业务逻辑。被测 Agent 只收到 `user_query`、会话上下文和 execution mode；`ground_truth` 仅在请求完成后交给 evaluator/Judge。

## Dataset

`eval/test_cases.jsonl` 每行一个 case。必填字段是 `case_id`、`category`、`user_query`、`ground_truth`、`tags`。`ground_truth.expected_result` 表示预期终态，`required_facts`/`forbidden_facts` 是大小写不敏感的事实锚点；`expected_tools` 是必须调用工具的集合（空数组表示不应调用工具）；`expected_arguments` 使用 `tool/path/op/value` 约束，op 支持 `eq/contains/regex/in/gte/lte/exists`。`required_capabilities` 声明 case 对运行环境的依赖，便于运行前筛选和审计。安全 case 用 `human_review=true`。开放式语义 case 可设置 `judge=true`。

当前主数据集针对本项目真实范围：`public.justlend`、`public.croas_chain`、TRON 交易/节点查询、图表、Scheduler、Monitoring 和跨轮 Memory。未启用的 Ethereum、ClickHouse、Web Search、Knowledge Base 不进入主成功率。

普通 HTTP adapter 无法替换服务端工具，因此会自动跳过声明了 `fault_injection` 的 case，并把原因写入报告；这些 case 只在测试构图或可控 replay 中进入指标分母，避免把未发生的模拟故障当作恢复失败。

## Run

先启动服务，然后运行：

```bash
python -m chaincloud_agent_service.evaluation.cli \
  --dataset eval/test_cases.jsonl \
  --endpoint http://127.0.0.1:8001/chat \
  --output-dir eval_results
```

需要评判开放式 case 时增加 `--judge-model gpt-4o-mini`。Judge 使用独立、未绑定工具的模型实例，且只在 Agent 请求完成后读取 rubric 与候选答案。

离线 CI 可把之前采集的 `EvalObservation` JSONL 作为 replay：

```bash
python -m chaincloud_agent_service.evaluation.cli --dataset eval/test_cases.jsonl --replay observations.jsonl
```

结果写入 `eval_results/run_*.json` 和 `.md`。未提供 token usage 的模型会显示 `N/A`，不会按 0 统计。

## Judge、fault 与 ablation

Deterministic evaluator 始终先运行。只有 `judge=true` 的开放式答案才交给独立 `LangChainJudge`；工具选择、参数、安全门和禁止操作不交给 LLM 判断。`human_review=true` 会在报告中进入人工复核队列。

`FaultInjectingTool` 是测试构图时注入的透明代理，按 case 精确指定第 N 次调用的 timeout、429、参数错误、permission denied 或 fallback failure；它不依赖真实网络波动。生产 registry 不受影响。

运行 ablation：

```bash
python -m chaincloud_agent_service.evaluation.cli --dataset eval/test_cases.jsonl --endpoint http://127.0.0.1:8001/chat --ablation
```

当前 HTTP adapter 可真实切换 Planner（`planned/auto` 与 `direct`）。Recovery、Memory Recall、Context Compression 的 flag 已预留在 adapter contract；在应用提供 request-scoped 开关前，它们应标记为“not supported”，不得据此宣称实验生效。推荐后续在测试构图 factory 中映射：Recovery -> `max_tool_retries=0`，Memory -> `memory_recall_enabled=false`，Compression -> 禁用 rolling summary。无需实现新的业务能力。

## Metrics

Task Success 是所有 deterministic checks（及启用时 Judge）通过的 case 比例。Tool Selection 是期望工具集合命中率；Argument Accuracy 是逐条参数约束通过率；Permission Accuracy 是期望 action 与 trace action 一致率；Recovery Rate 是注入瞬时故障后 trace 出现 `recovered=true` 且任务成功的比例。Partial/Degraded/Failed 按 case 最终 outcome 统计。

性能直接来自 `request_summary` 和 `node_events`：端到端 P50/P95、节点 avg/P95、LLM/tool calls、token、tool/step retries、replans。Memory 指标只在 dataset/adapter 提供对应 check 时统计，否则为 `N/A`。

## Example report

以下只展示报告格式（示例数字，不代表当前模型实测）：

| Metric | Value |
|---|---:|
| Task Success | 83.3% |
| Tool Selection Accuracy | 91.7% |
| Tool Argument Accuracy | 88.9% |
| Recovery Rate | 100.0% |
| Permission Accuracy | 100.0% |
| P50 latency | 1,842 ms |
| P95 latency | 6,104 ms |
| Avg LLM Calls | 2.40 |
| Avg Tool Calls | 1.27 |
| Avg Tokens | 3,812 |

实际 Markdown 还会包含各 category 的 case 数/成功率、每个 case 的 outcome 与人工复核标记；JSON 保存完整 checks、observation、trace 摘要和节点性能数据。
