# Planner 第一轮优化实施与 Multi-tool Evaluation 报告

## 结论

本轮 Planner 优化已实施，相关单测、完整单测和静态检查全部通过。最终代码下，三个 multi-tool case 的后台工具证据均不再出现 `postgres_list_tables`、`postgres_table_schema` 或额外 `amount_usd GROUP BY`：正常路径已经收敛为 `postgres_select → get_tron_transaction`。

但端到端评测仍受 180 秒 HTTP adapter timeout 和超时后丢失部分 trace 的影响。最终回归中：

- `multi_002` 成功，2 step、2 tool calls、9 LLM calls、53.88 秒；
- `multi_001` 和 `multi_003` 均已在后台完成两次最小充分工具调用，但客户端在 180 秒处超时，因而正式 observation 中的 plan、step、LLM calls 和 reply 被清空；这两项必须报告为不可观测，不能错误写成 0；
- 最终正式成功率为 1/3，即 33.3%；同一版主要改动的上一轮回归曾达到 2/3，即 66.7%，说明端到端成功率仍受模型生成时延波动显著影响。

因此，本轮对 over-planning 的结构目标已经达到，但没有解决大型链上回执、Executor/Answer Composer/Reviewer 生成耗时以及 adapter 超时观测丢失问题。

## 实施内容

### 1. Planner 获得同源、裁剪后的 trusted schema facts

在 `src/chaincloud_agent_service/agent/schema_context.py` 新增 `build_planner_trusted_schema_facts()`：

- schema source 与 Executor 相同，均读取 `settings.agent_database_schema_path`；
- 当前来源为 `config/agent_database_schema.md`；
- 从 Markdown 的表章节和字段表自动提取：
  - `schema_source`；
  - 已确认的 table mapping；
  - known columns；
- Planner 当前可见的紧凑事实包括：
  - `JustLend / justlend → public.justlend`；
  - `croas_chain / cross_chain → public.croas_chain`；
  - 两张表各自的已知字段，包括 `amount_usd`、`tx_hash`、`deposit_tx_hash` 等；
- 不复制完整 Executor system prompt；
- 不包含 sample rows、SQL 示例、长篇数据语义、回答风格或合约解码说明；
- 单测要求裁剪结果小于 1500 字符，当前满足。

`graph.py` 在构图时只生成一次该裁剪上下文，并同时用于初始 Planner 和 replan。Executor 仍使用原完整 system prompt，Direct/Planned 主体拓扑没有改变。

### 2. Minimal Sufficient Plan 规则

Planner prompt 已加入通用最小充分约束：

1. 正常路径中的每一步必须直接贡献于用户目标、success criteria、必要证据或权限边界；
2. trusted schema 已确认 table mapping 和 required columns 时，直接规划目标查询；
3. 不得用 `postgres_list_tables` 或 `postgres_table_schema` 重复确认已知事实；
4. 按动态数据依赖拆 step，不按工具数量机械拆分；
5. 用户未要求、success criteria 不依赖的额外验证不得加入正常路径；
6. “更全面”“顺便确认”“确认最大值是否并列”不构成额外查询的充分理由；
7. Answer Composer 会在取证完成后自动形成比较、整合和说明，因此 Planner 不应增加“汇总结果”“比较来源”“形成报告”step，也不得在整合阶段重复调用已成功的取证工具。

这些规则不包含任何 evaluation case ID、固定 SQL、固定 txid 或测试数据。

### 3. Schema discovery 改为 error-triggered recovery

本轮没有删除 discovery 工具，也没有降低工具能力。其使用语义调整为：

```text
已知 table/columns
  → 直接执行目标 SQL
  → 成功：继续下游步骤
  → undefined_table：使用 postgres_list_tables 或修正 mapping 后重试
  → undefined_column / type mismatch / schema mismatch：使用 postgres_table_schema 后修正并重试
  → permission / guardrail error：不得用 discovery 或等价工具绕过
```

