结论先说：

- `multi_001` 的主失败是框架级的 **State Validation** 误判。`tx_hash` 已经进入数据库工具结果的 `structured_facts`，随后也会被写进 `StepResult`；但校验器不识别“无 `0x` 前缀的 64 位 TRON txid”，因此在链上查询前错误阻断。
- `multi_002` 实际任务完整成功，工具链和依赖传递都正确。它被判失败只是 **Evaluator 的 permission 口径与运行时架构冲突**。
- `multi_003` 在客户端 180 秒超时前，后台实际上已经完成数据库查询和 TRON 查询。报告却因为 HTTP adapter 超时后丢弃全部部分轨迹，将其记录成“零工具调用”。主因是 **timeout/性能**，并伴随 **Evaluator/观测数据丢失**。
- 三个 case 没有单一共同的业务执行根因，但存在两个共同框架问题：planned 模式 permission 评分口径错误，以及评测结果没有保存 plan、StepResult、dependency evidence 等关键诊断状态。

以下分析基于 [本次评测报告](/Users/yuyu/Documents/code/Chaincloud-AI-main/eval_results/run_20260819T133827Z.json)、保留下来的 `tool_results` 和当前只读代码。报告没有序列化完整 plan/StepResult；因此下面会明确区分“报告直接可证”和“按 step/tool/decision 轨迹重建”。

## multi_001

### 用户任务与期望路径

任务：

1. `postgres_select` 查询 `public.justlend`，按 `amount_usd DESC LIMIT 1` 取最大事件。
2. 从结果中提取 `tx_hash`。
3. 将该值作为 dependency evidence 和 `get_tron_transaction.txid`。
4. 查询 TRON 交易本体及回执。
5. 分别说明数据库记录与链上结果。

### Planner 实际 plan

报告没有保存 plan JSON，但由 step ID、Evaluator reason 和工具序列可重建为：

| Step | 实际目标 | 依赖 | 建议工具 |
|---|---|---|---|
| `step_1` | 列出数据库表，确认 JustLend 表 | 无 | `postgres_list_tables` |
| `step_2` | 查看 `public.justlend` 表结构 | `step_1` | `postgres_table_schema` |
| `step_3` | 查询 `amount_usd` 最大事件并取得 `tx_hash` | `step_2` | `postgres_select` |
| `step_4` | 使用“该交易”的 hash 查询 TRON 本体和回执 | `step_3` | `get_tron_transaction` |

这比期望路径多了两个不必要的探查步骤。用户已经给出表名和排序字段，而 Planner prompt 本身也要求“已知表名时不要重复调用列举表工具”，见 [planner.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/planning/planner.py:36)。

因此 Planner 存在过度规划，但这不是导致任务中断的直接原因。

### 实际 step / tool、输入和结果

#### step_1

- 工具：`postgres_list_tables`
- 输入：`{}`
- dependency evidence：无
- 工具结果：
  - `tables_count = 8`
  - 包含 `justlend`
- StepResult：
  - `status = success`
  - structured facts：`{"tables_count": 8, "count": 8}`
  - Evaluator：`pass`

#### step_2

- 工具：`postgres_table_schema`
- 输入：`{"table_name":"public.justlend"}`
- dependency evidence：`step_1` 的成功结果，证明 `justlend` 存在
- 工具结果：
  - `columns_count = 17`
  - 字段包含 `tx_hash`、`amount_usd`、`operation_type` 等
  - 返回 3 条 sample rows
- StepResult：
  - `status = success`
  - structured facts 包含表名、字段列表、sample rows
  - Evaluator：`pass`

#### step_3

- 工具：`postgres_select`
- 输入：

```sql
SELECT
    day,
    occurred,
    ingested_at,
    tx_seq,
    market_event_id,
    event_index,
    tx_hash,
    from_address,
    to_address,
    market_id,
    operation_type,
    token_symbol,
    token_address,
    amount,
    price_usd,
    amount_usd,
    updated_at
FROM public.justlend
ORDER BY amount_usd DESC
LIMIT 1
```

