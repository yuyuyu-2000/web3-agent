# Answer Composer 输入上下文重复分析

## 1. 分析范围与结论

本报告只基于现有代码、最新完整 trace 和已有 Evaluation 产物进行分析：

- 最新完整 run：`eval_results/run_20260821T130800Z.json`
- 未修改任何代码；
- 未重新运行 Evaluation；
- 未实施任何上下文裁剪或 Composer 优化。

核心结论：Answer Composer 当前输入存在显著重复，最严重的问题是 `execution_summary` 在初次 Composer 调用中被近乎完整输入两遍；与此同时，`execution_summary.step_results`、独立 `evidence` 和理论上的 Executor draft 又在事实层重复表达相同工具结果。

最新 trace 中 20 次初次 Composer context 共包含 49,795 tokens：

| 类别 | Tokens | 占初次 Composer 输入 |
|---|---:|---:|
| `critical_state` / execution_summary | 11,732 | 23.6% |
| `evidence` | 11,959 | 24.0% |
| `draft` | 11,872 | 23.8% |
| system prompt | 13,595 | 27.3% |
| current request | 757 | 1.5% |

其中初次调用的 `draft` 实际不是 Executor draft，而是刚刚追加的 synthetic execution summary。因此，仅删除这份完全重复的伪 draft，理论上即可减少约 11,872 input tokens，即初次 Composer 输入的约 23.8%。进一步合并 `execution_summary` 与 `evidence` 中的重复 facts/reference，保守估计还可减少约 3,000–6,000 input tokens。

合理的总空间约为：

- 14.9k–17.9k input tokens/run；
- 初次 Composer 输入减少约 30%–36%；
- Composer 累计 latency 预计减少约 15–45 秒；
- 相对本 run Composer 累计 duration 约减少 3%–10%。

以上是基于现有 trace 的容量级估算，不应理解为已实测优化结果。

## 2. 当前 Answer Composer 输入组装流程

Composer graph node 当前首先复制 state messages，然后追加 synthetic AIMessage：

```text
结构化任务执行摘要：
{_execution_summary(state)}
```

随后构造以下输入：

- `current_request`：最后一条用户问题；
- `execution_summary`：再次调用 `_execution_summary(current)`；
- `evidence`：本轮最后一条用户消息之后的全部 ToolMessage；
- `draft`：从 messages 反向查找最新一条没有 tool calls 的 AIMessage；
- memory 和 conversation summary；
- Answer Composer system prompt。

ContextBuilder 最终按以下类别组装：

```text
system
memory
summary constraints
conversation summary
current_request
critical_state = execution_summary
evidence
draft
```

`execution_summary` 的结构是：

```json
{
  "execution_mode": "...",
  "route_reason": "...",
  "plan": "完整计划",
  "step_results": "全部 StepResult",
  "status": "...",
  "failure_reason": "..."
}
```

每个 `StepResult` 又可能包含：

- status；
- summary；
- evidence；
- structured facts；
- dependency outputs；
- result references；
- provenance；
- tool calls；
- error。

独立 `evidence` 则把 ToolMessage 转换成：

```text
工具证据 <tool_name>：
<tool_message_for_context 处理后的工具内容>
```

这使 Composer 同时接收执行摘要、StepResult 内部证据和原始/压缩 ToolMessage 证据。

## 3. execution_summary 与 draft 的重复

### 3.1 初次 Composer 调用中的实际行为

代码追加 synthetic execution summary AIMessage 后，立即执行：

```text
从 messages 末尾向前查找最新一条：
- 是 AIMessage；
- 没有 tool calls；
- content 非空。
```

刚追加的 synthetic execution summary 正好满足这些条件。因此：

```text
critical_state = _execution_summary(state)

draft =
  "结构化任务执行摘要：\n"
  + _execution_summary(state)
```

两者只有标题和少量 message wrapper 差异，语义内容近乎完全一致。

### 3.2 真实 Executor draft 被遮蔽

如果 Executor 曾生成自然语言步骤摘要，它位于 synthetic summary AIMessage 之前。反向搜索在 synthetic message 处已经命中，不会继续寻找此前真正的 Executor 输出。

因此初次 Composer 实际并没有获得设计意义上的“原始回答草稿”，而是获得 execution summary 的第二个副本。

这带来两个问题：

1. token 浪费：同一完整 JSON 输入两遍；
2. 信息损失：真实 Executor 的表达、限制说明或自然语言总结没有以 draft 身份进入 Composer。

### 3.3 Trace 证据

最新 run 的初次 Composer context 中：

