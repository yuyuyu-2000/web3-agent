# Step Evaluator deterministic fast path 空间分析

## 1. 结论摘要

基于最新有效评测 `eval_results/run_20260821T084656Z.json`：

- 完整 Evaluation 共 28 个有效 case，2 个 recovery case 因 adapter 不支持 fault injection 被跳过。
- 全部 Planned 执行中共发生 **10 次 Step Evaluator 调用**，总 duration **40,835.618 ms**，平均 **4,083.562 ms/次**。
- 10 次决策分布为：**pass 10，retry 0，replan 0，partial 0，fail 0**。
- 其中 **7 次**发生在 deterministic StepResult 之后，占全部 Evaluator 调用的 **70%**；总 duration **32,043.388 ms**，平均 **4,577.627 ms/次**。
- deterministic StepResult 后的 Evaluator **从未产生非 pass 决策**：pass 7，其他动作均为 0。
- 其余 **3 次** Evaluator 调用消费普通 LLM-generated StepResult，总 duration **8,792.230 ms**，平均 **2,930.743 ms/次**，也全部 pass。

因此，当前样本明确显示存在 Step Evaluator fast path 空间：若为这 7 次 deterministic StepResult 增加一个真正逐项覆盖 step success criteria 的 machine validator，并且 validator pass，则本 run 可安全候选跳过 **7 个 LLM call**，直接减少观测到的 **32.043 秒串行节点延迟**。

但不能把当前 `deterministic_step_result_eligible=true` 直接等同于“步骤语义成功”。现有 predicate 证明的是工具结果的结构、终态、完整性、无歧义和 provenance，不证明任意自然语言 `success_criteria` 已满足。建议采用严格白名单式 machine validator；无法机器证明、存在歧义、错误、partial、重试或 recovery 的路径继续使用现有 LLM Evaluator。

## 2. 数据源与统计口径

### 2.1 数据源

- 最新 fast path 总结：`planned_executor_deterministic_fast_path_report.md`
- 最新有效 Evaluation：`eval_results/run_20260821T084656Z.json`
- Evaluation 摘要：`eval_results/run_20260821T084656Z.md`
- Evaluator 实现：`src/chaincloud_agent_service/agent/evaluation/evaluator.py`
- Planned graph 路由：`src/chaincloud_agent_service/agent/graph.py`
- deterministic StepResult predicate：`src/chaincloud_agent_service/agent/step_result_fast_path.py`

### 2.2 “Planned Step Evaluator 调用”的定义

本报告以 execution trace 中 `node_events.node_name == "evaluator"` 为一次实际调用，并以同一 trace 的 `decision_events.decision_type == "evaluator"` 取得最终动作。

这会计入 `direct_003`：虽然数据集 category 是 `direct`，本次 API override/路由实际进入了 Planned graph，并真实产生了 planner、executor、evaluator、composer、reviewer 节点。统计按运行路径而非数据集标签归类。

不计入：

- 最终 Answer Reviewer；
- Evaluation 框架的 deterministic checks/judge；
- Planner、Executor summarization、Composer；
- 没有进入 Planned evaluator 节点的 case。

### 2.3 StepResult 来源判定

- 若同 case、同 step_id 在 Evaluator 决策前存在 `executor_fast_path: hit`，标记为 **deterministic StepResult**。
- 若为 `executor_fast_path: reject` 后回到 Executor summarization，或该步骤没有 fast-path hit，标记为 **LLM-generated StepResult**。

本 run 有 7 hit、2 reject/fallback。`direct_003` 的 StepResult 也由普通 Executor 生成。因此来源合计为 deterministic 7 次、LLM-generated 3 次。

## 3. 全部 Planned Step Evaluator 调用明细

