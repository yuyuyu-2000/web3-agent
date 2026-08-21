# Planned 模式 Over-planning 分析

## 结论

当前 planned 模式存在明确的 over-planning，重点表现为：

1. `multi_001` 把本可直接完成的“查最大事件 → 按 tx_hash 查链上”扩成了“列举表 → 查 schema → 查最大事件 → 查链上”4 步；前两步均非成功标准所需。
2. `multi_003` 可重建为“表/schema 探查 → 最大事件及分布查询 → 链上核验/比较”3 个执行阶段，并在数据库阶段额外执行 `amount_usd GROUP BY`。表/schema 探查和分布查询都不是用户目标所必需。
3. 根因不是模型完全无视已有规则，而是 Planner 的实际上下文并没有拿到完整 schema 文档。`public.justlend`、`amount_usd`、`tx_hash` 的确定性说明被注入 Executor，却没有注入 Planner；Planner 只看到自己的通用规划规则和容易诱发探查的工具描述。
4. 删除冗余步骤不会降低正常路径的 correctness，也不会改变 permission 结论。schema discovery 应保留为目标 SQL 失败后的 recovery，而不是默认前置步骤。
5. `multi_003` 的 `GROUP BY amount_usd` 只能验证最大金额是否并列；用户没有要求唯一性、排名分布或并列处理，且 `ORDER BY ... LIMIT 1` 已满足“最大金额事件”这一单数目标，所以该查询属于额外验证，不是必要步骤。

## 分析口径与证据边界

分析以 `eval/test_cases.jsonl`、`eval_results/run_20260819T133827Z.json`、`eval_results/run_20260820T123658Z.json`、持久化 `tool_results` 和当前实现为依据。

评测产物没有稳定序列化完整 plan：

- `multi_001` 的 2026-08-19 轨迹保留了 step ID、工具和 Evaluator decision，可可靠重建 4 步 plan。
- `multi_003` 两次都在 HTTP 180 秒超时后丢失 observation 中的 plan、node events 和部分轨迹；其后台持久化结果只能可靠证明 5 次工具调用及调用分组。因此下文将它标记为“可重建 3 个执行阶段”，而不是声称拿到了 Planner 原始 JSON。
- “当前实际 step 数”优先采用最近一次具有完整轨迹的运行；若最新运行超时丢轨迹，则使用最近可证运行或后台结果重建，并明确标注。

## 为什么 Planner 明知字段仍先探查 schema

### 1. Planner 实际上没有收到完整 schema 上下文

`build_agent_system_prompt()` 明确包含：

- JustLend 对应 `public.justlend`；
- 直接使用该表，不要先调用 `postgres_list_tables`；
- schema 文档已列出 `amount_usd`、`tx_hash` 等字段；
- 通常直接执行目标 `SELECT`，只有 SQL 报表/字段错误或用户明确要求结构时才探查 schema。

但 `graph.py` 的 `planner_node()` 调用 `context_builder.planner()` 时，传入的 system prompt 只有 `PLANNER_SYSTEM_PROMPT`。Planner 上下文由以下内容构成：

- Planner 通用规则；
- 当前用户目标；
- 最近对话和摘要；
- 工具 catalog。

完整的 `system_prompt = build_agent_system_prompt(settings)` 只在 `executor_node()` 中使用。换言之，系统整体确实知道 schema，Executor 也知道，但 Planner 在生成 plan 时并未获得这份知识。用户文本中的 “JustLend / amount_usd / tx_hash” 对 Planner 只是强提示，不等价于获得“`public.justlend` 及字段已经由受信 schema 文档确认”的上下文。

这是最主要的上下文分层问题。

### 2. 工具描述直接鼓励默认探查

`postgres_list_tables` 的描述是：

> 列出 PostgreSQL 中当前只读账号可见的业务表。查询表数据前可先调用它确认表名。

“查询表数据前可先调用”给模型提供了通用的安全工作流先验。即使 Planner 规则说“已知表名时不要重复调用列举表工具”，Planner 并不知道 `JustLend → public.justlend` 是已经确认的映射，因而容易把它解释成“用户给的是业务名，仍应确认物理表名”。

`postgres_table_schema` 的描述又强调它能返回字段、类型、可空信息和 3 行样本。对一个被要求比较数据库字段与链上回执的模型而言，这看起来像低风险、高信息量的验证步骤，于是形成典型链条：先列表、再看 schema、最后查询。

### 3. Planner prompt 中存在鼓励拆分探查的竞争性规则

`PLANNER_SYSTEM_PROMPT` 虽有两条抑制 over-planning 的规则：