- dependency evidence：`step_2` 表结构结果
- structured facts：
  - `row_count = 1`
  - `tx_hash = 742A2581F3ABB84FD1531B7A2CD5D0E5EFBD416D38477B14540FA729B421165E`
  - `amount_usd = 50000000`
  - `amount = 50000000`
  - `operation_type = stake`
  - `token_symbol = USDT`
  - `occurred = 2026-08-01 17:32:09 +0800`
- StepResult：
  - `status = success`
  - evidence 包含工具原始结果预览
  - structured facts 包含上述完整 sample
  - result reference 指向 result ID `2203ddd2d1784cdc8c7bf736cc8fc273`
  - Evaluator 明确给出：`可继续使用该 tx_hash 进入下一步`

这证明 `tx_hash` 并没有在 Tool Result → StepResult 阶段丢失。`complete_step_node` 会把 tool metadata 的 `structured_facts` 和 result reference 写入 StepResult，见 [graph.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/graph.py:933)。

#### step_4

- 预期输入：

```json
{
  "txid": "742A2581F3ABB84FD1531B7A2CD5D0E5EFBD416D38477B14540FA729B421165E"
}
```

- 实际：
  - Permission Gate：`allow`
  - State Validation：`MISSING`
  - `get_tron_transaction` 未被调用
  - 最终状态：`blocked_missing_state`
  - 最终回复退化成 `step_3` 摘要，并提示“下一步”再查链上

### dependency propagation 专项判断

数据库结果中的 `tx_hash`：

1. 已存在于工具原始返回；
2. 已存在于 `tool_result_records.structured_facts.sample[0].tx_hash`；
3. 按 `complete_step_node` 必然被写入 `step_3 StepResult.structured_facts`；
4. `state_validation_node` 把全部 `step_results` 传入校验器，见 [graph.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/graph.py:729)；
5. Executor prompt 也会将依赖步骤的完整 StepResult 序列化为“依赖步骤结果”，见 [graph.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/graph.py:144)。

所以这里不是“字段没有写入 StepResult”，而是“写入后，校验器没有识别”。

另有一个次级设计问题：Context Builder 名为 `dependency_evidence` 的消息列表实际上只取当前 step 开始后的消息，排除了前序 step 的 ToolMessage，见 [graph.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/graph.py:789)。目前依赖仍通过 `critical_state` 中的 StepResult JSON 传播，因此本 case 没因此丢 hash，但两条证据通道语义不一致。

### first divergence point

`step_4` 的 State Validation。

校验器先因为 step objective/success criteria 包含“该交易”而判定需要目标 identifier；随后 `_IDENTIFIER_RE` 只接受：

- `0x` 开头的 40–64 位十六进制串；
- TRON Base58 `T...` 地址。

它不接受裸 64 位十六进制 txid，见 [state_validation.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/agent/state_validation.py:16)。

结果中明明存在合法 TRON txid，却被判成 `target_identifier` 缺失。

### 分类与范围

- 主分类：**State Validation**
- 次分类：
  - **Planner**：多做 list/schema 探查
  - **Evaluator**：permission check 将正常只读 `allow` 视为失败
  - **dependency propagation**：命名为 dependency evidence 的独立通道没有真正携带前序 ToolMessage，但 StepResult 通道正常
- 范围：主问题是**框架级**，任何无 `0x` 的 64 位 txid 依赖步骤都可能触发。

---

## multi_002

### 用户任务与期望路径

1. 从 `croas_chain` 取一条非空 `deposit_tx_hash`。
2. 把该字段作为 `get_tron_transaction.txid`。
3. 根据 TRON RPC 实际返回说明存在、查不到或不是 TRON 交易。
4. 不从 `deposit_chain_id` 猜测链名。

### Planner 实际 plan

由完整 step/tool 轨迹可重建为两步：

| Step | 目标 | 依赖 | 工具 |
|---|---|---|---|
| `step_1` | 查询一条非空 `deposit_tx_hash` 及辅助数据库字段 | 无 | `postgres_select` |
| `step_2` | 使用上一步 hash 调用 TRON 查询并核验 | `step_1` | `get_tron_transaction` |

这是符合期望的最小执行 plan。

### 实际 step / tool、输入和结果

#### step_1

- 工具：`postgres_select`
- 输入：

