# TRON Tool Result 优化后 Multi-tool 性能分析

## 结论

已使用最新 `get_tron_transaction` canonical Tool Result contract 实际运行 `multi_001～multi_003`。本轮结果：

- 3/3 全部成功；
- success rate：100%；
- 0/3 出现 180 秒 timeout；
- 每个 case 均为最小 2 step、2 tool calls、9 LLM calls；
- TRON result summarization 的 dependency evidence 已降到 703～873 input tokens；
- TRON result summarization 的完整 Executor input 为 10,397～10,559 tokens；
- TRON result summarization duration 为 11.41～21.00 秒；
- 总 latency 为 60.13～133.32 秒，P50 为 105.69 秒；
- 总 input tokens 每例约 43.6K～44.2K，说明 canonical raw contract显著缩小了 TRON evidence，但整个 planned pipeline仍有较高固定和重复上下文成本；
- 当前最大 latency 节点已经从 raw-heavy Executor summarization 转移到 Answer Composer。`multi_001` Composer 为 44.22 秒，`multi_003` 为 51.63 秒。

完整 TRON raw没有进入本轮任何 Executor、Evaluator、Composer或Reviewer。后续节点只看到 canonical facts、基于 canonical facts 的 StepResult和自然语言摘要。

本轮没有修改代码，只运行评测并分析现有 trace。

## 评测产物与口径

本轮正式产物：

```text
eval_results/run_20260821T035546Z.json
eval_results/run_20260821T035546Z.md
```

模型：`deepseek-v4-flash`。

评测通过独立启动、确认加载当前代码的 HTTP 服务运行，仅包含 `multi_001～003`。

### Token 观测边界

现有 trace 提供：

- 每个 LLM node 的 start time 和 duration；
- Planner、每次 Executor、Answer Composer、Reviewer 的 ContextBuilder input token估算和分类；
- request summary 的总 input/output tokens；
- Step Evaluator没有 ContextBuilder audit；
- node event没有保存每次 response 的 `usage_metadata`。

因此：

- 每次 Planner/Executor/Composer/Reviewer 的 input tokens可精确报告 ContextBuilder值；
- 每次 Evaluator input tokens不可观测；
- 每次 LLM call 的 output tokens均不可观测；
- 每个 case 的总 input/output tokens可精确报告。

不能把 aggregate output tokens按 duration或调用数平均分配，那会产生虚假精度。下文逐调用 output tokens统一标为 N/A。

## 总体指标

| 指标 | 结果 |
|---|---:|
| Cases | 3 |
| Success rate | 100% |
| Tool selection accuracy | 100% |
| Tool argument accuracy | 100% |
| Permission accuracy | 100% |
| 180s timeout | 0/3 |
| Avg LLM calls | 9.0 |
| Avg tool calls | 2.0 |
| Avg input tokens | 43,848 |
| Avg output tokens | 3,350 |
| Avg total tokens | 47,198 |
| Latency P50 | 105.69s |
| Latency P95 | 130.56s |

### 每个 case

| Case | Steps | Tool calls | LLM calls | Input tokens | Output tokens | Total tokens | Latency | Timeout | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `multi_001` | 2 | 2 | 9 | 43,620 | 3,388 | 47,008 | 105.69s | 否 | success |
| `multi_002` | 2 | 2 | 9 | 44,196 | 2,261 | 46,457 | 60.13s | 否 | success |
| `multi_003` | 2 | 2 | 9 | 43,729 | 4,401 | 48,130 | 133.32s | 否 | success |

## 与 TRON Tool Result 优化前比较

### 可直接比较的基线

优化前最新正式回归中：

- `multi_001`：180.004 秒 timeout，trace被清空；
- `multi_002`：53.88 秒，44,868 input、1,362 output、success；
- `multi_003`：180.002 秒 timeout，trace被清空；
- 正式 success rate：1/3，即33.3%。

另一个优化前、保留完整 trace 的 `multi_001` 运行提供了 TRON summarization直接基线：

- TRON result summarization input：13,086 tokens；
- 其中 dependency evidence：3,636 tokens；
- duration：29.360 秒；
- request aggregate：82,204 input、13,314 output；
- latency：179.95 秒；
- 该次仍有一个重复 TRON step，因此 request总量比较同时包含旧 Planner冗余，不能全部归因于 canonical Tool Result。

### TRON result summarization

| 指标 | 优化前完整 `multi_001` | 优化后 `multi_001` | 变化 |
|---|---:|---:|---:|
| Executor完整input | 13,086 | 10,501 | -2,585，-19.8% |
| dependency evidence | 3,636 | 703 | -2,933，-80.7% |
| duration | 29.360s | 15.681s | -13.679s，-46.6% |