| Case | critical_state | draft | 关系 |
|---|---:|---:|---|
| `direct_003` | 1,297 | 1,304 | 几乎完全相等 |
| `multi_001` | 4,443 | 4,450 | 几乎完全相等 |
| `multi_002` | 4,827 | 4,834 | 几乎完全相等 |
| 多数普通 case | critical + 7 左右 | draft | 仅标题/token wrapper 差异 |

这正好对应“`结构化任务执行摘要` 标题 + 同一 execution summary”的实现行为。

### 3.4 可安全处理方式

初次 Composer 调用中的 synthetic draft 可以安全删除，因为：

- 完整内容已存在于 protected `critical_state`；
- 删除不会损失 plan、step results、status 或 failure reason；
- 不会损失 provenance 或 evidence；
- 不会改变执行、Evaluator、Reviewer 或 recovery 路由；
- Composer fallback 可单独选择真正的 Executor draft或已有回答，不需要依赖 synthetic summary。

仅这一项的确定性 token 空间为约 11,872 input tokens/run。

## 4. execution_summary 与 evidence 的重复

### 4.1 重复链路

同一工具事实通常沿以下路径重复进入 Composer：

```text
ToolMessage structured/context facts
  ├─ 独立 evidence 类别
  └─ StepResult
       ├─ evidence
       ├─ structured_facts
       ├─ dependency_outputs
       ├─ result_references
       └─ provenance
            └─ execution_summary.step_results
```

因此交易哈希、金额、状态、区块号、row count、result_id、工具名和 evidence source 等字段可能出现多次。

### 4.2 普通 LLM-generated StepResult

普通路径中：

- Executor LLM 生成自然语言 summary；
- `complete_step_node` 把 Executor summary 写入 `StepResult.summary`；
- ToolMessage 的前 2,000 字符又写入 `StepResult.evidence`；
- ToolResult metadata 的 facts 写入 `StepResult.structured_facts`；
- ToolMessage 本身又作为 Composer 独立 evidence 输入。

因此 Composer 可能同时看到：

1. Executor 对工具结果的自然语言总结；
2. StepResult evidence 中的工具文本预览；
3. StepResult structured facts；
4. 独立 ToolMessage evidence。

四者粒度不同，但关键事实高度重合。

### 4.3 Deterministic StepResult

deterministic StepResult 的重复更加机械化。构造时同一 structured facts 会进入：

- `evidence` 中的 JSON payload；
- `structured_facts`；
- `dependency_outputs[step_id]`。

同一个 reference 又同时进入：

- `result_references`；
- `provenance`。

随后 ToolMessage evidence 再输入一份工具内容。

这些重复对执行阶段可能有用途：

- `dependency_outputs` 服务下一步参数绑定；
- `result_references` 服务结果追踪；
- `provenance` 服务来源审计；
- ToolMessage 服务原始消息链。

但 Composer 不需要原样接收全部执行期表示。最终回答阶段只需要一份 canonical facts、一份最小 provenance 和必要的未结构化补充。

### 4.4 安全删除边界

可以从 Composer 专用输入中删除或合并：

- 与 `structured_facts` 完全相同的 `dependency_outputs[step_id]`；
- deterministic evidence payload 内再次嵌入的 structured facts；
- 当 `result_references == provenance` 时的其中一份；
- ToolMessage evidence 中已经完整存在于 canonical facts 的重复字段；
- execution-only 的内部绑定结构和重复 plan metadata。

不应删除：

- result_id；
- evidence source/evidence level；
- content hash 或其他必要审计标识；
- raw result reference；
- structured facts 未覆盖的文本说明；
- 图表路径、公开资料链接、完整地址或哈希；
- truncation、ambiguity、error、partial/failure 状态；
- 影响最终回答范围声明的数据库覆盖限制。

## 5. Executor draft 与另外两类输入的重叠

### 5.1 当前实际状态

初次 Composer 调用中，真实 Executor draft 被 synthetic summary 遮蔽。因此当前不是三份内容都成功进入，而是：

```text
execution_summary × 2
+ evidence
```

不过，如果只修复 draft 选择顺序并恢复真实 Executor draft，又会产生新的重复。

### 5.2 恢复真实 draft 后的潜在重复

普通 Planned 路径中，Executor draft 通常已经被复制到：

```text
Executor natural-language output
  -> StepResult.summary
  -> execution_summary.step_results[].summary
```

如果 Composer 再单独输入原始 Executor draft，则相同自然语言 summary 会输入两次。

此外 Executor draft 已经总结 ToolMessage；独立 evidence 又输入原始事实，所以仍存在摘要与证据之间的语义重叠。

### 5.3 不同执行模式应采用不同策略