```sql
SELECT deposit_tx_hash, deposit_chain_id, protocol_order_id, deposit_chain_time
FROM public.croas_chain
WHERE deposit_tx_hash IS NOT NULL AND deposit_tx_hash <> ''
LIMIT 1
```

- dependency evidence：无
- structured facts：
  - `row_count = 1`
  - `deposit_tx_hash = 9faa13f61f59bbcba67bc0736460278b7900ab1110b1d699defe417957a568bf`
  - `deposit_chain_id = 32004`
  - `deposit_chain_time = 2026-07-16T23:12:12+08:00`
- StepResult：
  - `status = success`
  - evidence 和 structured facts 均保留上述 hash
  - result ID：`ee62d7e2abe143299ceac34a72332432`
  - Evaluator：`pass`

#### step_2

- 工具：`get_tron_transaction`
- 实际输入：

```json
{
  "txid": "9faa13f61f59bbcba67bc0736460278b7900ab1110b1d699defe417957a568bf"
}
```

- dependency evidence：
  - 来源是 `step_1` 的数据库 StepResult
  - 参数与 `deposit_tx_hash` 完全一致
- structured facts：
  - `provider = tron_public_node`
  - `txid` 与数据库字段一致
  - `transaction.txID` 一致
  - `blockNumber = 84514819`
  - `blockTimeStamp = 1784214732000`
  - `fee = 16112700`
  - 返回交易本体、receipt、log、internal transactions
- StepResult：
  - `status = success`
  - result ID：`6c3b8bc0cf3b4e79a155c5e146ff4149`
  - Evaluator：`pass`

最终状态为 `completed`。回答也正确区分了：

- 数据库字段；
- TRON RPC 事实；
- `deposit_chain_id = 32004` 不能仅凭单样本推广成固定链映射。

### dependency propagation 专项判断

这个 case 是明确的正向对照：

```text
Tool Result.deposit_tx_hash
→ step_1 StepResult.structured_facts
→ step_2 dependency result
→ get_tron_transaction.txid
```

值逐字符一致，依赖传播可靠，没有靠模型猜测或从最终回答反推。

### first divergence point

运行时没有偏离预期。

第一个评测层偏离发生在 deterministic permission check：

- planned 模式每个 step 都经过 Permission Gate；
- 只读步骤生成 `action = allow` 事件；
- 测试数据的 `expected_permission = none`；
- Evaluator 将 `none` 定义成“不能存在任何 permission action”，见 [deterministic.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/evaluation/deterministic.py:92)。

因此 actual 为 `["allow", "allow"]`，case 被判失败，尽管工具选择、参数、任务完成全部通过。

### 分类与范围

- 主分类：**Evaluator**
- 不是 Permission runtime 错误：Permission Gate 的 `allow` 决策本身正确。
- 范围：**框架级**。所有 planned + read-only + `expected_permission=none` 的 case 都可能被误判。

---

## multi_003

### 用户任务与期望路径

1. 查询 JustLend 最大 `amount_usd` 事件。
2. 提取该事件 `tx_hash`。
3. 查询对应 TRON 交易及回执。
4. 比较数据库字段与链上回执。
5. 明确数据库只代表项目数据覆盖，不能当成 TRON 全链统计。

### Planner 实际 plan

HTTP adapter 超时后没有保留 plan JSON、node events 或 StepResult，所以不能从报告逐字恢复 Planner 输出。

根据后台工具时间和调用分组，可重建其执行意图为：

| 阶段 | 实际目标 |
|---|---|
| 前置探查 | 列出表并查看 `public.justlend` schema |
| 数据库查询 | 取最大事件；额外查询最高若干 `amount_usd` 的计数分布 |
| 链上核验 | 用最大事件的 `tx_hash` 调用 `get_tron_transaction` |
| 汇总 | 比较数据库字段、链上回执和覆盖边界 |

Planner 很可能仍存在与 `multi_001` 相同的多余表/schema 探查。由于 plan 没被写入评测产物，无法可靠给出每个 `depends_on`、success criteria 的原文。

### 实际 step / tool 序列

评测 JSON 显示零工具调用，但这是错误观测。超时窗口内留下了以下后台结果：

1. `postgres_list_tables`
2. `postgres_table_schema(public.justlend)`
3. 同一时点的两个 `postgres_select`
   - 最大金额事件
   - 最大金额分布/并列性检查