- 简单任务只生成一个步骤，不要过度拆分；
- 已知表名时不要重复调用列举表工具。

但也有会把模型推向探查的规则：

- 每个步骤必须有明确成功标准；
- 一个步骤只设一个主要、可验证的数据目标；
- “定位数据源、确认结构、查询明细、统计分析”在额度可能不足时应拆成有依赖关系的步骤；
- 每步默认最多 4 次工具调用。

其中“定位数据源、确认结构、查询明细、统计分析”的枚举实际上给出了一个标准调查模板。模型在缺少 schema 上下文时，会把“确认结构”视为合理且可验证的独立目标。最多 6 步、每步 4 次调用的宽预算也没有给冗余步骤形成成本压力。

此外，“能够用一条聚合 SQL 同时确认日期覆盖、记录数和金额分布时……”以及 schema system prompt 中“优先用一条聚合 SQL 同时确认日期覆盖、记录数和金额分布”的措辞，会让模型形成“分析任务通常应补充覆盖/分布验证”的先验。它对日期统计任务有价值，但对“取最大一条并查回执”并不适用，且 Planner 没有被明确要求用用户成功标准约束额外验证。

### 4. Validator 只验证结构合法，不验证最小性

`validate_plan()` 只检查 step ID、依赖、环、工具是否存在等结构约束。它不会判断：

- 已知 schema 时是否仍用了 discovery 工具；
- 某一步是否对用户成功标准有必要贡献；
- 删除该步后任务是否仍可完成；
- discovery 是否应被降级为失败 recovery。

所以只要模型输出形式合法，冗余 plan 会原样进入执行。

## multi_001

### 实际计划与最小充分计划

| 项目 | 数量/内容 |
|---|---|
| 当前实际 step 数 | 4 |
| 当前实际工具调用 | 计划上 4；已执行 3，链上步骤被 State Validation 阻断 |
| 最小充分 step 数 | 2 |
| 最小充分路径 | ① `postgres_select` 取 `amount_usd` 最大事件及 `tx_hash`；② `get_tron_transaction` 查本体和回执，并据两类证据作答 |
| 可删除步骤 | `step_1 postgres_list_tables`、`step_2 postgres_table_schema` |

可重建的实际步骤为：

1. 列出数据库表，确认 JustLend 表存在。
2. 查看 `public.justlend` 表结构，确认 `amount_usd`、`tx_hash` 等字段。
3. `ORDER BY amount_usd DESC LIMIT 1` 查询最大事件并取得 `tx_hash`。
4. 用该 hash 查询 TRON 交易本体与回执。

### 删除影响

| 维度 | 影响 |
|---|---|
| Correctness | 不影响正常路径。受信 schema 已明确给出表和列，目标 SQL 本身是更强的存在性与可用性验证：查询成功即可同时证明表、列和权限可用。 |
| Recovery | 不应取消 discovery 能力，只应改变触发时机。若目标 SQL 返回 undefined table/column、search_path 或权限相关错误，再进入 `postgres_table_schema`；只有物理表名未知时才使用 `postgres_list_tables`。 |
| Permission | 不影响。三个 PostgreSQL 工具和 TRON 查询都在只读白名单中；删除两个只读步骤只会减少两次 `ALLOW/risk_level=none` gate，不会新增确认，也不会绕过任何副作用权限。 |

需要特别区分：删除冗余步骤不能修复 `multi_001` 当前的 State Validation 裸 64 位 txid 误判。它能缩短到达链上步骤的路径，但 correctness 要完整恢复，仍需让 recovery/state validation 正确认出 dependency 中的 TRON txid。这是独立问题，不是保留 schema 探查的理由。

## multi_003

### 实际执行与最小充分计划

后台结果可证的工具序列为：

1. `postgres_list_tables`；
2. `postgres_table_schema(public.justlend)`；
3. 最大事件查询；
4. `SELECT amount_usd, COUNT(*) ... GROUP BY amount_usd ... LIMIT 5`；
5. `get_tron_transaction(max_event.tx_hash)`。

前两个工具同一时间组产生，两个 SQL 也在同一时间组产生，因此最合理的 plan 重建为 3 个执行阶段：

1. 数据源/schema 探查；
2. 数据库最大事件与金额分布分析；
3. 链上核验并比较来源和覆盖边界。

| 项目 | 数量/内容 |
|---|---|
| 当前实际 step 数 | 可重建为 3；原始 plan JSON 因超时未保存 |
| 当前实际工具调用 | 5 |
| 最小充分 step 数 | 2 |
| 最小充分路径 | ① 一条 SQL 取最大事件及比较所需数据库字段；② 按 tx_hash 查 TRON 本体/回执并完成字段、来源、覆盖范围比较 |
| 可删除步骤 | 整个表/schema 探查阶段 |
| 可删除的同一步额外调用 | `amount_usd GROUP BY` 分布/并列性查询 |