dependency evidence是最能隔离 Tool Result contract影响的指标。它从3,636降到703 tokens，证明完整TRON raw已经被canonical payload替代。

完整Executor input只下降19.8%，因为仍包含固定6,583-token Executor system prompt、1,837-token critical state和1,326-token recent history。canonical result只控制其中的dependency evidence部分。

### 总量和成功率

| Case | 优化前 latency/result | 优化后 latency/result | 优化前 tokens | 优化后 tokens |
|---|---|---|---|---|
| `multi_001` | 180.00s timeout | 105.69s success | timeout trace不可用；完整代理82,204/13,314 | 43,620/3,388 |
| `multi_002` | 53.88s success | 60.13s success | 44,868/1,362 | 44,196/2,261 |
| `multi_003` | 180.00s timeout | 133.32s success | timeout trace不可用 | 43,729/4,401 |

`multi_002` input下降672 tokens，但output增加899 tokens，latency增加约6.26秒。这表明单次端到端latency仍受输出长度和模型时延波动影响；canonical contract不是保证每次请求都更快的缓存机制。

更稳定的结构改善是：

- timeout：`2/3 → 0/3`；
- success rate：`33.3% → 100%`；
- 三例均得到完整trace；
- TRON raw不再进入Agent Graph；
- 每例稳定为2 tools / 9 LLM calls。

## multi_001 完整 LLM Execution Timeline

请求开始：2026-08-21 03:50:47.403 UTC。

| # | LLM call | Start UTC | Duration | Input tokens | Output tokens | 输入上下文主要组成 |
|---:|---|---|---:|---:|---:|---|
| 1 | Planner | 03:50:47.404 | 11.301s | 2,508 | N/A | system 1,178；request/tool catalog 1,332 |
| 2 | Executor step_1 tool-call generation | 03:50:58.709 | 2.336s | 6,960 | N/A | system 6,583；request 58；critical state 323；无dependency/history |
| 3 | Executor step_1 DB result summarization | 03:51:01.066 | 3.875s | 7,422 | N/A | system 6,583；critical state 323；DB dependency evidence 464 |
| 4 | Step Evaluator step_1 | 03:51:04.944 | 4.707s | N/A | N/A | step定义 + DB candidate StepResult |
| 5 | Executor step_2 TRON tool-call generation | 03:51:09.656 | 1.942s | 9,800 | N/A | system 6,583；critical state 1,837；recent history 1,326；dependency仅引用DB StepResult |
| 6 | Executor step_2 TRON result summarization | 03:51:14.163 | 15.681s | 10,501 | N/A | system 6,583；critical state 1,837；recent history 1,326；canonical TRON evidence 703 |
| 7 | Step Evaluator step_2 | 03:51:29.846 | 5.506s | N/A | N/A | step定义 + canonical-based candidate StepResult |
| 8 | Answer Composer | 03:51:35.357 | 44.220s | 10,916 | N/A | system 970；execution summary 4,211；canonical evidence 1,465；Executor draft 4,218 |
| 9 | Reviewer | 03:52:19.579 | 13.501s | 7,534 | N/A | system 167；request 58；answer + execution summary 7,313 |

总 usage：43,620 input / 3,388 output。

### 工具完成边界

- 第二个工具节点开始：03:51:11.600；
- TRON工具节点结束：约03:51:14.161；
- 从请求开始到全部工具完成：约26.76秒；
- 全部工具完成到请求结束：约78.93秒；
- post-tool阶段占总服务端时间约74.7%。

这里没有timeout，因此“工具完成后到timeout”为不适用。对应可比较量是“工具完成后到成功返回”：78.93秒。

## multi_002 完整 LLM Execution Timeline

请求开始约2026-08-21 03:52:33.090 UTC。