4. `get_tron_transaction(txid=最大事件 tx_hash)`
5. 随后进入汇总/审核阶段，客户端在 180 秒处超时

前两个工具创建时间同为 `13:26:37Z`，两个 SQL 创建时间同为 `13:26:54Z`，说明 Executor 在各自 step/轮次内使用了并行 tool calls。

### 输入、facts 与依赖证据

#### 数据源探查

- `postgres_list_tables({})`
  - facts：8 张表，包含 `justlend`
- `postgres_table_schema({"table_name":"public.justlend"})`
  - facts：17 个字段，包含 `tx_hash`、`amount_usd`、业务时间、地址和 token 字段

#### 最大事件查询

输入：

```sql
SELECT
    day,
    occurred,
    ingested_at,
    tx_seq,
    market_event_id,
    event_index,
    tx_hash,
    from_address,
    to_address,
    market_id,
    operation_type,
    token_symbol,
    token_address,
    amount,
    price_usd,
    amount_usd,
    updated_at
FROM public.justlend
ORDER BY amount_usd DESC NULLS LAST
LIMIT 1
```

结果：

- `tx_hash = 742A2581F3ABB84FD1531B7A2CD5D0E5EFBD416D38477B14540FA729B421165E`
- `amount_usd = 50000000`
- `operation_type = stake`
- `token_symbol = USDT`
- `occurred = 2026-08-01 17:32:09 +0800`

额外 SQL：

```sql
SELECT amount_usd, COUNT(*) AS cnt
FROM public.justlend
GROUP BY amount_usd
ORDER BY amount_usd DESC NULLS LAST
LIMIT 5
```

结果显示 `50000000` 的计数为 1。该查询不是完成用户任务所必需，但可用于说明最大值没有并列。

#### TRON 查询

实际输入：

```json
{
  "txid": "742A2581F3ABB84FD1531B7A2CD5D0E5EFBD416D38477B14540FA729B421165E"
}
```

它与数据库查询结果完全一致，证明本 case 的 Tool Result → 下一工具参数传播成功。

链上结果：

- 规范化 txid：
  `742a2581f3abb84fd1531b7a2cd5d0e5efbd416d38477b14540fa729b421165e`
- 类型：`TriggerSmartContract`
- transaction result：`SUCCESS`
- receipt result：`SUCCESS`
- block number：`84968651`
- block timestamp：`1785576729000`
- fee：`20738700`
- owner/contract 地址与数据库 `from_address`、`to_address` 在补 `41` 前缀并转小写后对应
- receipt 含 USDT 合约 Transfer log，转账 data 对应数据库的 50,000,000 USDT 原始单位

### StepResult / dependency evidence 状态

由于 adapter 超时异常分支直接返回空 observation，没有保存服务器端 state，因此不能从评测产物逐项列出 StepResult summary/evidence 文本。

但可以确认：

- 最大事件的工具结果已经持久化；
- `get_tron_transaction` 的实际 `txid` 与其完全一致；
- 所以数据库 hash 至少已经可靠到达链上工具参数；
- 链上工具结果也已持久化；
- 丢失发生在“把完整运行状态返回给 evaluator”这一级，而不是数据库到链上参数的依赖链。

HTTP adapter 在 timeout 异常中仅写 `status=failed,error=...`，不保留任何部分 trace，见 [adapters.py](/Users/yuyu/Documents/code/Chaincloud-AI-main/src/chaincloud_agent_service/evaluation/adapters.py:89)。

### first divergence point

执行路径本身直到 TRON 查询均符合预期。

第一个可证偏离是：请求在 Answer Composer/Reviewer 尚未返回前达到 HTTP adapter 的 180 秒超时。

后果是：

- reply 为空；
- execution trace 为空；
- Evaluator 误认为实际工具序列为 `[]`；
- “数据库 / 链上 / 覆盖”三个 required facts 全部失败。

### 分类与范围

- 主分类：**timeout/性能**
- 次分类：
  - **Evaluator/观测框架**：超时后丢弃部分执行轨迹
  - **Planner**：存在多余 schema 探查和额外分布 SQL，增加 LLM 轮次
  - **Answer Composer/Reviewer 性能**：工具已在超时前约 42 秒完成，剩余耗时发生在汇总/审核链