#### Direct 模式

Direct 模式没有 Planned StepResult 作为核心结果层。建议语义上以真实 Direct/Executor draft 为主：

- 保留真实 draft；
- 保留必要 ToolMessage evidence；
- execution summary 仅保留 status、failure reason、route boundary 等控制信息；
- 不需要完整 plan/step-results 结构。

#### Planned + LLM-generated StepResult

Executor draft 与 `StepResult.summary` 二选一：

- 若保留真实 Executor draft，则 execution summary 不再重复完整 summary；
- 若以 StepResult 为 canonical 输入，则无需另传同一 Executor draft；
- ToolMessage 只补充 summary/structured facts 没有覆盖的信息。

#### Planned + deterministic StepResult

没有 Executor summarization draft。建议：

- 使用 canonical structured facts；
- 使用最小 provenance；
- 使用 plan objective/success criteria；
- 不构造 synthetic draft；
- ToolMessage 只作为 structured facts 缺失内容的补充。

## 6. 推荐的 Canonical Composer 输入

不建议直接把完整 AgentState、完整 StepResult 和 ToolMessage 并排输入。可以为 Composer 构造专用 canonical representation：

```json
{
  "request": "用户问题",
  "execution": {
    "mode": "planned",
    "status": "completed",
    "failure_reason": null
  },
  "plan": {
    "goal": "...",
    "steps": [
      {
        "step_id": "step_1",
        "objective": "...",
        "success_criteria": "...",
        "status": "success"
      }
    ]
  },
  "results": [
    {
      "step_id": "step_1",
      "summary": "...",
      "tool": "postgres_select",
      "facts": {},
      "source": "company_database",
      "result_id": "...",
      "content_sha256": "...",
      "raw_result_location": "...",
      "limitations": []
    }
  ],
  "unstructured_evidence_not_in_facts": [],
  "draft": "仅在确有独立草稿时提供"
}
```

关键原则：

- 一个事实只出现一次；
- 一个 reference 只出现一次；
- dependency outputs 不作为 Composer 展示数据重复传入；
- draft 必须是独立的自然语言草稿，而不是 execution summary 的副本；
- 无独立 draft 时省略 draft；
- ToolMessage 只保留 canonical facts 没有覆盖的内容。

## 7. 最新 Trace Token 统计

### 7.1 初次 Composer 调用

最终 run 中有 20 个 case 产生初次 Composer context event：

| 类别 | Tokens |
|---|---:|
| 总计 | 49,795 |
| system | 13,595 |
| current request | 757 |
| execution summary / critical state | 11,732 |
| evidence | 11,959 |
| draft | 11,872 |

execution summary、evidence、draft 三类合计：

```text
11,732 + 11,959 + 11,872 = 35,563 tokens
```

占初次 Composer context 的约 71.4%。其中至少 11,872 tokens 属于可明确识别的完整重复。

### 7.2 包含 Reviewer 修订的全部 Composer 调用

最新 run 共记录 29 次 Answer Composer context build：

| 类别 | Tokens |
|---|---:|
| 总计 | 74,395 |
| critical state | 17,070 |
| evidence | 20,369 |
| draft | 13,700 |

后续修订调用中的 draft 不一定仍是 execution summary：它可能包含 Reviewer feedback 或前一版回答。因此不能把全部 13,700 draft tokens 都直接删除。

最安全、可直接归因的节省口径是初次调用中的 11,872 tokens。

### 7.3 重型 case

重复在 multi-tool case 中最明显：

| Case | execution summary | evidence | draft | 总 context |
|---|---:|---:|---:|---:|
| `multi_001` | 4,443 | 1,468 | 4,450 | 11,383 |
| `multi_002` 初次 | 4,827 | 1,507 | 4,834 | 12,193 |

删除 synthetic draft 后：

- `multi_001` 可直接减少约 4,450 tokens，约占该次 context 的 39.1%；
- `multi_002` 可直接减少约 4,834 tokens，约占该次 context 的 39.6%。

如果进一步 canonicalize execution summary 与 evidence，重型 multi-tool case 仍有额外压缩空间。

## 8. Token 节省估算

### 8.1 确定性空间

删除初次 Composer 中重复的 synthetic draft：

- 约 11,872 input tokens/run；
- 初次 Composer context 减少约 23.8%；
- 平均约 594 tokens/初次 Composer case；
- 重型 multi-tool case 可减少约 4.4k–4.8k tokens/call。

### 8.2 execution_summary/evidence 合并空间

现有 trace 只记录 category tokens，没有逐字段 token attribution，因此无法精确区分逐字重复、JSON wrapper 和语义重复。