Planner prompt 明确要求 discovery 不得预先进入正常 plan。Planned Executor 在收到结构化工具错误后也会看到同一限制：只有已发生 `undefined_table`、`undefined_column`、类型不匹配或其他 schema mismatch 时才允许 schema discovery。

这保留了 failure recovery，不把 discovery 误当成成功路径的固定前置成本。

### 4. Discovery 工具 description

`postgres_list_tables` description 已从：

> 查询表数据前可先调用它确认表名。

改为明确的 Schema recovery 工具，只在目标表未知或目标 SQL 已出现 `undefined_table/表不存在` 时使用，并明确禁止在已知 mapping 的正常查询前调用。

`postgres_table_schema` description 也改为只在目标 SQL 已出现 `undefined_column`、类型不匹配或其他 schema mismatch 时使用，并明确禁止重复确认 trusted schema 已有字段。

### 5. 不相关聚合提示清理

Executor 原系统提示中的“优先用聚合 SQL 同时确认日期覆盖、记录数和金额分布”被收窄为：只有用户目标或成功标准需要统计分析时才执行聚合，不得为了“顺便验证”增加分布或并列性查询。

这直接消除了 `multi_003` 中仅为确认最大值唯一性而执行 `GROUP BY amount_usd` 的提示诱因。

### 6. Permission 与架构边界

本轮没有：

- 删除或绕过 Permission Gate；
- 改变只读/副作用工具分类；
- 改变 Direct/Planned 主体图结构；
- 为某个 case、SQL 或 txid 增加特判；
- 把 schema discovery 从工具集中移除。

最终成功的 `multi_002` 仍产生两个 `permission_gate=allow` 事件，对应两个只读 step。timeout case 的正式 trace 被 adapter 丢弃，但后台调用也均为只读工具。

## 测试结果

### 相关单测

命令：

```text
.venv/bin/pytest -q tests/test_planning.py tests/test_pg_schema_tool_descriptions.py tests/test_agent_graph_routing.py tests/test_permission_gate.py tests/test_evaluation_framework.py
```

结果：

```text
31 passed in 1.80s
```

补充最终 Planner 规则后再次运行直接相关集合：

```text
12 passed in 1.27s
```

覆盖内容包括：

- Planner prompt 包含 Minimal Sufficient Plan；
- known schema 禁止正常路径 discovery；
- discovery 只作为 recovery；
- 禁止无关最大值并列验证；
- 禁止独立汇总 step 和重复取证；
- trusted facts 与 Executor schema source 同源；
- table mapping 与 known columns 正确提取；
- trusted facts 不含 SQL 示例和长文档内容；
- schema source 不可用时安全降级；
- discovery tool descriptions 不再诱导查询前探查；
- 原有 Planner、graph routing、Permission Gate 和 evaluation 行为保持通过。

### 完整单测

```text
170 passed in 1.98s
```

此前同一完整集运行结果为 `170 passed in 2.28s`；两次均全部通过。

### 静态检查

```text
.venv/bin/ruff check <本轮相关源文件和测试>
All checks passed!
```

## Evaluation 口径

### 修改前基线

主要对比采用最近的修改前报告：

- `eval_results/run_20260820T123658Z.json`

由于该报告的 `multi_001` 和 `multi_003` 都在 180 秒 timeout 后丢失 trace，补充使用：

- `eval_results/run_20260819T133827Z.json`；
- 修改前持久化 `tool_results`；
- 已完成的 over-planning 重建分析。

因此，修改前的 plan/step 信息分为“完整 trace 可证”和“后台调用重建”。

### 修改后回归

第一轮修改后运行：

- `eval_results/run_20260821T023250Z.json`

补强“Answer Composer 自动汇总，不规划独立整合 step”后的最终代码运行：

- `eval_results/run_20260821T024122Z.json`

最终结论以第二个文件为正式结果；第一个文件用于说明模型输出的随机性和 prompt 补强原因。

