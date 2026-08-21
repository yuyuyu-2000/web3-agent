# Tool Result → Executor summarization → StepResult 横向性能分析

## 1. 结论摘要

值得实施 Executor deterministic StepResult fast path，但第一轮应严格限定在“成功、结果结构完整、success criteria 可由代码判定、无需继续调用工具”的 planned step，不能按 tool name 或 evaluation case 放行。

本次完整 Evaluation 有 30 个定义 case；HTTP adapter 实际执行 28 个，`recovery_001`、`recovery_002` 因不支持 fault injection 被框架明确跳过。有效 run 为 `run_20260821T074527Z`，28 个 case 的 task success rate 为 82.14%，共 37 次工具调用。另一次 `run_20260821T072304Z` 是服务受沙箱阻止连接本机 PostgreSQL 后产生的立即失败记录，全部排除，不作为性能证据。

最重要的横向发现是：

- Executor result summarization 是 planned 工具链的普遍固定成本，不只存在于 TRON。PostgreSQL 代表样本的 summarization 为 6,975～8,856 input tokens、2.81～8.06 秒；TRON 大结果在 canonical 化后仍为 10,385～11,524 input tokens、14.80～28.42 秒。
- TRON 是最明显的长尾放大器，但不是唯一瓶颈。主要共同成本是每次 summarization 都重新携带约 6,583-token Executor system prompt，以及 plan/current step、既有 StepResult、history；工具结果自身只占其中一部分。
- 在本次 run 中，可保守识别出 9 次“工具成功后即可由代码结束 planned step”的 summarization，合计 77,746 input tokens 和 92.879 秒 Executor wall time。跳过它们可直接减少 9 次 LLM calls；这是实测输入与时延上界，不等于端到端一定完整减少 92.879 秒，因为后续 Composer/Reviewer 的输入形态也会变化。
- 当前 telemetry 只记录每次 context input tokens，不记录单次 LLM output tokens；request summary 只有全请求 output 合计。因此不能诚实地给出这 9 次调用的精确 output-token 节省。合理报告方式是“至少减少 77,746 个已观测 input tokens，另加未单独归因的 summarization output tokens”，而不是伪造精确总 token 数。
- PostgreSQL 当前的 generic `structured_facts` 只保留前两行 sample。对 Top-5、最近 10 条等目标，它不足以直接表达完整 success criteria；fast path 必须从已持久化 raw result 做确定性完整投影，或升级为 query-result canonical contract，不能直接把现有 sample 当完整结果。
- Scheduler 与 Monitoring 在当前 Evaluation 中没有实际成功工具执行样本：Scheduler 在 Permission Gate 的 `need_confirm` 处停止；Monitoring 由确定性 draft 路径完成，没有 Tool Result、Executor summarization 或 StepResult。不能据此猜测它们的工具结果性能。

## 2. 测量口径与限制

原始大小取 `tool_result_records.raw_size_bytes`；raw tokens 使用当前 `TokenCounter` 同口径计数。模型名无法直接映射时使用 `cl100k_base`，因此 raw token 数是可复现估计值；Executor 完整 input tokens、dependency evidence tokens 和节点 duration 则直接来自 execution trace，是实测值。

“Executor summarization”在 Planned 模式中指同一步骤第一次 Executor 生成 tool call、工具返回后第二次 Executor 消费 ToolMessage 并输出无 tool call 的自然语言总结；该文本随后成为 `StepResult.summary`。Direct 模式没有 `StepResult` 和 Step Evaluator，工具返回后的 Direct Agent 调用可能同时承担“解释上一个结果、决定是否调用下一工具、或生成 draft”三种职责，不能与 Planned 的纯 StepResult summarization 完全等同。

当前 trace 不保存完整 Executor 自然语言输出，也不导出最终 checkpoint 的 `step_results`，所以 `StepResult.summary` 的精确 bytes/tokens 不可观测。可精确测量的是：

- `StepResult.evidence`：由原始 ToolMessage content 每条截断到 2,000 字符；
- `StepResult.structured_facts`：直接复制 Tool Result metadata 中的 structured facts；
- Composer 的 `critical_state` / `evidence` tokens 与 Reviewer 的 `critical_state` tokens；
- summary 的存在及其后续重复路径，但不能从 trace 反推出精确 summary token 数。