- 范围：**框架级**，复杂 planned case 都可能发生。
- case-specific 因素：本 case 的 plan 和返回数据较大，TRON receipt 含大量 logs/internal transactions，放大了上下文和生成耗时。

---

## 三个 case 的共同根因

不存在一个同时解释三者业务失败的单一根因：

| 问题 | multi_001 | multi_002 | multi_003 |
|---|---:|---:|---:|
| tx hash Tool Result → 下一工具参数丢失 | 否；在 State Validation 被阻断 | 否 | 否 |
| State Validation 裸 txid 误判 | 是 | 未触发阻断 | 未触发阻断 |
| permission `none` 评分口径错误 | 是 | 是，且是唯一失败原因 | 超时后无事件，未暴露 |
| Planner 过度探查 | 是 | 否 | 是 |
| timeout/性能 | 否，约 60 秒后主动阻断 | 否，约 87 秒完成 | 是，180 秒 |
| 超时后观测丢失 | 否 | 否 | 是 |

最重要的专项结论是：

> 三个 case 都没有证据表明 `tx_hash` 在 Tool Result → StepResult → 下一 step 参数链上被普遍丢失。`multi_002` 和 `multi_003` 已实际用正确 hash 调用了 TRON；`multi_001` 的 hash 也已进入 structured facts/StepResult，只是 State Validation 的 identifier 正则不接受裸 64 位 txid。

---

## 按优先级排序的最小修改建议

暂不实施，仅建议：

1. **修正 State Validation 的 identifier 识别。**

   接受边界明确的裸 64 位十六进制交易哈希，例如独立 token 的 `(?:0x)?[0-9a-fA-F]{64}`；地址和 txid 最好分成不同类型校验。更稳妥的是直接遍历 dependency `structured_facts` 中语义字段，如 `tx_hash`、`deposit_tx_hash`、`txid`，而不是把 StepResult JSON 拼成字符串后用宽泛正则搜索。

2. **统一 `expected_permission=none` 的评测语义。**

   对 planned 只读步骤，`allow/risk_level=none` 应视为“无需用户许可”，而不是失败。可以将 `none` 定义为“不出现 `need_confirm` 或 `deny`”；如果确实要断言“不运行 gate”，应另设 `not_checked` 或 `no_gate_event`。

3. **超时也保留部分轨迹。**

   HTTP adapter 应在请求进行中采集流式事件，或服务端按 trace/thread 持久化执行状态；超时 observation 至少应保留 plan、已完成 tool events、StepResult 和当前 node。不能把“客户端没收到最终响应”折算成“agent 没调用工具”。

4. **将 plan 和 StepResult 纳入 debug execution trace。**

   现有 `_execution_summary` 本来包含 plan 和 step results，但最终评测 trace 没有保存它们。建议加入脱敏后的：
   - plan
   - current step
   - accepted StepResults
   - dependency field provenance
   - state-validation input/evidence IDs

   这样以后无需从工具时间戳反推实际 plan。

5. **明确并统一 dependency evidence 通道。**

   当前 Executor 的 `critical_state` 包含依赖 StepResult，但 Context Builder 的 `dependency_evidence` 只取当前 step 内消息。建议直接从 `depends_on` 对应 StepResult 构造 dependency evidence，并携带字段级 provenance，例如：

   ```text
   step_1.structured_facts[0].sample[0].deposit_tx_hash
   → step_2.args.txid
   ```

6. **减少 Planner 的无效探查。**

   强化已有规则：当用户明确给出 `justlend`、`amount_usd`、`tx_hash` 时，优先直接执行一条查询；只有 SQL 因表/列不存在失败后，再进入 schema recovery。不要默认先 list tables、再 schema。

7. **压缩链上结果后再进入 Answer Composer/Reviewer。**

   对 TRON receipt 保留 txid、状态、区块、时间、费用、合约、关键 log 摘要和 result reference；避免将大量 raw hex、所有 logs/internal transactions 重复送入多个 LLM 阶段。这是降低 `multi_003` 延迟的最小性能改动。

这些建议均是通用能力修复，不需要硬编码 case ID、关键词、txid、特定 SQL 或测试数据。