| # | Case | Step | StepResult 来源 | fast-path 状态 | Evaluator duration | 决策 | 决策理由/反馈 |
|---:|---|---|---|---|---:|---|---|
| 1 | `direct_003` | `step_1` | LLM-generated | 未命中 deterministic path | 2,408.300 ms | pass | 结果已满足步骤目标，无需修正。 |
| 2 | `db_001` | `step_1` | LLM-generated | reject：`incomplete_structured_facts` | 4,037.599 ms | pass | 本步骤结果已满足成功标准，可继续后续步骤。 |
| 3 | `db_002` | `step_1` | LLM-generated | reject：`incomplete_structured_facts` | 2,346.331 ms | pass | 结果可直接用于后续步骤。 |
| 4 | `db_003` | `step_1` | deterministic | hit：`eligible_complete_contract` | 1,986.859 ms | pass | 步骤已成功完成。 |
| 5 | `multi_001` | `step_1` | deterministic | hit | 2,813.775 ms | pass | 结果正确。 |
| 6 | `multi_001` | `step_2` | deterministic | hit | 3,588.012 ms | pass | 步骤结果可靠且证据完整。 |
| 7 | `multi_002` | `step_1` | deterministic | hit | 2,673.721 ms | pass | 结果有效，可进入下一步骤。 |
| 8 | `multi_002` | `step_2` | deterministic | hit | 6,697.033 ms | pass | 后续步骤可继续使用该交易数据。 |
| 9 | `multi_003` | `step_1` | deterministic | hit | 1,967.592 ms | pass | 步骤已完成；可把 tx_hash 及关联字段传给下一步。 |
| 10 | `multi_003` | `step_2` | deterministic | hit | 12,316.396 ms | pass | 可继续；建议最终报告标注 step_1 tx_hash 与 step_2 txid 的对应关系。 |

### 3.1 汇总

| StepResult 来源 | 调用次数 | 占全部 Evaluator | duration 合计 | 平均 duration | pass | retry | replan | partial | fail |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deterministic | 7 | 70% | 32,043.388 ms | 4,577.627 ms | 7 | 0 | 0 | 0 | 0 |
| LLM-generated | 3 | 30% | 8,792.230 ms | 2,930.743 ms | 3 | 0 | 0 | 0 | 0 |
| **合计** | **10** | **100%** | **40,835.618 ms** | **4,083.562 ms** | **10** | **0** | **0** | **0** | **0** |

完整 Evaluation 的 Evaluator duration P95 为 **9,787.683 ms**；deterministic 组中还出现一次 **12,316.396 ms** 的长尾。由于 evaluator 是步骤间串行门，以上时间基本直接进入端到端 critical path，不能被其他 LLM 节点并行隐藏。

## 4. Evaluator 的输入信息

### 4.1 实际输入 schema

`evaluate_step()` 向模型发送两条消息：

1. 固定 system prompt：定义 pass/retry/replan/partial/fail，并要求逐项核对 success criteria、处理错误、权限、critical/non-critical 等规则。
2. JSON human message：

```json
{
  "step": "PlanStep.model_dump()",
  "candidate_result": "StepResult.model_dump()"
}
```

因此每次 Evaluator 实际能看到：

- Planned step：step id、目标/描述、success criteria、critical、预计工具调用数、fallback tools、依赖等 PlanStep 字段；
- candidate StepResult：status、summary、evidence、structured facts、dependency outputs、result references、provenance、tool calls、error 等字段。

Evaluator **不直接重新调用工具**，也不重新读取 raw result；它只判断被放入 candidate StepResult 的内容。

### 4.2 普通 LLM-generated StepResult 输入

普通路径由 Executor 对工具输出作自然语言 summarization，再由 `complete_step_node` 建立 StepResult。其输入特点是：

- `summary` 是 Executor LLM 的回答；
- `evidence` 是每个 ToolMessage 最多 2,000 字符的文本片段；
- `structured_facts` 来自 tool result metadata；
- `result_references` 保留 result_id、工具名、参数、时间、来源、raw location、hash；
- `tool_calls` 记录工具名；
- unresolved tool error 会进入 `error`，并把 status 置为 failed/partial。

本 run 中三个普通结果分别是：

- `direct_003`：无工具的范围边界回答，Evaluator 核对“不能由有限数据库推出 TRON 全链不存在”这一语义目标；
- `db_001`：多行聚合结果，fast path 因 structured facts 不满足当前完整性约束而 fallback；
- `db_002`：Top-5 多行结果，同样 fallback。

这些结果仍需要 LLM Evaluator 的理由更充分：自然语言 summary 可能遗漏字段、排序、数量、范围限制或错误地解释多行结果，现有机器 contract 没有完全证明语义成功。

### 4.3 deterministic StepResult 输入

deterministic path 不让 Executor LLM总结工具返回，而是直接构造：

- `status = success`；
- 固定 summary：“工具已成功返回完整、无歧义的结构化结果，可由 result_id 追溯”；
- evidence 包含 result_id、result_kind、structured_facts；
- structured_facts 保存机器结构化事实；
- dependency_outputs 以 step_id 保存同一 facts，供后续参数解析；
- result_references 与 provenance 保存工具、参数、来源、raw location、内容 hash 等；
- tool_calls 保存唯一工具名；
- error 为空。