| # | LLM call | Start UTC | Duration | Input tokens | Output tokens | 输入上下文主要组成 |
|---:|---|---|---:|---:|---:|---|
| 1 | Planner | 03:52:33.102 | 8.505s | 2,511 | N/A | system 1,178；request/tool catalog 1,335 |
| 2 | Executor step_1 tool-call generation | 03:52:41.613 | 1.556s | 6,944 | N/A | system 6,583；request 61；critical state 304 |
| 3 | Executor step_1 DB result summarization | 03:52:43.188 | 2.494s | 8,178 | N/A | system 6,583；critical state 304；DB evidence 1,236 |
| 4 | Step Evaluator step_1 | 03:52:45.685 | 3.182s | N/A | N/A | step定义 + DB candidate StepResult |
| 5 | Executor step_2 TRON tool-call generation | 03:52:48.872 | 1.747s | 9,526 | N/A | system 6,583；critical state 2,000；recent history 886 |
| 6 | Executor step_2 TRON result summarization | 03:52:53.241 | 11.407s | 10,397 | N/A | system 6,583；critical state 2,000；history 886；canonical TRON evidence 873 |
| 7 | Step Evaluator step_2 | 03:53:04.652 | 4.299s | N/A | N/A | step定义 + canonical-based candidate StepResult |
| 8 | Answer Composer | 03:53:08.954 | 14.834s | 11,177 | N/A | system 970；execution summary 4,342；canonical evidence 1,461；draft 4,349 |
| 9 | Reviewer | 03:53:23.789 | 9.429s | 6,412 | N/A | system 201；request 61；answer + execution summary 6,154 |

总 usage：44,196 input / 2,261 output。

### 工具完成边界

- TRON工具节点结束：约03:52:53.239；
- 从请求开始到全部工具完成：约20.15秒；
- 工具完成到成功返回：约39.98秒；
- post-tool阶段占总服务端时间约66.5%。

## multi_003 完整 LLM Execution Timeline

请求开始：2026-08-21 03:53:33.233 UTC。

| # | LLM call | Start UTC | Duration | Input tokens | Output tokens | 输入上下文主要组成 |
|---:|---|---|---:|---:|---:|---|
| 1 | Planner | 03:53:33.234 | 21.794s | 2,507 | N/A | system 1,178；request/tool catalog 1,331 |
| 2 | Executor step_1 tool-call generation | 03:53:55.035 | 2.041s | 7,006 | N/A | system 6,583；request 57；critical state 370 |
| 3 | Executor step_1 DB result summarization | 03:53:57.096 | 6.088s | 7,382 | N/A | system 6,583；critical state 370；DB evidence 378 |
| 4 | Step Evaluator step_1 | 03:54:03.186 | 2.798s | N/A | N/A | step定义 + DB candidate StepResult |
| 5 | Executor step_2 TRON tool-call generation | 03:54:05.990 | 1.736s | 9,854 | N/A | system 6,583；critical state 1,879；recent history 1,339 |
| 6 | Executor step_2 TRON result summarization | 03:54:10.597 | 20.996s | 10,559 | N/A | system 6,583；critical state 1,879；history 1,339；canonical TRON evidence 707 |
| 7 | Step Evaluator step_2 | 03:54:31.598 | 3.467s | N/A | N/A | step定义 + canonical-based candidate StepResult |
| 8 | Answer Composer | 03:54:35.069 | 51.631s | 11,230 | N/A | system 970；execution summary 4,359；canonical evidence 1,484；draft 4,366 |
| 9 | Reviewer | 03:55:26.702 | 19.840s | 8,199 | N/A | system 167；request 57；answer + execution summary 7,979 |

总 usage：43,729 input / 4,401 output。

### 工具完成边界

- TRON工具节点结束：约03:54:10.595；
- 从请求开始到全部工具完成：约37.36秒；
- 工具完成到成功返回：约95.96秒；
- post-tool阶段占总服务端时间约72.0%。

## TRON Result Summarization 专项比较

| Case | Full Executor input | Canonical dependency evidence | Duration | 占总latency |
|---|---:|---:|---:|---:|
| `multi_001` | 10,501 | 703 | 15.681s | 14.8% |
| `multi_002` | 10,397 | 873 | 11.407s | 19.0% |
| `multi_003` | 10,559 | 707 | 20.996s | 15.7% |

canonical evidence本身已经很小。完整Executor input仍约10.5K的组成是：

```text
固定Executor system prompt：6,583
critical state：1,837～2,000
recent history：886～1,339
canonical dependency evidence：703～873
request/constraints：约60
```

所以继续压缩TRON payload只能影响约7%～8%的该次Executor input；更大的成本来自固定system prompt和前序StepResult/history。

## 最大的3个LLM latency节点

### 每个case

`multi_001`：

1. Answer Composer：44.220s；
2. TRON result summarization：15.681s；
3. Reviewer：13.501s。

`multi_002`：

1. Answer Composer：14.834s；
2. TRON result summarization：11.407s；
3. Reviewer：9.429s。

`multi_003`：

1. Answer Composer：51.631s；
2. Planner：21.794s；
3. TRON result summarization：20.996s；

Reviewer为19.840秒，紧随其后。

### 全部case总体Top 3

1. `multi_003` Answer Composer：51.631s；
2. `multi_001` Answer Composer：44.220s；
3. `multi_003` Planner：21.794s。