基于以下重复结构：

- StepResult evidence 与 ToolMessage；
- structured facts 与 dependency outputs；
- result references 与 provenance；
- Executor summary 与 StepResult summary；
- tool args/result_id/source 的多次出现；

保守估计还可减少约 3,000–6,000 input tokens/run。

### 8.3 合计

| 优化层 | 估计节省 |
|---|---:|
| 删除重复 synthetic draft | 约 11.9k input tokens |
| 合并 execution summary 与 evidence | 约 3k–6k input tokens |
| 合计 | 约 14.9k–17.9k input tokens |

相对于初次 Composer 的 49,795 tokens，约为 30%–36%。

该估算没有包括进一步缩短 system prompt、conversation summary 或 Reviewer 输入的空间。

## 9. Latency 空间估算

最新完整 run 的 Composer 指标：

- 调用次数：29；
- 平均 duration：15,834.761 ms；
- 累计 duration：约 459.2 秒。

输入 token 减少与总 latency 不严格线性：

- 模型输出生成通常占据大量时间；
- provider 排队、模型长尾和网络时间不会随输入同比下降；
- 较短 prompt 主要降低 prefill、首 token latency 和上下文处理成本；
- 重型 case 比短 context case 更可能获得明显收益。

合理的一阶估计：

| 范围 | 预计 latency 节省 |
|---|---:|
| 普通 Composer call | 约 0.3–1.5 秒 |
| 重型 multi-tool call | 约 1–5 秒 |
| 全 run 累计 | 约 15–45 秒 |
| 相对 Composer 累计 duration | 约 3%–10% |

这些是容量级估算，不是实测结果。真正实施后需要通过同模型、同数据集、多轮重复 Evaluation 分离模型长尾波动。

## 10. Correctness 风险与安全边界

### 10.1 低风险优化

- 删除初次 Composer 中 execution summary 的第二份完整副本；
- 当 references 与 provenance 完全一致时只保留一份；
- 删除与 structured facts 完全相同的 dependency output 副本；
- 不向 Composer暴露纯执行控制字段；
- 为 Composer 构造 canonical representation，而不修改 AgentState 中的执行数据。

### 10.2 中等风险优化

- 只保留 structured facts、删除 ToolMessage preview；
- 只保留 Executor summary、删除原始工具 evidence；
- 压缩完整 plan；
- 去除重复地址、哈希或金额。

这些操作必须验证 structured facts 是否覆盖：

- 完整链接；
- 图表路径；
- 长文本说明；
- scope limitation；
- error detail；
- 多行结果中的必要样本；
- 最终回答需要引用但结构化层未保留的信息。

### 10.3 不安全做法

- 完全删除 provenance 或 result_id；
- 只保留 Executor draft 而删除全部工具证据；
- 只保留 ToolMessage preview 而删除完整 structured facts；
- 在不知道 structured facts 是否完整时丢弃 raw reference；
- Reviewer 修订时删除 feedback；
- 把 partial、failure、ambiguity 或 database coverage limitation 当作重复信息删除。

## 11. 推荐分析优先级

如果后续进入实施阶段，建议按以下顺序，但本报告不实施这些改动：

1. 修正 synthetic execution summary 被选作 draft 的问题；
2. 明确不同执行模式的 Composer input contract；
3. 建立 canonical step evidence；
4. 对 `StepResult` 做 Composer 专用投影，不直接序列化完整执行结构；
5. 只补充 canonical facts 未覆盖的 ToolMessage 内容；
6. 为 canonicalization 增加字段保留测试；
7. 在 trace 中记录去重前/后 token 和被移除字段；
8. 最后通过多轮 Evaluation 测量 token、首 token latency、总 latency 和 correctness。

## 12. 最终判断

Answer Composer 当前的主要问题不是一般意义上的 prompt 过长，而是数据模型层级重复：

```text
execution_summary
  + execution_summary 伪装成 draft
  + ToolMessage evidence
  + StepResult 内部重复 facts/reference
```

最确定、最安全的空间是删除初次 Composer 中 execution summary 的第二份副本，约减少 11.9k input tokens/run。再通过 canonical evidence 合并，可以把总节省扩大到约 14.9k–17.9k input tokens，约占初次 Composer context 的 30%–36%。

预计 Composer 累计 latency 可减少约 15–45 秒，但由于模型生成和 provider 长尾占比较高，不能把 token 降幅直接等同于 latency 降幅。

真正优化时应遵循：保留一份 canonical facts、一份最小 provenance、一份真正独立的 draft；同一事实和 reference 不应通过 execution summary、evidence、dependency outputs 和 draft 重复输入。