7 次 hit 的结果类型是：

- PostgreSQL 单行只读查询：`db_003`、三个 multi-tool case 的 step_1，共 4 次；
- TRON canonical transaction：三个 multi-tool case 的 step_2，共 3 次。

## 5. deterministic 后的 Evaluator 实际重复验证了什么

### 5.1 已被现有 fast-path predicate / contract 机器保证的条件

在 7 次 hit 上，Evaluator 再次看到并原则上复核了以下已经由代码 predicate 保证的事实：

- 没有 unresolved tool error；
- 本 step 实际恰好一次 tool call；
- Planner 预计该 step 恰好一次 tool call，不需要继续调用工具；
- 恰好存在一个 ToolMessage/result；
- tool result metadata 和 result contract 存在；
- result 是 terminal；
- structured facts 被工具 contract 标记为 complete；
- result 未截断；
- ambiguity 为空；
- provenance complete；
- contract 显式标记 deterministic eligible；
- result kind 属于当前白名单：单行 `tabular_query` 或 `canonical_transaction`；
- PostgreSQL 结果为只读且 `row_count == 1`；
- structured facts 类型正确；
- result reference 的 result_id、tool name、created_at、evidence source、raw result location、content hash 完整；
- StepResult 的 status、evidence、facts、dependency output、provenance 和 tool call 列表均由代码确定性构造，而非模型自由生成。

这解释了为什么 7 次 Evaluator 全部 pass，以及其反馈主要只是“结果完整/可靠/可继续”。`multi_003 step_2` 的建议只是最终呈现建议，并未改变 step 决策。

### 5.2 现有 predicate 尚未保证、不能假装已经保证的条件

以下是 LLM Evaluator 理论上仍有增量价值的部分：

- 工具是否就是该 step 语义上正确的工具；
- 工具参数是否满足 step 的具体 success criteria；
- 返回 facts 是否逐项覆盖自然语言 success criteria；
- “返回完整”是否等于“回答了计划问题”，尤其是空结果、业务条件不满足、错误网络/环境、查询 SQL 语义错误；
- step_1 输出是否包含后续依赖真正需要的字段；
- 多源对比步骤中，两份结果之间是否一致；
- 计划本身是否有逻辑缺陷，是否应 replan；
- 某个 canonical transaction 虽结构完整，但链上状态失败时，该 step 是否仍算成功；
- 工具 contract 或 adapter 若错误地标记 complete/eligible，Evaluator 是否能成为第二道防线。

最关键的区别是：**structured facts complete 是“工具表示完整”，不是“step success criteria 已满足”。** 当前 7/7 pass 是很强的优化信号，但样本量不足以证明可无条件删除 Evaluator。

## 6. 建议的路由设计

建议实现如下保守决策门，而不是仅以 StepResult 来源判断：

```text
deterministic StepResult
  AND existing fast-path predicate pass
  AND machine step validator pass
  AND no ambiguity/error/partial/retry/recovery state
    -> complete step directly
    -> append an auditable machine_evaluator/pass decision
    -> skip LLM Step Evaluator

otherwise
    -> preserve current LLM Step Evaluator
```

### 6.1 machine validator 至少应检查

- `candidate.status == success` 且 `error is None`；
- fast-path capability contract 的所有既有条件仍成立；
- tool name 在 step 的允许/计划工具集合中；
- tool arguments 可机器校验，并与 step/dependency binding 一致；
- success criteria 必须能编译为受支持的结构化断言，且逐项为 true；
- 后续依赖字段声明存在、非空、类型正确；
- 需要关联时，对规范化后的关键字段做相等性校验，例如 DB `tx_hash` 与 TRON `txid`；
- 空结果语义必须显式声明：有些查询“0 行”是合法答案，有些则表示目标未达成；
- canonical transaction 若 step 要求成功交易，应检查 transaction/receipt status，而不只是 payload 完整；
- validator/contract 版本受控，未知版本 fail closed 到 LLM Evaluator；
- 任一断言无法机器证明时返回 `unknown`，而不是 pass。

### 6.2 适合第一阶段直通的白名单

- 单行 read-only 聚合/lookup，成功标准只要求返回明确字段；
- canonical transaction lookup，成功标准可直接映射到固定字段存在性、状态和 txid 一致性；
- 单一工具、无错误、无截断、无歧义、完整 provenance；
- 后续 dependency outputs 可用显式 schema 验证。