因此下表对 summary 标注“未单独记录”，不把 Composer context 的复合大小冒充 summary 大小。

## 3. 各工具类型代表样本

### 3.1 PostgreSQL：`db_002`

目标是查询 `public.justlend` 中 `amount_usd` 最大的 5 条事件。

| 指标 | 实测值 |
|---|---:|
| raw result | 1,016 bytes / 约 480 tokens |
| 进入 Executor 的 representation | raw；`representation=default`，`compressed=false` |
| dependency evidence | 580 tokens |
| Executor summarization 完整 input | 7,495 tokens |
| Executor summarization duration | 4,908.148 ms |
| StepResult summary | 存在，但 trace 未单独记录大小 |
| StepResult evidence | 1,016 bytes / 约 480 tokens |
| StepResult structured_facts | 501 bytes / 约 203 tokens |
| Composer context | 4,894 tokens，其中 critical_state 1,996、evidence 535、draft 2,003 |
| Reviewer context | 2,939 tokens，其中 critical_state 2,735 |

这里出现明显的同义重复：5 行 raw 先进入 Executor；Executor 把它扩展为自然语言 StepResult summary；Composer 同时收到 StepResult/执行状态和 evidence，并生成 draft；Reviewer 又消费最终答案与关键状态。相同的交易哈希、金额、操作类型和时间至少以 raw/evidence、summary、draft/answer 三种形式流转。

确定性 fast path 条件并未被当前 generic structured facts 完全满足：`row_count=5`，但 `sample` 只有前 2 行。用户明确要求 5 条，因此只用现有 structured facts 会丢 3 行，可能降低 correctness。安全做法是从 raw JSON 确定性保留全部 bounded rows，或让 PostgreSQL canonical contract 根据查询行数上限保留完整结果，再构造 StepResult。

PostgreSQL 的其他 Planned 实测：

| Case | raw bytes | dependency evidence | summarization input | duration |
|---|---:|---:|---:|---:|
| `db_003` | 20 | 90 tokens | 6,975 tokens | 2,808.970 ms |
| `db_004` | 3,860 | 1,897 tokens | 8,856 tokens | 8,062.795 ms |

即使 raw 只有 20 bytes，`db_003` 仍需要 6,975 input tokens，说明固定 prompt/context 是跨工具基础成本。`db_004` 要求最近 10 条，generic sample 同样不完整，是不能直接启用 fast path 的高风险 case。

### 3.2 TRON：`multi_001` 的 `get_tron_transaction`

| 指标 | 实测值 |
|---|---:|
| raw result | 7,654 bytes / 约 3,538 tokens |
| 进入 Executor 的 representation | tool-specific canonical，非 raw |
| canonical ToolMessage content | 1,257 bytes / 约 512 tokens |
| dependency evidence | 740 tokens |
| Executor summarization 完整 input | 10,385 tokens |
| Executor summarization duration | 14,797.057 ms |
| StepResult summary | 存在，但 trace 未单独记录大小 |
| StepResult evidence | 1,257 bytes / 约 512 tokens；未含完整 transaction/receipt/logs/internal transactions |
| StepResult structured_facts | 1,257 bytes / 约 512 tokens |
| Composer context | 该 case 为 10,486 tokens，其中 critical_state 4,322、evidence 1,469、draft 3,631 |
| Reviewer context | 7,957 tokens，其中 critical_state 7,736 |

canonical contract 已显著降低工具结果本体：raw 约 3,538 tokens 降到约 512 tokens。`result_id`、txid、transaction/receipt status、block、timestamp、fee、contract type、地址、关键 transfer、log/internal count 和资源费用已经足以表达该步骤的依赖字段与审计来源；完整 raw 通过 result_id 保留在 Tool Result Store。

但 Executor 完整输入仍有 10,385 tokens，因为除 740-token dependency evidence 外，还包含 6,583-token system prompt、1,837-token 左右 critical state/已有 StepResult 和约 1,293-token recent history。换言之，TRON raw 已不再是 summarization input 的主体；剩余瓶颈是“为一个确定性 canonical result 再调用一次通用 Executor”。