评测只包含 `multi_001～003`，通过新启动且确认加载当前代码的独立 HTTP 服务执行。

## 每个 case 的最终结果与修改前比较

### 汇总表

| Case | 修改前 plan/step | 修改前 tools | 修改前 LLM calls | 修改前 latency | 修改前结果 | 最终代码 plan/step | 最终 tools | 最终 LLM calls | 最终 latency | 最终结果 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| `multi_001` | 4 个计划 step；完整旧轨迹执行到 3 | 正常计划 4；旧完整轨迹执行 3 | 10（阻断前） | 60.10s（旧完整轨迹）；最近基线 180.00s timeout | failed | 正式 trace 因 timeout 不可观测；后台工具阶段符合最小 2 step | 2（后台可证） | N/A，不能写 0 | 180.00s timeout | failed |
| `multi_002` | 最近基线 3 | 3 | 12 | 112.85s | runtime completed；旧 permission 评分使报告失败 | 2 | 2 | 9 | 53.88s | success |
| `multi_003` | 可重建 3 个执行阶段 | 5（后台可证） | N/A | 180.01s timeout | failed | 正式 trace 因 timeout 不可观测；后台工具阶段符合最小 2 step | 2（后台可证） | N/A，不能写 0 | 180.00s timeout | failed |

注意：timeout observation 的 `execution_trace={}` 是 adapter 的错误分支行为，不代表 Agent 实际执行了 0 step、0 tool 或 0 LLM call。表中只把正式 trace 无法恢复的字段写为 N/A，同时单列后台持久化工具事实。

### multi_001

修改前完整 plan 可重建为：

1. `postgres_list_tables`；
2. `postgres_table_schema(public.justlend)`；
3. 查询最大 `amount_usd` 事件；
4. 按 `tx_hash` 查询 TRON。

修改前 2026-08-19 完整轨迹：

- 已执行 step：3；
- 已执行 tools：3；
- LLM calls：10；
- latency：60.10 秒；
- 在 step 4 前被旧 State Validation 阻断。

修改前最近一次 2026-08-20 运行在 180.00 秒 timeout，trace 丢失。

最终代码回归：

- 正式 observation：180.004 秒 timeout，trace 被清空；
- 后台持久化工具结果明确只有：
  1. `postgres_select`，SQL 直接使用 `public.justlend`、`amount_usd`、`tx_hash`，`ORDER BY amount_usd DESC NULLS LAST LIMIT 1`；
  2. `get_tron_transaction`，参数来自第一步返回的 tx hash；
- 没有 `postgres_list_tables`；
- 没有 `postgres_table_schema`；
- 没有 `GROUP BY amount_usd`；
- 没有第三次工具调用。

因此 over-planning 已从“4 step / 4 tools 的正常计划”收敛到后台可证的“2 个必要工具阶段”。但 adapter 没保留最终 plan JSON，无法审计它是否还包含无工具 summary step；本轮 prompt 已明确禁止这类 step，下一步应把 plan 持久化到 timeout-safe trace 才能做确定性断言。

中间回归曾出现：

- 3 step；
- 3 tools；
- 第三步重复调用 `get_tron_transaction`；
- 12 LLM calls；
- 179.95 秒；
- success。

这促使本轮追加通用规则：最终整合由 Answer Composer 自动完成，不得单独规划整合 step 或重复取证。最终代码的后台工具结果已不再出现该重复调用。

### multi_002

修改前最近基线：

1. `postgres_table_schema(public.croas_chain)`；
2. `postgres_select` 取非空 `deposit_tx_hash`；
3. `get_tron_transaction` 核验。

指标：

- step：3；
- tool calls：3；
- LLM calls：12；
- latency：112.85 秒。

最终代码：

1. `postgres_select`；
2. `get_tron_transaction`。

指标：

- step：2；
- tool calls：2；
- LLM calls：9；
- latency：53.88 秒；
- deterministic outcome：success；
- permission accuracy：通过，两个只读 step 均为 `allow`。