若只看工具完成后的节点，总体第三名是`multi_003` TRON result summarization：20.996秒。

## 工具完成后还消耗多少时间

| Case | 工具全部完成累计耗时 | 工具完成后到成功返回 | 到timeout | Post-tool占比 |
|---|---:|---:|---|---:|
| `multi_001` | 26.76s | 78.93s | 不适用，未timeout | 74.7% |
| `multi_002` | 20.15s | 39.98s | 不适用，未timeout | 66.5% |
| `multi_003` | 37.36s | 95.96s | 不适用，未timeout | 72.0% |

虽然不再timeout，主要耗时仍发生在工具完成后。canonical contract解决了raw context问题，但planned质量链的串行结构仍是总latency主体。

## TRON raw是否被重复送入各节点

### Executor

否。

`get_tron_transaction`完整raw先写入Tool Result Store，ToolMessage.content固定为canonical JSON。本轮TRON summarization dependency evidence只有703～873 tokens，且内容为：

- result_id；
- txid；
- transaction/receipt status；
- block/time；
- fee；
- contract type；
- owner/contract/relevant addresses；
- bounded transfer summaries；
- log/internal counts；
- 少量energy/error字段。

不包含完整transaction、receipt、logs、internal transactions、raw_data_hex或signature。

### Step Evaluator

否。

Evaluator看到candidate StepResult：Executor summary、canonical evidence、canonical structured facts和result reference。它不再收到raw前2K，因为StepResult evidence的来源ToolMessage本身已经canonical化。

### Answer Composer

否。

Composer仍同时接收execution summary、canonical evidence summary和Executor draft，因此同一canonical事实仍有语义重复，但不存在完整TRON raw重复。

### Reviewer

否。

Reviewer接收answer draft和execution summary，两者均只基于canonical facts。

### 审计raw

完整raw只在Tool Result Store中，通过result_id和raw_result_location追溯。Agent Graph没有默认read(result_id)路径。

## structured facts/context summary为何仍未把总上下文降得更低

这次压缩已经充分解决“raw进入Graph”，但没有解决整个planned pipeline的其他重复层。

### 1. Executor system prompt固定占6,583 tokens

四次Executor调用每次都重新发送这部分。仅这一项ContextBuilder估算累计约26,332 tokens。canonical TRON evidence每例只有约700～870 tokens，已经不是主项。

### 2. StepResult进入critical state和recent history

TRON tool-call generation和summarization仍携带：

- 1.8K～2.0K critical state；
- 0.9K～1.3K recent history。

这些内容来自数据库StepResult、执行摘要和前序对话，不是raw。

### 3. Executor仍生成长自然语言summary

canonical facts虽短，Executor会把它扩展成报告式StepResult summary。这个summary随后进入Evaluator、execution summary、Composer draft和Reviewer上下文。

### 4. Composer三路重复canonical语义

Composer输入仍包括：

- execution summary：约4.2K～4.4K；
- evidence：约1.46K～1.48K；
- draft：约4.2K～4.4K。

三者不含raw，但大量描述相同数据库/链上事实。Composer总input达到10.9K～11.2K，并成为最大latency节点。

### 5. Reviewer重复answer和execution summary

Reviewer input为6.4K～8.2K。`multi_003`输出较长，Reviewer critical state达到7,979 tokens，duration达19.84秒。

### 6. Output tokens仍主导波动

三例input非常接近：43.6K～44.2K；latency却从60.13秒到133.32秒。明显差异是output：

- `multi_002`：2,261；
- `multi_001`：3,388；
- `multi_003`：4,401。

`multi_003`同时有更慢Planner、Composer和Reviewer。decode长度和模型端时延仍显著影响总latency。

### 7. Rolling summary不会触发

最大单节点input只有约11.2K，远低于96K input budget和约90% trigger。当前机制优化context overflow，不会为latency主动缩短10K级调用。

## 哪些LLM call语义必要

### 必要或有明确价值

| Call | 判断 | 原因 |
|---|---|---|
| Planner | 有价值 | 需要建立数据库结果到TRON txid的动态依赖；但当前固定两步任务可考虑更轻量规划机制 |
| Executor step_1 tool-call generation | 必要 | 生成目标SQL并选择数据库字段 |
| Executor step_2 tool-call generation | 必要 | 从dependency提取tx_hash并生成TRON调用 |
| Answer Composer | 必要 | 用户要求最终比较、来源与覆盖说明；应保留一次报告式生成 |

### 存在合并、跳过或条件执行空间