### GROUP BY 是否必要

不必要。它证明 `amount_usd = 50,000,000` 的记录数为 1，解决的是“最大值是否并列”问题；用户的成功标准是：

- 找到 JustLend 最大金额事件；
- 比较该数据库记录与对应 TRON 回执；
- 说明数据来源与覆盖范围；
- 不把数据库记录当成全链统计。

这些目标均不依赖最大值唯一性。`ORDER BY amount_usd DESC NULLS LAST LIMIT 1` 已足以选择一条最大事件。即使最大值并列，用户仍只要求单数“事件”；若产品希望对并列采用确定性选择，可在同一条目标 SQL 中增加稳定次级排序，而不是追加分布查询。只有用户明确要求“所有并列最大事件”“确认是否并列”或答案正确性确实依赖唯一性时，`GROUP BY`/窗口统计才是必要步骤。

“不能把数据库记录当成全链统计”也不要求做金额分布查询。覆盖边界来自数据源定义：`public.justlend` 只覆盖 JustLend 协议事件；TRON RPC 查询只证明指定 txid 的链上本体与回执。统计五个最高金额的计数不会把协议库变成全链数据，也不会增强覆盖边界结论。

### 删除影响

| 维度 | 影响 |
|---|---|
| Correctness | 删除 discovery 和 GROUP BY 不影响用户成功标准。最大事件 SQL、TRON 回执及已知数据源说明已经构成充分证据链。 |
| Recovery | 与 `multi_001` 相同：schema discovery 应在目标 SQL 因 schema 类错误失败后触发。若最大值存在并列但用户没有要求全取，直接选择一条仍正确；若用户要求并列完整性，再升级查询。 |
| Permission | 不影响。删除的是只读工具调用，只减少 `ALLOW` gate；链上只读核验仍执行，副作用边界不变。 |

删除这些调用还会减少传入 Executor、Answer Composer 和 Reviewer 的 sample rows、schema 列表与分布结果，降低上下文体积。`multi_003` 的主要超时还包括大体积 TRON receipt 和后续汇总/审核，因此去冗余能降低风险，但不能保证单独消除 180 秒超时。

## 其余 planned cases

### 汇总

| Case | 当前实际 step 数 | 最小充分 step 数 | 可删除步骤 | 删除后 correctness / recovery / permission |
|---|---:|---:|---|---|
| `multi_001` | 4 | 2 | list tables、table schema | correctness 不变；discovery 后移为 SQL 失败 recovery；permission 不变 |
| `multi_002` | 最新完整运行为 3；前一运行为 2 | 2 | 最新运行中的 `postgres_table_schema(public.croas_chain)` | correctness 不变；schema 错误时 recovery；permission 不变 |
| `multi_003` | 可重建 3 | 2 | discovery 阶段；另删同一步 GROUP BY 调用 | correctness 不变；失败时 recovery；permission 不变 |
| `scheduler_001` | 超时产物未保存 plan，按目标应为 1 | 1 | 无可证冗余步骤 | 创建前必须保留确认；不可用“最小化”绕过 permission |
| `scheduler_002` | 1 | 1 | 无 | 唯一步是有副作用的创建动作；必须保留 `NEED_CONFIRM` |
| `scheduler_003` | 1 | 1 | 无可删除步骤，但当前 step 语义需调整 | 缺少执行时间时应先澄清，不能仅请求副作用确认；permission 仍应在参数齐全、实际创建前执行 |

### multi_002

`multi_002` 是规划不稳定性的对照：2026-08-19 运行直接采用两步最小路径并成功；2026-08-20 运行又变成 3 步，先执行 `postgres_table_schema`，再查询和链上核验。相同代码路径可产生是否探查 schema 不一致的 plan，说明现有 prompt 只是软建议，没有形成可验证的最小性约束。

删除最新运行的 schema step 不影响正确性。`config/agent_database_schema.md` 已明确 `public.croas_chain.deposit_tx_hash`；目标查询成功本身足以验证字段可用。若失败，再按错误类型恢复即可。

### scheduler_001～003

这三个 case 没有证据显示 schema 类 over-planning：