`multi_003` 的同一真实交易更明显：TRON summarization input 11,524 tokens、duration 28,419.188 ms，dependency evidence 702 tokens。相同 canonical 结果大小接近，但时延扩大近一倍，说明模型服务长尾与较大的既有执行上下文共同放大耗时，不能再归因于 raw payload。

`multi_002` 的 TRON canonical 表达为 793 bytes，明确给出 `OUT_OF_ENERGY`、block、fee、contract 和 internal transaction count，也满足“核验并明确结果”的依赖字段；其 summarization input 9,640 tokens、duration 18,219.827 ms。成功与失败状态均已结构化，不需要 LLM 才能判断链上执行失败。

单工具占位哈希 `tron_001/002` 不适合作为“大 TRON raw”代表：其 `get_tron_transaction` raw 只有 151 bytes，随后模型又调用多个 `tron_node_request`。这些调用混合了错误/空结果解释、fallback 和下一工具决策，不满足无歧义 fast path 条件，也不能用来估算真实交易 canonical 的收益。

### 3.3 Chart / 文件类：`chart_001`

该 case 为 Direct 模式，先执行 PostgreSQL，再执行 `generate_time_series`。

| 指标 | PostgreSQL 结果 | Chart 文件结果 |
|---|---:|---:|
| raw result | 384 bytes / 约 163 tokens | 161 bytes / 约 45 tokens |
| representation | raw / default | raw / default |
| Direct Executor 下次调用完整 input | 6,944 tokens | 7,248 tokens |
| Direct Executor 下次调用 duration | 1,987.485 ms | 4,126.007 ms |
| dependency evidence | 不适用；Direct 路径记入 recent_history | 不适用；Direct 路径记入 recent_history |
| recent_history | 241 tokens | 545 tokens（累计含 SQL 和 chart 交互） |
| StepResult | 不适用 | 不适用 |
| structured facts | 134 bytes / 约 44 tokens | 156 bytes / 约 40 tokens |

Chart 工具结果只有 `status=success`、`filepath`、`url`，且有 result_id/raw provenance，天然满足确定性“artifact created”结果表达。最后一次 Direct Agent 调用仍花 4.13 秒，随后 Composer/Reviewer 又运行两轮（本 case Reviewer 要求修订），但不能仅通过 Planned StepResult fast path 消除，因为 Direct 架构没有 StepResult，且第一次 SQL 结果必须继续供模型构造 chart 参数。

如果未来把 fast path 扩展到 Direct，Chart 工具成功后的最终收尾是高价值候选；SQL→Chart 中间节点则不能跳过模型，除非 chart 参数也能由 plan/tool contract 确定性映射。文件类当前完整 Evaluation 只有 chart HTML artifact，没有通用上传、PDF、CSV、文档生成等实际 case；这些类别无可用样本，不猜测。

### 3.4 Scheduler

`scheduler_001～003` 都有实际 Evaluation case，但没有实际 Scheduler Tool Result：前两例在 Planner 后由 Permission Gate 返回 `need_confirm`，第三例因缺少执行时间同样未进入工具执行。以 `scheduler_001` 为例，Planner 2,482 input tokens、26,068.753 ms，之后零 tool calls、零 Executor summarization、零 StepResult。

因此 Scheduler 的 raw bytes/tokens、representation、dependency evidence、summarization duration 和 StepResult 大小全部是“不适用/无实测样本”。不能基于 case 定义假设创建工具会返回 task ID、cron 或状态。

从设计条件看，未来若经确认后工具返回稳定的 `task_id`、normalized schedule、timezone、status、created_at 和 result_id，成功创建本身适合 A 类；但这只是 contract 建议，不是本轮实测分类证据。Permission Gate 必须保持在 fast path 之前，fast path 不能绕过确认。

### 3.5 Monitoring

`monitor_001～004` 有实际 Evaluation case，但当前走确定性 `monitor_draft` 路径：无 LLM calls、无 tool calls、无 Tool Result、无 Executor summarization、无 StepResult。`monitor_002` 总 latency 1.949 秒，trace 只有 `monitor_draft_created` decision。