相对最近基线：

- step：`3 → 2`，减少 1，下降 33.3%；
- tools：`3 → 2`，减少 1，下降 33.3%；
- LLM calls：`12 → 9`，减少 3，下降 25.0%；
- latency：`112.85s → 53.88s`，减少 58.97 秒，下降 52.3%；
- schema discovery：`1 → 0`。

这是本轮优化最完整、可直接量化的正向结果。

### multi_003

修改前后台可重建工具序列：

1. `postgres_list_tables`；
2. `postgres_table_schema(public.justlend)`；
3. 最大事件 `postgres_select`；
4. `GROUP BY amount_usd` 分布/并列验证；
5. `get_tron_transaction`。

可重建为 3 个执行阶段，工具调用共 5 次。正式 observation 在 180.01 秒 timeout 后清空 trace，因此旧 LLM calls 不可用。

最终代码回归：

- 正式 observation：180.002 秒 timeout，trace 被清空；
- 后台持久化结果只包含：
  1. 直接查询 `public.justlend` 最大事件的 `postgres_select`；
  2. 使用返回 tx hash 的 `get_tron_transaction`；
- 没有 list tables；
- 没有 table schema；
- 没有 `GROUP BY amount_usd`；
- 没有其他验证工具调用。

结构变化：

- 可证工具调用：`5 → 2`，减少 3，下降 60%；
- schema discovery：`2 → 0`；
- 无关并列验证：`1 → 0`；
- 执行阶段从重建的 3 个收敛到两个必要工具阶段；
- latency 仍为 timeout 上限，未改善为可完成状态；
- LLM calls 因 timeout trace 丢失仍不可比较。

这说明 `multi_003` 的 over-planning 已消除，但其失败根因已经转移到/保留在性能和观测层，而不是 schema 探查或额外 GROUP BY。

## 成功率比较

### 修改前

`run_20260820T123658Z` 中三个 multi-tool case 的正式 outcome 均为 failed，报告成功率为 0/3。需要注意：其中 `multi_002` runtime 实际 completed，但当时 `expected_permission=none` 的评分口径把正常只读 `allow` 错判为失败；这项修复属于工作区已有改动，不是本轮 Planner 优化新增。

### 修改后第一轮

`run_20260821T023250Z`：

- `multi_001`：success；
- `multi_002`：success；
- `multi_003`：timeout failed；
- 成功率：2/3，66.7%；
- tool selection accuracy：66.7%；
- permission accuracy：100%；
- P50 latency：179.95 秒；
- 仅对有完整 trace 的两个成功 case统计：平均 10.5 LLM calls、2.5 tool calls。

### 最终代码回归

`run_20260821T024122Z`：

- `multi_001`：timeout failed；
- `multi_002`：success；
- `multi_003`：timeout failed；
- 成功率：1/3，33.3%；
- tool selection accuracy：33.3%，因为 timeout case 被 adapter 记录为零工具；
- tool argument accuracy：33.3%，同样受 timeout observation 清空影响；
- permission accuracy：100%；
- P50 latency：180.002 秒；
- 完整 trace 仅有 `multi_002`，因此报告中的平均 LLM/tool calls 实际等于该单例：9 / 2，不能推广成三个 case 的真实平均。

从修改前正式报告的 0% 到最终正式报告的 33.3% 有提升，但两次修改后运行从 66.7% 波动到 33.3%，不能把单轮成功率变化全部归因于 Planner。结构指标更稳定：所有最终后台记录都不再出现 schema discovery 或额外 GROUP BY。

## LLM calls 与 latency 解释

一个完成的 2-step planned case 通常包含：

- 1 次 Planner；
- 每个有工具 step 各 2 次 Executor（生成 tool call、消费结果形成 step summary）；
- 每个 step 各 1 次 Evaluator；
- 1 次 Answer Composer；
- 1 次 Reviewer。

合计通常为 9 次 LLM calls，与最终成功的 `multi_002` 完全一致。

