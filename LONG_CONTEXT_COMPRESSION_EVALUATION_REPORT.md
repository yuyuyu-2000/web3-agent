# 长对话 / Context Compression Evaluation 报告

测试日期：2026-08-22（Asia/Shanghai）  
测试范围：只新增测试和报告；未修改 ContextBuilder、Rolling Summary、Agent graph 业务逻辑或 Evaluation 框架。  
测试文件：`tests/test_long_context_compression_evaluation.py`

## 结果

| 指标 | 30轮 | 50轮 |
| -------------------- | --- | --- |
| Rolling Summary 是否触发 | 是 | 是 |
| 压缩次数 | 53 次成功压缩 | 93 次成功压缩 |
| Key Fact Retention | 通过：USDT、固定 txid、2026-08-06 均保留 | 通过：USDT、固定 txid、2026-08-06 均保留 |
| Constraint Retention | 通过：`只看 USDT`、`amount_usd >= 100000` 均保留 | 通过：`只看 USDT`、`amount_usd >= 100000` 均保留 |
| 最终 Input Tokens | 1106 / 1800 | 1106 / 1800 |
| 最终任务是否成功 | 是，`completed` | 是，`completed` |

## 测试方法与边界

每个场景都编译并运行当前生产 Agent graph，分别使用独立的 `thread_id`：

- 30 轮：`long-context-30-rounds`
- 50 轮：`long-context-50-rounds`

同一场景内的全部轮次严格复用同一个 `thread_id` 和同一个项目 `MemorySaver` Checkpoint。每轮通过 `graph.ainvoke` 提交一条 HumanMessage，走真实 router、direct executor、Answer Composer、Rolling Summary、ContextBuilder、Trace 和 checkpoint 状态合并路径。

第 1 轮预埋：

- 后续分析只看 USDT；
- 只关注 `amount_usd >= 100000`；
- 固定 txid：`a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1`；
- 日期固定为 `2026-08-06`；
- 仅做只读分析。

最后一轮只要求 Agent 根据此前信息列出 Token、金额阈值、txid 和日期，没有在当轮重复这些值。

为避免调用外部模型及其随机性，测试只替换 `ChatOpenAI` 为确定性测试模型。其余压缩触发、压缩边界、summary 状态、ContextBuilder token 计算、上下文组装、Trace 和 Checkpoint 全部是当前项目生产实现。该测试能验证压缩机制以及“摘要事实是否被继续送给最终 LLM 并被使用”；它不能衡量某个真实在线 LLM 自由生成摘要时的概率性遗漏率。

测试环境将 `max_input_tokens` 设为 1800、Rolling Summary trigger ratio 设为 0.10，以便在 30/50 轮内确定性达到阈值。Answer Composer 的固定 protected context 约 1159 tokens，因此 1800 能容纳生产固定提示，同时仍能产生长对话压缩压力。

## 判定依据

### Rolling Summary 是否真的触发

不是根据最终回答推断。必须同时满足：

- `compact_events` 存在 `status=success`、`mode=proactive`、`reason=token_threshold`；
- `conversation_summary` 非空；
- `summary_version > 0`；
- `summarized_until > 0`；
- `len(summarized_message_ids) == summarized_until`；
- Checkpoint 中保留原始完整 `messages`，但 active context 从 `summarized_until` 后开始。

30 轮最终 `summary_version=53`、`summarized_until=80`；50 轮最终 `summary_version=93`、`summarized_until=140`。两者的 `summarized_message_ids` 数量分别为 80 和 140，与压缩边界完全一致。

压缩次数大于用户轮数，是因为一个用户轮次会经过 router、executor、composer 等多个 LLM context build；在本测试较低的 10% 阈值下，同一轮内可能在多个节点继续压缩仍未摘要的安全消息前缀。这是实际 Trace 结果，不是按轮数推算。

### 关键事实与约束保留

Checkpoint 的最终 `conversation_summary` 同时包含：

```json
{
  "current_goal": "持续分析指定日期的大额 USDT 交易",
  "confirmed_user_constraints": [
    "后续分析只看 USDT",
    "只关注 amount_usd >= 100000",
    "日期固定为 2026-08-06"
  ],
  "important_entities": [
    "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
    "USDT"
  ],
  "important_numbers": ["100000", "2026-08-06"],
  "current_plan": {"step": "continue_analysis"},
  "completed_steps": [],
  "pending_steps": ["按既有约束继续任务"],
  "important_tool_findings": [],
  "failed_attempts": [],
  "unresolved_errors": [],
  "permissions_approvals": ["仅只读分析"],
  "clarified_state": {
    "token": "USDT",
    "minimum_amount_usd": 100000,
    "txid": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
    "date": "2026-08-06"
  },
  "decisions_made": ["不分析其他 Token"],
  "open_questions": []
}
```

测试还直接检查最后一次传给 Answer Composer LLM 的实际消息文本，四项值全部存在，证明 ContextBuilder 没有把已保留的摘要约束错误裁剪掉。

30 轮和 50 轮的最终回答均为：

```text
USDT；amount_usd >= 100000；a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1；2026-08-06
```

因此最终 Agent 不仅收到摘要事实，也正确使用了这些事实。

### Token 控制

“最终 Input Tokens”取最后一个 `context_events` 的 `total_tokens`，即 ContextBuilder 真正构造出的最终 Answer Composer 输入，不使用最终答案长度猜测。