### 6.3 必须保留 LLM Evaluator 的路径

- LLM-generated StepResult；
- fast-path reject/fallback；
- 多行/Top-N/采样/截断结果，直到有对应 machine validator；
- 自然语言 success criteria 无法编译为确定性断言；
- ambiguity 非空或 structured facts/provenance 不完整；
- tool error、partial、空 summary、未知 result kind/contract version；
- retry、fallback tool、recovery、permission/guardrail 异常；
- 需要跨结果解释、主观判断、业务语义推断或重新规划；
- validator 返回 fail 或 unknown。

## 7. 可减少的 LLM calls、tokens 与 latency

### 7.1 LLM calls

以本 run 严格回放：

- 可候选跳过：**7 次 Step Evaluator LLM calls**；
- 占所有 Evaluator calls：**70%**；
- 占本 run 总 LLM calls（131）：**5.34%**；
- 总 LLM calls：**131 -> 124**；
- 平均每 case LLM calls：**4.679 -> 4.429**，减少 **0.25 call/case**，约 **5.34%**。

这 7 次是对已实现 Executor fast path 的增量收益；不会重复计算此前已经跳过的 7 次 Executor summarization。

### 7.2 latency

直接使用 node trace 实测值：

- 可移除 evaluator node duration 合计：**32,043.388 ms**；
- 平均每个 deterministic step：**4,577.627 ms**；
- 分 case 潜在端到端减少：

| Case | 可跳过次数 | 实测 evaluator latency 节省 |
|---|---:|---:|
| `db_003` | 1 | 1,986.859 ms |
| `multi_001` | 2 | 6,401.787 ms |
| `multi_002` | 2 | 9,370.754 ms |
| `multi_003` | 2 | 14,283.988 ms |
| **合计** | **7** | **32,043.388 ms** |

摊到全部 28 个有效 case，算术平均可减少约 **1,144.407 ms/case**。对 deterministic-hit case，收益更明显。P50/P95 的新值不能由单次 run 严格反事实推出，因为各 case 其余模型节点存在明显长尾；但每个命中步骤的 evaluator 是串行节点，所以表中 case 级节省是合理的一阶估算。

### 7.3 tokens

当前 trace 只记录 request 级总 input/output tokens，没有逐 LLM node token usage；Evaluator 也没有经过 `context_build` 审计事件。因此无法从该产物精确拆出这 7 次 Evaluator 的 token 数，任何“精确 token 节省”都会是伪精确。

可确定的下界/结构事实：

- 每跳过一次都会移除固定 Evaluator system prompt；
- 会移除完整序列化的 PlanStep；
- 会移除 deterministic StepResult。该 StepResult 中 structured facts 同时出现在 evidence、structured_facts 和 dependency_outputs，reference 又同时出现在 result_references/provenance，因此 evaluator prompt 存在重复输入；
- 会移除一次短 JSON decision 输出。

依据当前 payload 结构作容量级估计，7 次调用预计可减少约 **12k–25k input tokens** 和 **0.2k–0.7k output tokens**。范围较宽是因为产物没有保存 evaluator 单节点 usage，且 canonical transaction facts、计划文本长度和 tokenizer 均会影响结果。相对于本 run 的 469,989 input tokens 和 21,270 output tokens，约为：

- input tokens：**2.6%–5.3%**；
- output tokens：**0.9%–3.3%**；
- total tokens：约 **12.2k–25.7k**。

正式上线前应先给每次 model invocation 增加 `node_name/model/input_tokens/output_tokens` 归因，再用 shadow mode 得到精确数字。建议不要用 request 总 token 差值反推 evaluator，因为 Planner、Composer、Reviewer 和 Executor 输出存在运行间波动。

## 8. Correctness、retry、replan、recovery 风险

### 8.1 Correctness

主要风险不是 deterministic StepResult 构造错误，而是把“数据完整”误判为“任务完成”。典型反例：

- SQL 成功返回单行，但 SQL 条件、排序或聚合写错；
- canonical transaction 完整返回，但 txid 不是依赖步骤要求的 tx_hash；
- receipt 是 FAILED，而 step 要求确认成功；
- 必填业务字段为 null；
- 结果来自正确工具，但 success criteria 还要求比较、换算或范围声明；
- adapter 错误地声明 `structured_facts_complete` 或 `eligible`。