修改前多一个 schema step 时，通常增加：

- 2 次 Executor；
- 1 次 Evaluator；
- 1 次 Tool；
- 1 次只读 Permission Gate。

所以 `multi_002` 从 12 降至 9 LLM calls 与架构预期一致。

但 `multi_001` 和 `multi_003` 的最终后台工具在很早阶段已经完成，客户端仍可能在后续 Executor summary、Evaluator、Answer Composer 或 Reviewer 阶段超过 180 秒。TRON receipt 体积、模型生成长度和模型端排队仍是显著变量。Planner 去冗余减少了调用和上下文，但不是完整的 latency 修复。

## Correctness、Recovery 与 Permission 评估

### Correctness

- `multi_002` 证明直接使用 trusted schema 能正确生成 SQL、传递 `deposit_tx_hash` 并完成链上核验；
- `multi_001`、`multi_003` 的后台 SQL 和 txid 参数均正确；
- 删除 discovery 没有导致 undefined table/column；
- 删除 `GROUP BY` 没有影响最大事件查询和链上 lookup；
- 没有硬编码 case、SQL 或 txid。

### Recovery

- discovery 工具仍注册且可调用；
- Planner、tool description 和 Planned Executor 都将其定义为 schema 错误后的 recovery；
- 瞬时错误仍走原工具层 retry；
- permission/guardrail 错误禁止通过 discovery 绕过；
- 本轮真实 multi-tool 数据库查询均成功，因此 evaluation 没有触发 undefined table/column recovery；相关单测验证的是提示与工具契约，尚未增加带真实 schema error 注入的 graph integration test。

### Permission

- Permission Gate 未移除、未降级；
- 只读步骤仍逐步产生 `allow`；
- 副作用工具政策未修改；
- recovery 不改变 permission 分类；
- 完整测试中的 permission tests 全部通过。

## 已知限制与下一轮建议

1. timeout adapter 仍会把已执行工具、plan、StepResult 和 LLM calls 全部丢弃，导致失败 case 的结构指标被错误显示为 0。应优先做 timeout-safe trace 持久化或流式采集。
2. Planner 最小性目前主要由 trusted facts、prompt、工具描述和 Executor recovery 契约约束。若需要确定性保证，可在下一轮增加基于 trusted schema state 的 plan lint/reject-and-retry，但应避免以关键词或具体表的测试规则硬编码。
3. 应新增 schema error fault-injection integration test，验证 `postgres_select(undefined_column) → postgres_table_schema → 修正 SQL → 成功`，同时断言正常成功路径 discovery 次数为 0。
4. 应压缩 TRON receipt 进入 Executor、Answer Composer 和 Reviewer 的上下文，只保留 txid、状态、区块、时间、费用、合约、必要 log 摘要和 result reference。
5. 应对同一代码版本重复运行至少 3～5 次 multi-tool evaluation，报告均值、P50/P95 和成功率置信区间，避免单次模型时延波动掩盖 Planner 结构收益。

## 最终交付判断

第一轮 Planner 优化已经完成以下目标：

- Planner 获得与 Executor 同源但裁剪后的 trusted schema facts；
- 已知 table/columns 时直接规划目标查询；
- schema discovery 从默认前置改为 schema error recovery；
- discovery 工具 description 不再诱导查询前探查；
- 无关 `GROUP BY` 验证从最终工具路径消失；
- Permission Gate 和 Direct/Planned 主体架构保持不变；
- 没有 evaluation case、具体 SQL 或 txid 硬编码；
- 170 个单元测试和 Ruff 检查全部通过；
- 最终后台工具路径在三个 multi-tool case 上均收敛到两个必要工具调用。

尚未完成的是端到端性能与 timeout-safe observability。最终正式成功率为 33.3%，不能宣称评测已全面通过；但 over-planning 的直接结构证据已经从修改前的 schema discovery 和额外 GROUP BY，变为最终运行中的零 discovery、零额外聚合。