- 30 轮：最终 1106，预算 1800，剩余 694；全程最大 1775；预算越界 0 次。
- 50 轮：最终 1106，预算 1800，剩余 694；全程最大 1775；预算越界 0 次。

最终 context category 分布在两个场景中相同：

```json
{
  "system": 325,
  "summary_constraints": 308,
  "current_request": 44,
  "critical_state": 52,
  "evidence": 2,
  "memory": 0,
  "draft": 23,
  "summary": 360
}
```

`summary_constraints` 是 protected context，并且最终 `trimmed=[]`，所以关键摘要约束不是依靠低优先级历史侥幸留下，而是通过项目已有的 summary constraint 通道进入最终上下文。

## Trace 摘要

### 30 轮

```json
{
  "thread_id": "long-context-30-rounds",
  "rounds": 30,
  "checkpoint_message_count": 90,
  "rolling_summary_triggered": true,
  "compact_event_count": 55,
  "successful_compression_count": 53,
  "summary_model_calls": 53,
  "summary_version": 53,
  "summarized_until": 80,
  "summarized_message_ids_count": 80,
  "context_event_count": 90,
  "max_context_tokens_observed": 1775,
  "context_budget_violations": 0,
  "key_fact_retention": true,
  "constraint_retention": true,
  "final_context_contains_all_facts": true,
  "final_input_tokens": 1106,
  "max_input_tokens": 1800,
  "final_status": "completed",
  "final_task_success": true
}
```

首个成功压缩事件：

```json
{
  "type": "rolling_compact",
  "mode": "proactive",
  "status": "success",
  "reason": "token_threshold",
  "summarized_from": 0,
  "summarized_until": 2,
  "summarized_count": 2,
  "active_tokens_before": 433,
  "active_tokens_after": 619,
  "summary_version": 1
}
```

最后一个成功压缩事件：

```json
{
  "type": "rolling_compact",
  "mode": "proactive",
  "status": "success",
  "reason": "token_threshold",
  "summarized_from": 78,
  "summarized_until": 80,
  "summarized_count": 2,
  "active_tokens_before": 344,
  "active_tokens_after": 595,
  "summary_version": 53
}
```

最终 Context Trace：

```json
{
  "type": "context_build",
  "scene": "answer_composer",
  "model": "fake-long-context",
  "model_context_window": 2400,
  "max_input_tokens": 1800,
  "reserved_output_tokens": 300,
  "total_tokens": 1106,
  "category_tokens": {
    "system": 325,
    "summary_constraints": 308,
    "current_request": 44,
    "critical_state": 52,
    "evidence": 2,
    "memory": 0,
    "draft": 23,
    "summary": 360
  },
  "trimmed": [],
  "remaining_input_tokens": 694
}
```

### 50 轮

```json
{
  "thread_id": "long-context-50-rounds",
  "rounds": 50,
  "checkpoint_message_count": 150,
  "rolling_summary_triggered": true,
  "compact_event_count": 95,
  "successful_compression_count": 93,
  "summary_model_calls": 93,
  "summary_version": 93,
  "summarized_until": 140,
  "summarized_message_ids_count": 140,
  "context_event_count": 150,
  "max_context_tokens_observed": 1775,
  "context_budget_violations": 0,
  "key_fact_retention": true,
  "constraint_retention": true,
  "final_context_contains_all_facts": true,
  "final_input_tokens": 1106,
  "max_input_tokens": 1800,
  "final_status": "completed",
  "final_task_success": true
}
```

首个成功压缩事件：

```json
{
  "type": "rolling_compact",
  "mode": "proactive",
  "status": "success",
  "reason": "token_threshold",
  "summarized_from": 0,
  "summarized_until": 2,
  "summarized_count": 2,
  "active_tokens_before": 433,
  "active_tokens_after": 619,
  "summary_version": 1
}
```

最后一个成功压缩事件：

```json
{
  "type": "rolling_compact",
  "mode": "proactive",
  "status": "success",
  "reason": "token_threshold",
  "summarized_from": 138,
  "summarized_until": 140,
  "summarized_count": 2,
  "active_tokens_before": 344,
  "active_tokens_after": 595,
  "summary_version": 93
}
```

最终 Context Trace 与 30 轮相同：`total_tokens=1106`、`max_input_tokens=1800`、`remaining_input_tokens=694`、`trimmed=[]`。

## 失败分类

最终两组测试均通过，因此没有以下最终失败：

- 没触发压缩：否；
- 摘要遗漏关键事实：否；
- 上下文裁剪错误：否；
- 最终 Agent 没正确使用已保留的信息：否。

测试开发期间曾出现两类测试替身/配置问题，均未计入 Agent 缺陷：首次预算 700 小于 Answer Composer 固定 protected context 的 1159 tokens，导致第 1 轮即发生 `ContextBudgetError`；其次假模型的 `usage_metadata` 初版缺少 LangChain 要求的 `total_tokens`。两处仅修正新增测试配置和测试替身，没有修改任何 Agent 代码。

## 测试执行结果

```text
.venv/bin/pytest -q tests/test_long_context_compression_evaluation.py -vv
2 passed in 1.75s

.venv/bin/pytest -q tests/test_long_context_compression_evaluation.py tests/test_rolling_summary.py tests/test_context_builder.py tests/test_trace.py
29 passed in 2.17s
```