- `scheduler_002` 的 plan 为 1 步，Permission Gate 在执行 `add_scheduled_task` 前正确停在 `NEED_CONFIRM`。
- `scheduler_003` 也是 1 步，但用户尚未给执行时间。这里的问题不是 step 太多，而是最小充分动作应为“澄清时间”，而不是立刻对一个参数不完整的创建动作请求确认。澄清不需要工具；时间补齐后，再对实际创建请求一次确认。
- `scheduler_001` 的 180 秒超时 observation 丢失 plan 和轨迹，无法声称看到了实际 Planner JSON。按用户目标，最小充分 plan 是 1 个带确认的创建步骤；没有依据建议删除权限步骤。

因此，Minimal Sufficient Plan 原则必须服从权限边界：最小化只删除不必要工作，不能合并或删除必要的人类确认，也不能把缺少关键参数误当成已可执行。

## Minimal Sufficient Plan 原则

### 定义

一个 plan 是最小充分的，当且仅当：

1. 每个正常路径 step 都直接贡献于用户明确目标或成功标准；
2. 删除任一步都会使正常路径无法完成、无法提供必要证据，或违反权限要求；
3. 已知事实不被重复探查；
4. 仅为失败处理准备的动作位于 recovery 分支，不占用默认成功路径；
5. 用户未要求、且不改变成功判定的验证不执行。

可用一个通用删除测试约束 Planner/plan validator：

> 对每个 step，询问“若已有上游事实成立且工具正常成功，删除该 step 后，用户成功标准、证据可追溯性或权限边界是否受损？”若三者都不受损，该 step 不应在主计划中。

### 规则 1：已知 schema 直接使用

- Planner 必须能看到与 Executor 同源、经过裁剪的 schema facts，而不是只看到工具 catalog。
- 当上下文已确认业务名到物理表的映射及目标列时，直接生成目标查询步骤。
- “用户提到字段”本身不必无条件可信；关键是系统受信 schema context 已确认该字段。若只有用户猜测而系统无 schema，则可以探查。
- 不针对 case ID、固定 SQL、固定 txid 或某一测试数据做规则；规则应基于通用状态，如 `known_tables`、`known_columns`、`schema_source` 和 schema 版本。

### 规则 2：schema discovery 是 recovery，不是默认前置

建议的通用状态机：

```text
已知 table + required columns
  → 直接执行目标 SQL
  → 成功：继续下游步骤
  → undefined_table：必要时 list tables / 修正映射 / 重试一次
  → undefined_column 或 type mismatch：table schema / 修正列或转换 / 重试一次
  → permission denied：按权限/能力错误处理，不用 discovery 循环掩盖
  → 其他错误：按现有 retry/fallback 策略处理
```

`postgres_list_tables` 与 `postgres_table_schema` 不应被简单删除出工具集；它们应成为有错误触发条件、调用上限和 provenance 的 recovery tools。恢复后必须回到原目标 SQL，不能把“成功看到 schema”误判为完成用户任务。

### 规则 3：禁止无关额外验证

只有满足至少一项时才执行额外验证：

- 用户明确要求；
- success criteria 明确依赖；
- 主结果存在可识别歧义，且不消除歧义就无法正确回答；
- 安全、权限或不可逆操作要求；
- 工具结果出现异常，需要验证才能区分成功、空结果或错误。

以下理由单独存在时不足以增加调用：

- “更全面”；
- “顺便确认”；
- “可能有并列”，但用户允许返回一条；
- “说明覆盖范围”，但覆盖范围已由数据源契约确定；
- “提高置信度”，但新增结果不会改变答案或成功判定。

### 规则 4：计划粒度按依赖边界，而不是按工具数量

- 需要把前一步动态输出作为后一步参数时，应分步，例如数据库 `tx_hash → TRON txid`。
- 同一数据目标内的一条 SQL 和其结果解释不应拆成多个 step。
- discovery recovery 不预先占一个 step；只有失败时动态插入或重规划。
- 最终作答通常不需要独立工具 step；Answer Composer 可消费已有 StepResult。

### 规则 5：权限不可因最小化而弱化

- 删除只读探查只减少无意义的 `ALLOW` 事件。
- 所有副作用工具仍必须在精确参数和影响摘要可用后触发确认。
- 缺少副作用操作的关键参数时，先澄清；不能让用户确认一个尚未定义完整的操作。
- recovery 不得用替代工具绕过 `NEED_CONFIRM` 或 `DENY`。

### 通用、非硬编码的实现约束建议

暂不修改代码，但原则落地时应优先采用：