因此 Monitoring 不是当前 summarization 瓶颈，也没有可用于 Tool Result 横向统计的样本。它实际上已经体现了“结构足够时不调用 LLM”的思路。未来真正的 monitor persistence/activation 若引入工具，仍需单独评测；不能从 draft case 推断其结果大小或时延。

## 4. A / B / C 分类

### A. 适合 deterministic StepResult fast path

第一批推荐：

1. `get_tron_transaction` 的 tool-specific canonical 成功或明确链上失败结果。canonical 已包含下一步依赖字段、success/failure status 和 result_id；复杂跨数据库比较应留给 Composer，而不是让 Executor 再把单工具事实改写一次。
2. PostgreSQL 标量聚合、单行 lookup、以及能够完整保留所有 bounded rows 的只读查询。当前 `db_003` 与 multi case 的单行依赖适合；Top-N/最近 N 条必须先解决 generic sample 截断。
3. Planned 模式中的 artifact generation success result，前提是 contract 包含 status、artifact path/url、存在性/校验信息和 result_id。当前 chart case 是 Direct，不能直接获得本轮 Planned fast path 收益，但 contract 本身满足条件。

fast path 输出应由代码构造：`status`、简短模板化 summary、完整且有界的 evidence、structured_facts、result_references、tool_calls、error。任何字段缺失、status 不一致、结果超出 contract、下一步仍需模型选择工具时 fallback 到现有 LLM。

### B. 适合 tool-specific canonical representation，但仍需要 Executor summarization

- `tron_node_request`：endpoint 返回形态取决于 path，当前 generic structured facts 对最新区块、错误体和不同 RPC payload 不够稳定。应先按 RPC endpoint family canonical 化；若用户要求解释复杂节点响应或结果仍有歧义，再保留 summarization。
- PostgreSQL 的开放式宽表、大结果、动态列、Top-N 但 canonical 未保留全部 requested rows 的结果。应先建立 query-result canonical，完整表达 selected columns、row_count、bounded rows、truncation flag、null/precision metadata；在 contract 尚不完整时不能 fast path。
- Scheduler 的未来真实执行结果，在没有稳定 create/list/update result contract 和确认后实测前，先归 B，而不是直接按工具名放 A。

### C. 应保留 LLM summarization

- 工具结果本身是长自然语言、搜索文档、日志语义归纳、模糊错误解释，或需要跨多个非标准字段推断才能知道是否达标；当前完整 Evaluation 没有 Web/Search/知识库类实际工具 case。
- 同一次 Executor 调用既要解释结果又要选择下一工具，且下一工具参数不能从 plan + canonical facts 确定性派生。例如 `tron_001` 的空/错误结果后多次 fallback、Direct 的 SQL→Chart 参数生成。
- 结果与 success criteria 存在语义歧义、contract version 未知、字段冲突、truncation、provenance 缺失，或需要复杂自然语言归纳。

## 5. 通用 fast path 判定条件

判定必须基于运行状态和 contract capability，而不是 tool name、SQL 文本、txid 或 evaluation case：

1. 当前节点是 Planned step，工具调用已结束，且没有待执行 tool call。
2. Tool execution terminal status 可确定；没有 retryable error、permission error、schema mismatch、partial transport failure 或 unresolved fallback。
3. result contract 声明版本和完整性；`structured_facts_complete=true`，并明确 `truncated=false`，或 success criteria 只依赖已保留字段。
4. 所有 dependency outputs 可由 typed facts 确定性提取，并通过 plan dependency schema 校验。
5. success criteria 可由机器谓词判断：字段存在、状态集合、row count、artifact existence、ID/hash 格式、数值/排序/limit 等；不需要自然语言语义判断。
6. 不要求复杂归纳、主观解释或跨字段/跨来源推理。跨来源解释可推迟到 Composer，但 StepResult 必须完整保留各来源事实。
7. `result_id`、tool name/args、content hash、evidence source、created_at 和 raw location 足够审计。
8. Permission Gate 已通过；fast path 不创建权限、不改变确认状态，也不处理未经批准的副作用结果。
9. contract 未知、字段缺失、status 冲突、截断、异常或 evaluator 机器校验失败时，自动 fallback 到 LLM summarization；schema mismatch 仍走既有 recovery。