| Call | 空间 | 节点证据 |
|---|---|---|
| Executor step_1 DB summarization | 可结构化短路 | DB structured facts已可直接构造最小StepResult；当前仍增加2.49～6.09秒 |
| Step Evaluator step_1 | 可确定性判定 | row_count、required field、tx_hash格式均可规则检查；当前2.80～4.71秒 |
| Executor TRON summarization | 可缩短或与Composer合并 | canonical facts已是任务相关摘要，仍花11.41～21.00秒扩写一次 |
| Step Evaluator step_2 | 可条件执行 | status/txid/block/result_id齐全时可规则pass；当前3.47～5.51秒 |
| Reviewer | 可条件执行 | 三例均只读、工具成功、Evaluator pass；仍花9.43～19.84秒 |

### 最大合并空间

当前post-tool链：

```text
canonical TRON facts
→ Executor长摘要
→ LLM Step Evaluator
→ Answer Composer再次生成完整答案
→ Reviewer再次审查
```

语义最小链可以是：

```text
canonical facts
→ deterministic success checks
→ Answer Composer一次报告式生成
→ 仅风险/冲突/低置信度时Reviewer
```

本轮没有实施这些改变，只分析空间。

## 本轮问题逐项回答

### 1. Executor TRON result summarization input tokens

- `multi_001`：10,501；其中canonical dependency evidence 703；
- `multi_002`：10,397；其中canonical dependency evidence 873；
- `multi_003`：10,559；其中canonical dependency evidence 707。

对比优化前完整`multi_001`：13,086 / dependency 3,636，分别下降19.8%和80.7%。

### 2. TRON result summarization duration

- `multi_001`：15.681秒；
- `multi_002`：11.407秒；
- `multi_003`：20.996秒。

对比优化前完整`multi_001`的29.360秒，下降46.6%。

### 3. 总input/output tokens

- `multi_001`：43,620 / 3,388；
- `multi_002`：44,196 / 2,261；
- `multi_003`：43,729 / 4,401。

### 4. 总latency

- `multi_001`：105.69秒；
- `multi_002`：60.13秒；
- `multi_003`：133.32秒。

### 5. 是否仍有180秒timeout

没有。0/3 timeout。

### 6. Success rate

100%，3/3 success。

### 工具全部完成时累计耗时

- `multi_001`：26.76秒；
- `multi_002`：20.15秒；
- `multi_003`：37.36秒。

### 工具完成后到timeout消耗多少

三例都未timeout，因此该值不适用。工具完成后到成功返回分别为：

- `multi_001`：78.93秒；
- `multi_002`：39.98秒；
- `multi_003`：95.96秒。

### 最大三个LLM latency节点

总体：

1. `multi_003` Answer Composer：51.631秒；
2. `multi_001` Answer Composer：44.220秒；
3. `multi_003` Planner：21.794秒。

### TRON raw是否仍被重复发送

否。Executor、Evaluator、Composer和Reviewer均未收到完整raw。它只存在Tool Result Store中。后续仍有canonical语义的重复表示，但已不是raw JSON、receipt/log/internal/signature重复。

### 压缩为什么没有把总tokens降到更低

因为TRON canonical evidence只占每次TRON Executor约700～870 tokens；固定6,583-token Executor system prompt、StepResult/history、Composer的execution summary + evidence + draft以及Reviewer的answer + execution summary成为主要成本。压缩已经解决raw层，尚未解决LLM质量链的语义重复和长output。

### 哪些调用可优化

两次tool-call generation和一次最终Composer语义必要；DB/TRON result summarization可缩短或结构化，两个Step Evaluator可在成功条件明确时规则化，Reviewer可按风险/冲突条件执行。

## 最终判断

最新TRON Tool Result contract达成了预期性能目标的第一部分：完整raw退出Agent Graph，TRON dependency evidence下降80.7%，同case TRON summarization duration下降46.6%，三例从上一正式回归的2个timeout改善为0个timeout，success rate达到100%。

但总latency仍为60～133秒，且66%～75%的时间发生在工具完成之后。当前主瓶颈已经不是TRON raw，而是：

1. Answer Composer长输入与长输出；
2. planned路径串行的Executor summary、Evaluator、Composer、Reviewer；
3. canonical事实通过StepResult、execution summary、evidence和draft多路语义重复；
4. 固定Executor system prompt在四次Executor调用中重复；
5. output token和模型端时延波动。

因此，canonical Tool Result优化是有效且可量化的，但下一轮性能工作应聚焦post-tool LLM调用合并/条件化和canonical facts到最终答案之间的单一证据视图，而不是继续压缩已经只有约700～870 tokens的TRON payload。