缓解方式是：machine validator 只对可证明的 criteria pass；任何 unknown 进入 LLM Evaluator，并保留 provenance/hash 供最终 Composer/Reviewer 使用。

### 8.2 Retry

本 run 的 10 次 Evaluator 都没有 retry，不能据此认为 retry 永远无用。跳过 LLM 后可能漏掉“工具成功但参数语义不对，应该修正参数重试”的情况。

因此：

- validator 必须检查参数与 success criteria/dependency binding；
- validator fail 不应直接 complete；可进入现有 LLM Evaluator，由其选择 retry/fail；
- unresolved tool error、fallback、空结果语义不清时禁止直通；
- 保持现有 max step retry 约束不变。

### 8.3 Replan

LLM Evaluator 可能识别“当前步骤或后续路径不再适用”。现有 predicate 不理解整体计划语义，直接 complete 可能延迟或漏掉 replan。

风险控制：

- 仅对 planner 已声明为单工具、终态、success criteria 全部可机器判定的 step 直通；
- 若结果改变后续路径、产生多个候选、依赖字段缺失或出现业务分支，则返回 unknown 进入 LLM；
- 第一阶段不要对发现型、搜索型、比较型步骤开放直通。

### 8.4 Recovery

本次 `recovery_001/002` 被跳过，没有线上 fault-injection 数据。因此对 recovery 安全性只能基于代码路径和测试，不能基于本 run 给出实证结论。

应明确排除：

- 曾发生 tool retry/fallback/recovered 的 step；
- last_tool_errors 非空，即使之后得到部分结果；
- permission/guardrail refusal；
- status 为 partial/failed；
- contract 或 validator 异常。

这些情况继续交给现有 Evaluator，保持 retry/replan/partial/fail 决策能力。未来必须补跑支持 fault injection 的 recovery evaluation，再考虑是否对“机器可证明已恢复”的窄路径开放 fast path。

### 8.5 模型故障时的行为差异

现有 `evaluate_step()` 在异常时会 fail-open：非空结果被判 `pass`，confidence=0。引入 machine validator 后，deterministic 且可证明的步骤反而能减少对 Evaluator 服务可用性的依赖。但 validator 本身必须 fail-closed：异常或 unknown 应回到 LLM，而不是直接 complete。

## 9. 推荐落地顺序与验收标准

本轮不修改代码。若进入实现阶段，建议：

1. 先增加 evaluator 单节点 token usage 和输入尺寸观测，不改变路由。
2. 实现只读的 machine validator 与 `pass/fail/unknown + reason + checked_predicates` 审计结果，shadow mode 下仍调用 LLM。
3. 对照至少数百个 deterministic steps，统计 machine pass 后 LLM 非-pass 的冲突率，并人工审计全部冲突与抽样 pass。
4. 仅当 machine-pass/LLM-non-pass 冲突为 0，且 recovery/fault-injection、空结果、错误参数、FAILED receipt、依赖不一致测试全部通过，才打开白名单直通。
5. 保留 kill switch、按 result kind/validator version 灰度和采样式 LLM 审计。

建议验收指标：

- deterministic + validator pass 的 LLM Evaluator 跳过率；
- machine/LLM 决策冲突率，尤其 non-pass 漏判率；
- task success、tool argument accuracy、multi-tool dependency accuracy 无回归；
- retry/replan/recovery success 无回归；
- 每 result kind 的 false-pass 数为 0；
- evaluator calls、精确 input/output tokens、节点 duration 按预期下降；
- skipped decision 仍有完整 predicate、validator、contract version、result_id 和 hash 审计链。

## 10. 最终判断

**可以设计并值得设计**：

```text
deterministic StepResult + machine validator pass
  -> direct complete step
  -> skip LLM Step Evaluator

LLM-generated StepResult / ambiguity / error / partial / retry / fallback / recovery / validator unknown-or-fail
  -> keep current LLM Step Evaluator
```

当前 run 为这一方向提供了清晰证据：7 次候选调用全部 pass，未观察到任何 retry/replan/partial/fail，并消耗了 32.043 秒 evaluator latency。但正确的安全边界是“deterministic StepResult **且 success criteria 已被机器逐项证明**”，不是“deterministic StepResult 即自动成功”。在这个边界下，预计本 run 可再减少 7 个 LLM calls、约 12k–25k input tokens、0.2k–0.7k output tokens及 32.043 秒累计串行 latency，同时把需要语义判断和恢复决策的路径完整保留下来。