建议让工具 contract 输出 capability metadata，例如 `terminal`、`complete_for_dependencies`、`complete_for_success_criteria`、`truncated`、`ambiguity_flags`、`provenance_complete`，由统一 predicate 判定。tool-specific adapter 负责生成这些状态，Graph 不维护工具名白名单。

## 6. 完整 Evaluation 的调用、token 与 latency 收益估算

保守候选为 `db_002`、`db_003`、`db_004` 各 1 次，以及 `multi_001～003` 各 2 次，共 9 次 Planned Executor result-summarization：

| Case / call | input tokens | duration |
|---|---:|---:|
| `db_002` PostgreSQL result | 7,495 | 4,908.148 ms |
| `db_003` PostgreSQL result | 6,975 | 2,808.970 ms |
| `db_004` PostgreSQL result | 8,856 | 8,062.795 ms |
| `multi_001` PostgreSQL result | 7,416 | 5,174.718 ms |
| `multi_001` TRON result | 10,385 | 14,797.057 ms |
| `multi_002` PostgreSQL result | 7,246 | 4,890.815 ms |
| `multi_002` TRON result | 9,640 | 18,219.827 ms |
| `multi_003` PostgreSQL result | 8,209 | 5,597.151 ms |
| `multi_003` TRON result | 11,524 | 28,419.188 ms |
| 合计 | **77,746** | **92,878.669 ms** |

理论上可减少：

- LLM calls：9 次，占本轮 28 case 已记录 156 次 LLM calls 的约 5.8%；若只看有 Planned StepResult 的正常成功链路，覆盖面很高。
- input tokens：至少 77,746；占有 token telemetry 的请求总 input 约 615,161 tokens 的 12.6%。Scheduler/Monitoring 的 request summary 没有 token usage，未放进分母。
- output tokens：会减少 9 个 Executor summary 的输出，但 trace 不提供 per-call usage，无法给出精确值。不能用 request 总 output 反推。
- Executor 串行 latency：实测节点时间上限 92.879 秒；平均每 call 10.320 秒。按 case 聚合，理论端到端 wall-time 改善约为 `db_002` 4.9 秒、`db_003` 2.8 秒、`db_004` 8.1 秒、`multi_001` 20.0 秒、`multi_002` 23.1 秒、`multi_003` 34.0 秒，前提是 deterministic construction 与 machine validation 的成本可忽略。
- 后续 token 还可能进一步下降，因为模板化 StepResult summary 会短于报告式 LLM summary，Composer/Reviewer 的 critical_state 可能缩小；这部分未实测，不计入 77,746。

9 次是收益上界而不是建议立即全部上线的安全集合。若第一版仅覆盖 contract 已完整的 TRON canonical 和单行/标量 PostgreSQL，则先覆盖 `db_003`、multi 三个 PostgreSQL dependency step、multi 三个 TRON step，约 7 calls；`db_002/db_004` 应等 PostgreSQL canonical 保留全部 requested rows 后再加入。

Direct 的数据库和 Chart case 还有 1～3 次 Direct Agent 工具后调用，但它们没有 StepResult；把这些也消除需要改变 Direct 收尾/工具链策略，不属于本轮通用 Planned StepResult fast path，未计入收益。

## 7. correctness / dependency propagation / recovery 风险

最可能因跳过 summarization 降低质量的 case：

- `db_002`：要求 5 行，当前 structured facts 只有 2 行 sample；直接 fast path 会漏数据。
- `db_004`：要求最近 10 条，generic sample 不完整，风险同上。
- `multi_003`：本次 PostgreSQL 实际返回 5 行，而目标是比较最大金额事件。LLM 能从排序结果选择第一行；通用代码若只看到“5 行成功”而不理解 step output selector，可能把多行错误传播到 TRON step。应由 plan dependency schema 明确 `selected_row=first`，或目标 SQL本身只返回 1 行。
- `tron_001/002/004`：空结果、fallback、多 endpoint 调用和预算耗尽混合，不能走成功 fast path。
- `tron_003`：无效 txid 后模型还错误调用 PostgreSQL；虽然这暴露现有策略问题，但 fast path 不能把参数错误包装成成功 StepResult。参数验证错误应保留 deterministic error/recovery 分支。
- future Scheduler：fast path 绝不能越过 Permission Gate；创建结果字段缺失时需 fallback。