1. 向 Planner 注入裁剪后的受信 schema facts 和 `known/unknown` 标记。
2. 在 Planner prompt 中明确：正常路径不得包含仅用于确认已知 table/column 的 discovery；把 discovery 写入 recovery policy，而非 steps。
3. 调整工具描述，去掉 `postgres_list_tables` 中“查询表数据前可先调用”的默认前置暗示，改成“表名未知或目标 SQL 报 undefined_table 时使用”。
4. 为 plan 增加静态最小性检查：若 step 只使用 schema discovery，且目标表/列已在受信上下文中，则拒绝或要求 Planner 重写。
5. 给额外验证增加 `necessity_reason`，必须引用用户要求、success criterion、权限或具体歧义；“更全面”不能通过校验。
6. 以错误类别驱动 recovery，限制 discovery/retry 次数，防止 schema 探查循环。

这些约束只依赖能力状态、错误类别和用户成功标准，不依赖 evaluation case、具体 SQL 文本或任何 txid。

## 调用量与理论 latency 估算

### 每个重点 case

planned step 的常见运行链是：

```text
Executor 生成 tool call → Tool → Executor 总结 step → Evaluator 判定
```

因此删除一个完整的单工具 step，通常减少：

- 2 次 Executor 模型调用；
- 1 次 Evaluator 模型调用；
- 1 次 Tool 调用；
- 1 次 Permission Gate（代码调用，非 LLM，耗时很小）。

| Case | 可减少 Executor 调用 | 可减少 Evaluator 调用 | 可减少 Tool 调用 | 说明 |
|---|---:|---:|---:|---|
| `multi_001` | 4 | 2 | 2 | 删除 list-tables 与 schema 两个完整 step |
| `multi_003` | 至少 2 | 至少 1 | 3 | 删除含两个并行 discovery tools 的完整阶段；GROUP BY 与目标 SQL 同轮并行时，删除它只减少 1 个 Tool，不一定再减少 Executor/Evaluator 轮次 |
| `multi_002`（最新完整运行） | 2 | 1 | 1 | 删除 schema step |
| scheduler cases | 0 | 0 | 0 | 无可证冗余执行 step；确认/澄清不能删除 |
| 合计 | 至少 8 | 至少 4 | 6 | 不计 recovery；正常成功路径估算 |

如果 `multi_003` 的 GROUP BY 在独立 step 中执行，而非与最大事件 SQL 同一 step 并行，则其上界会再减少 2 次 Executor 和 1 次 Evaluator，即总计最多减少 10 次 Executor、5 次 Evaluator、6 次 Tool 调用。现有时间分组更支持“同一步并行”，所以应采用“至少 8/4/6”作为保守估计。

### latency

数据库工具本身不是主要耗时：已保存轨迹中的 list/schema/select 通常约 15～30 ms。主要 latency 来自每个多余 step 前后的 Executor 生成和 Evaluator 审核。

`multi_001` 的两个冗余 step 实测模型耗时约为：

- step 1：Executor 1.20 s + Executor 2.45 s + Evaluator 2.56 s；
- step 2：Executor 1.51 s + Executor 4.34 s + Evaluator 3.12 s；
- 两个工具合计约 0.05 s。

直接可归因的 wall-clock 节省约为 15.2 秒，约占该次 60.1 秒运行的 25%。同时计划更短、后续上下文更小，还会有少量间接收益，但不应在没有测量时重复计入。

按该实测每个完整冗余 step 约 6～9 秒估算：

- `multi_001`：约 15 秒；
- `multi_002`：约 6～9 秒；
- `multi_003`：删除 discovery 阶段约 6～12 秒；删除并行 GROUP BY 的工具网络耗时本身仅几十毫秒，但可减少结果序列化、上下文 token 和后续生成负担，理论上再节省约 0～数秒；
- 六个 planned cases 合计正常路径理论节省约 27～40 秒。

这是基于现有串行 LLM 节点和已观察时长的工程估算，不是 SLA。模型排队、生成长度、TRON receipt 大小、Reviewer 是否重写都可能使实际值波动。对 `multi_003` 而言，去除冗余最多只能解释一部分 180 秒超时；工具完成后的 Answer Composer/Reviewer、大型链上结果上下文以及 adapter 超时丢轨迹仍是独立的主要性能问题。

## 最终判断

planned 模式的核心问题不是“使用 Planner”本身，而是 Planner 所见上下文与 Executor 所见事实不一致，加上工具描述和 prompt 的调查模板共同奖励了“先探查再执行”。正确方向是让正常路径只保留完成用户成功标准所必需的依赖链：

```text
已知 schema → 目标查询 → 使用动态结果调用下游工具 → 基于必要证据作答
```

schema discovery、额外统计和并列性检查都应由真实失败、明确歧义或用户要求触发。这样可在不牺牲 correctness、recovery 和 permission 的前提下，保守减少 6 次 Tool、至少 8 次 Executor、至少 4 次 Evaluator 调用，并显著降低 planned 模式的 latency 与超时概率。