dependency propagation 的核心风险不是“没有自然语言 summary”，而是 typed facts 不完整或没有声明 output selector。正确实现后，下游应优先消费 typed `structured_facts`，自然语言 summary 只用于展示，不应承担唯一依赖传递责任。

recovery 不会因 fast path 本身下降，只要 predicate 在任何 error、schema mismatch、missing field、ambiguity 或 truncation 时拒绝 fast path，并回到原 Executor/Evaluator 链路。建议 deterministic StepResult 仍经过一个廉价 machine validator；不必调用现有 LLM Step Evaluator来重新确认完全确定的成功状态，但第一阶段也可暂时保留 Evaluator以降低上线风险，此时仍能先省 Executor summarization 的 9 calls。

## 8. 按“收益 × 覆盖面 × 实现风险”排序的建议

1. **实现 Planned Executor deterministic StepResult fast path，首批覆盖 `get_tron_transaction` canonical terminal result和 PostgreSQL 单行/标量完整结果。** 收益最高，覆盖 multi-tool 关键链路，已有 result_id/provenance；风险可由严格 predicate 和 LLM fallback 控制。
2. **补 PostgreSQL query-result canonical contract，再扩到 bounded Top-N/列表。** 明确保留全部 requested rows、selected columns、row_count、truncated、null/precision、ordering/limit metadata。完成前不要让 `db_002/db_004` 仅依赖 sample fast path。
3. **让 fast path 输出短模板 summary，typed facts 成为 dependency 的唯一权威输入。** 避免 Executor 把短 canonical 扩展成报告，再被 Evaluator、Composer、Reviewer重复消费；Composer负责面向用户的叙述。
4. **为 fast path 增加 contract/state predicate 与 machine validator，不建立工具名/case 白名单。** 记录命中/拒绝原因、fallback 次数、correctness 对比和每类节省 tokens。
5. **第二阶段评估 Direct terminal tool fast finish，优先 Chart artifact 成功后的最后一次 Direct Agent。** 这有额外收益，但涉及 Direct 主体数据流，风险和改动面高于 Planned StepResult fast path。
6. **为 `tron_node_request` 建 endpoint-family canonical contract。** 当前结果形态多样且 case 中存在反复调用，先结构化再判断是否需要 LLM，不宜直接 fast path。
7. **Scheduler 等待真实确认后执行样本再分类；Monitoring 暂不优化。** Scheduler 当前主要长尾是 Planner（`scheduler_001` 26.07 秒），不是 result summarization；Monitoring 已是无 LLM draft fast path。

## 9. 最终推荐

现在值得实施 Executor deterministic fast path。第一批应覆盖：

- `get_tron_transaction` 已 canonical 化、terminal、字段完整、有 result_id 的成功或明确链上失败结果；
- PostgreSQL `row_count` 与完整结果一致的标量/单行只读结果，尤其是下一步只需要 tx_hash 等确定字段的 dependency step；
- 在 Planned 路径实际出现时，contract 完整的 artifact success result。

第一批不应覆盖：generic facts 已截断的 Top-N/最近 N 条、任意 schema/error/retry/fallback 状态、`tron_node_request` 的未规范化响应、仍需模型决定下一工具的 Direct/Planned 中间调用、未经 Permission Gate 确认的 Scheduler 操作。

这项优化的价值不只是 TRON：TRON 提供最高单点长尾收益，而 PostgreSQL 证明即使工具结果极短，通用 summarization 仍会重复 6.5K 以上固定 prompt。严格的状态驱动 fast path 能同时改善覆盖面和长尾，并且在异常、缺字段或歧义时保留现有 LLM recovery，不需要针对任何 evaluation case、具体 SQL 或 txid 硬编码。
