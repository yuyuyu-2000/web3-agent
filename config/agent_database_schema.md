# ChainCloud Agent 数据库说明

> 适用文件：`config/agent_database_schema.md`  
> 当前版本：ClickHouse `trx` 数据库初始说明版  
> 目的：为 ChainCloud-AI Agent 提供可读的公司数据库结构说明，帮助 Agent 正确选择表、生成安全 SQL、解释查询结果。  
> 安全原则：所有查询必须是只读查询，禁止写入、删除、修改或结构变更操作。

---

## 1. 总体说明

当前 Agent 可访问的主要数据源为 ClickHouse 数据库 `trx`。

ClickHouse 主要用于存储大规模链上数据，例如：

- 链上交易记录；
- TRON 地址行为记录；
- TRC20 / USDT 转账记录；
- Token 字典；
- 地址标签映射；
- 智能合约相关数据；
- 待处理交易与历史记录；
- 按日聚合的统计数据。

Agent 在使用数据库时，应优先根据用户问题判断需要查询的表，再通过 `DESCRIBE TABLE` 查看字段结构，最后使用带有 `LIMIT` 的只读 `SELECT` 查询获取少量数据。

---

## 2. 查询安全规则

Agent 必须遵守以下规则：

1. 只允许执行只读查询。
2. 优先使用 `SHOW`、`DESCRIBE`、`SELECT`。
3. 查询业务数据时必须使用 `LIMIT`。
4. 查询大表时不要直接 `SELECT *` 查询大量数据。
5. 不允许执行以下操作：

```sql
DROP
DELETE
TRUNCATE
ALTER
INSERT
UPDATE
CREATE
OPTIMIZE
RENAME
```

6. 当用户请求查看表中样例数据时，最多查询 5 到 20 行。
7. 当用户请求分析某张表时，应先查询字段结构，再查询少量样例。
8. 当用户要求查询地址、交易哈希或 token 时，应尽量使用精确条件过滤。
9. 如果不确定表结构，应先执行：

```sql
DESCRIBE TABLE trx.table_name
```

10. 如果不确定当前库中有哪些表，应先执行：

```sql
SHOW TABLES FROM trx
```

---

## 3. 当前数据库

### 3.1 数据库名称

```text
trx
```

### 3.2 数据库类型

```text
ClickHouse
```

### 3.3 主要业务方向

`trx` 数据库主要面向 TRON 链相关数据，包括 TRX 原生资产、TRC20 token、USDT 转账、地址标签、交易历史和智能合约数据。

---

## 4. 已观察到的数据表

以下表来自当前 DBeaver 中已观察到的 `trx` 数据库表列表。

| 表名 | 初步用途判断 |
|---|---|
| `dictionary_contract_address_fake` | 合约地址相关字典或伪造地址识别信息 |
| `dictionary_tokens` | Token 字典表，记录 token 地址、符号、名称、精度 |
| `dictionary_trx_map_address_label` | TRON 地址与标签映射表 |
| `distributed_histories` | 历史交易或链上行为总表 |
| `distributed_histories_aggregated_daily` | 按日聚合的历史记录 |
| `distributed_histories_daily_aggregated` | 已完成的按日聚合历史记录 |
| `distributed_histories_daily_aggregating` | 聚合中的历史记录中间表 |
| `distributed_histories_others` | 其他类型历史记录 |
| `distributed_histories_permission` | 权限相关历史记录 |
| `distributed_histories_resource` | 资源相关历史记录 |
| `distributed_histories_trc20` | TRC20 token 相关历史记录 |
| `distributed_histories_trx` | TRX 原生资产相关历史记录 |
| `distributed_histories_trx_petty` | 小额 TRX 历史记录 |
| `distributed_histories_usdt` | USDT 相关历史记录 |
| `distributed_histories_usdt_petty` | 小额 USDT 历史记录 |
| `distributed_pending_histories` | 待处理历史记录 |
| `distributed_pending_tx_entries` | 待处理交易条目 |
| `distributed_pending_txs` | 待处理交易 |
| `distributed_smart_contracts` | 智能合约相关数据 |
| `distributed_tx_entries` | 交易条目 |
| `distributed_txs` | 交易记录表 |
| `kafka_consumer_histories_v1` | Kafka 消费的历史记录数据 |
| `kafka_consumer_history_categories_swap_parsed` | Swap 分类解析相关消费数据 |
| `kafka_consumer_pending_histories_v1` | Kafka 待处理历史数据 |
| `kafka_consumer_pending_txs_v1` | Kafka 待处理交易数据 |
| `kafka_consumer_txs_v1` | Kafka 交易消费数据 |

---

## 5. 核心表说明

### 5.1 `trx.dictionary_tokens`

#### 表用途

`dictionary_tokens` 是 token 字典表，用于记录 token 合约地址、token 符号、token 名称和 token 精度。

该表适合用于：

- 根据 token 地址查询 token 信息；
- 根据 token 符号或名称理解 token 含义；
- 辅助解释 TRC20 或 USDT 相关交易；
- 将交易记录中的 token 地址映射为更容易理解的 token 符号和名称。

#### 已观察到的字段

| 字段 | 初步含义 |
|---|---|
| `Address` | Token 合约地址 |
| `Symbol` | Token 符号 |
| `Name` | Token 名称 |
| `Decimals` | Token 精度 |

#### 推荐查询

查看少量 token 样例：

```sql
SELECT Address, Symbol, Name, Decimals
FROM trx.dictionary_tokens
LIMIT 5
```

根据 token 地址查询：

```sql
SELECT Address, Symbol, Name, Decimals
FROM trx.dictionary_tokens
WHERE Address = '<TOKEN_ADDRESS>'
LIMIT 5
```

根据 token 符号查询：

```sql
SELECT Address, Symbol, Name, Decimals
FROM trx.dictionary_tokens
WHERE Symbol = '<TOKEN_SYMBOL>'
LIMIT 20
```

---

### 5.2 `trx.dictionary_trx_map_address_label`

#### 表用途

`dictionary_trx_map_address_label` 可能是 TRON 地址标签映射表，用于记录地址与标签之间的对应关系。

该表适合用于：

- 查询某个地址是否有标签；
- 判断地址是否属于某类实体；
- 辅助风险分析、地址归因和交易解释；
- 与交易历史表中的 `Address` 或 `Counterpart` 字段关联。

#### 使用前建议

当前尚未完整确认该表字段结构。使用该表前应先执行：

```sql
DESCRIBE TABLE trx.dictionary_trx_map_address_label
```

#### 推荐查询

查看字段结构：

```sql
DESCRIBE TABLE trx.dictionary_trx_map_address_label
```

查看少量样例：

```sql
SELECT *
FROM trx.dictionary_trx_map_address_label
LIMIT 5
```

如存在地址字段，可按地址过滤：

```sql
SELECT *
FROM trx.dictionary_trx_map_address_label
WHERE Address = '<ADDRESS>'
LIMIT 10
```

---

### 5.3 `trx.distributed_histories`

#### 表用途

`distributed_histories` 是历史交易或链上行为总表，可能记录 TRON 链上地址行为、交易哈希、区块号、对手方地址、合约地址、token 标识和交易状态等信息。

该表适合用于：

- 查询地址历史行为；
- 查询交易哈希对应记录；
- 查看某个地址与对手方的交互；
- 分析 token、合约、区块、交易状态等链上信息；
- 作为 TRON 链上分析的核心入口表之一。

#### 已观察到的字段

| 字段 | 类型 | 初步含义 |
|---|---|---|
| `Address` | String | 主体地址 |
| `Serial` | UInt64 | 序号 |
| `ExtendedSerial` | UInt64 | 扩展序号 |
| `Type` | UInt32 | 记录类型 |
| `Action` | UInt32 | 行为类型 |
| `Value` | UInt256 | 数值 |
| `TokenId` | String | Token 标识 |
| `ContractAddress` | String | 合约地址 |
| `Counterpart` | String | 对手方地址 |
| `CreatedAt` | DateTime64(3) | 创建时间 |
| `BlockNumber` | UInt64 | 区块高度 |
| `BlockHash` | String | 区块哈希 |
| `TxHash` | String | 交易哈希 |
| `TxReceiptStatus` | UInt8 | 交易回执状态 |
| `TxType` | UInt32 | 交易类型 |

#### 推荐查询

查看字段结构：

```sql
DESCRIBE TABLE trx.distributed_histories
```

查看少量样例：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories
LIMIT 5
```

根据交易哈希查询：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash, TxReceiptStatus
FROM trx.distributed_histories
WHERE TxHash = '<TX_HASH>'
LIMIT 20
```

根据地址查询：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories
WHERE Address = '<ADDRESS>'
ORDER BY CreatedAt DESC
LIMIT 20
```

根据对手方地址查询：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories
WHERE Counterpart = '<ADDRESS>'
ORDER BY CreatedAt DESC
LIMIT 20
```

---

### 5.4 `trx.distributed_histories_trx`

#### 表用途

`distributed_histories_trx` 可能是 TRX 原生资产相关历史记录表。

该表适合用于：

- 查询 TRX 原生资产转账；
- 分析 TRON 主币流动；
- 查看某个地址的 TRX 相关行为；
- 与 `distributed_histories` 总表进行对比。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_histories_trx
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_histories_trx
LIMIT 5
```

如果字段与 `distributed_histories` 类似，可优先选择以下字段：

```sql
SELECT Address, Counterpart, Value, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories_trx
LIMIT 5
```

---

### 5.5 `trx.distributed_histories_trc20`

#### 表用途

`distributed_histories_trc20` 可能是 TRC20 token 相关历史记录表。

该表适合用于：

- 查询 TRC20 token 转账；
- 查询某个地址的 token 交互；
- 分析 token 合约地址；
- 结合 `dictionary_tokens` 解释 token 符号、名称和精度。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_histories_trc20
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_histories_trc20
LIMIT 5
```

如果包含 token 地址或 token 标识，可与 `dictionary_tokens` 辅助解释。

---

### 5.6 `trx.distributed_histories_usdt`

#### 表用途

`distributed_histories_usdt` 可能是 USDT 相关历史记录表，适合用于 TRON USDT 转账分析。

该表适合用于：

- 查询 USDT 转账记录；
- 查询某个地址相关 USDT 流入流出；
- 分析大额 USDT 转账；
- 结合地址标签表进行风险识别。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_histories_usdt
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_histories_usdt
LIMIT 5
```

如果字段与 `distributed_histories` 类似，可使用：

```sql
SELECT Address, Counterpart, Value, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories_usdt
ORDER BY CreatedAt DESC
LIMIT 20
```

---

### 5.7 `trx.distributed_txs`

#### 表用途

`distributed_txs` 可能是交易记录表，用于存储交易级别信息。

该表适合用于：

- 根据交易哈希查询交易记录；
- 查看交易所在区块、时间和状态；
- 作为交易分析的基础表；
- 与 `distributed_tx_entries` 或历史表结合分析。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_txs
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_txs
LIMIT 5
```

根据交易哈希查询：

```sql
SELECT *
FROM trx.distributed_txs
WHERE TxHash = '<TX_HASH>'
LIMIT 10
```

---

### 5.8 `trx.distributed_tx_entries`

#### 表用途

`distributed_tx_entries` 可能是交易条目表，记录一笔交易中的明细条目。

该表适合用于：

- 拆解一笔交易中的多个条目；
- 分析交易中的地址、资产、金额和合约交互；
- 与 `distributed_txs` 结合使用。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_tx_entries
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_tx_entries
LIMIT 5
```

---

### 5.9 `trx.distributed_smart_contracts`

#### 表用途

`distributed_smart_contracts` 可能存储智能合约相关数据。

该表适合用于：

- 查询合约地址；
- 查看合约相关属性；
- 分析交易是否与智能合约交互；
- 辅助识别 token 合约或业务合约。

#### 使用前建议

当前尚未完整确认字段结构。使用前应先执行：

```sql
DESCRIBE TABLE trx.distributed_smart_contracts
```

#### 推荐查询

查看少量样例：

```sql
SELECT *
FROM trx.distributed_smart_contracts
LIMIT 5
```

---

## 6. 常见用户问题与推荐查询策略

### 6.1 用户询问当前数据库有哪些表

推荐执行：

```sql
SHOW TABLES FROM trx
```

回答时应按用途分类，例如：

- 字典类表；
- 历史交易类表；
- TRX / TRC20 / USDT 专用表；
- 待处理交易表；
- 智能合约表；
- Kafka 消费表。

---

### 6.2 用户询问某张表有什么字段

推荐执行：

```sql
DESCRIBE TABLE trx.table_name
```

回答时应说明：

- 字段名；
- 字段类型；
- 可能含义；
- 哪些字段适合查询地址、交易哈希、区块号或时间。

---

### 6.3 用户询问 token 字典

优先使用：

```sql
SELECT Address, Symbol, Name, Decimals
FROM trx.dictionary_tokens
LIMIT 20
```

如果用户给出 token 地址：

```sql
SELECT Address, Symbol, Name, Decimals
FROM trx.dictionary_tokens
WHERE Address = '<TOKEN_ADDRESS>'
LIMIT 5
```

---

### 6.4 用户询问某个地址的交易历史

优先判断是否查询总表或专用表。

通用查询：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories
WHERE Address = '<ADDRESS>'
ORDER BY CreatedAt DESC
LIMIT 20
```

如用户明确询问 USDT：

```sql
SELECT *
FROM trx.distributed_histories_usdt
WHERE Address = '<ADDRESS>'
ORDER BY CreatedAt DESC
LIMIT 20
```

如用户明确询问 TRX：

```sql
SELECT *
FROM trx.distributed_histories_trx
WHERE Address = '<ADDRESS>'
ORDER BY CreatedAt DESC
LIMIT 20
```

---

### 6.5 用户询问某个交易哈希

优先在历史总表中查询：

```sql
SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash, TxReceiptStatus
FROM trx.distributed_histories
WHERE TxHash = '<TX_HASH>'
LIMIT 20
```

如果 `distributed_txs` 字段确认后，也可以查询：

```sql
SELECT *
FROM trx.distributed_txs
WHERE TxHash = '<TX_HASH>'
LIMIT 10
```

---

### 6.6 用户询问 USDT 转账

优先使用：

```sql
DESCRIBE TABLE trx.distributed_histories_usdt
```

确认字段后，再执行：

```sql
SELECT *
FROM trx.distributed_histories_usdt
LIMIT 5
```

如果字段与总表一致，可查询：

```sql
SELECT Address, Counterpart, Value, CreatedAt, BlockNumber, TxHash
FROM trx.distributed_histories_usdt
ORDER BY CreatedAt DESC
LIMIT 20
```

---

## 7. Agent 回答规范

Agent 使用数据库工具后，回答应包含：

1. 实际查询了哪张表；
2. 查询使用的核心字段；
3. 查询结果的简要解释；
4. 如果字段含义不确定，应说明“根据字段名初步判断”；
5. 如果样例数据不足以得出结论，应说明限制；
6. 不应编造数据库中没有返回的信息；
7. 不应暴露密码、连接串、内部账户等敏感信息；
8. 对大额金额字段应注意 token 精度，例如 `Decimals` 可能影响金额换算；
9. 对 `Value` 字段的解释应谨慎，必要时结合 `TokenId` 和 `dictionary_tokens`。

---

## 8. Agent 查询流程建议

当用户提出数据库相关问题时，建议按以下流程：

1. 判断问题是否涉及 trx 数据库；
2. 判断问题是查表、查字段、查样例、查地址、查交易哈希还是查 token；
3. 如果没有明确表名，先 `SHOW TABLES FROM trx`；
4. 如果有表名但不确定字段，先 `DESCRIBE TABLE trx.table_name`；
5. 再使用带 `LIMIT` 的 `SELECT` 查询；
6. 根据工具返回结果进行解释；
7. 明确说明查询结果的限制和不确定性。

---

## 9. 推荐测试问题

以下问题可用于测试 Agent 是否正确调用 ClickHouse 工具：

### 9.1 表列表测试

```text
请使用 ClickHouse 工具列出 trx 数据库中你能访问的表，最多展示 30 个，并说明哪些表可能和 TRON 链上交易分析有关。
```

### 9.2 Token 字典测试

```text
请使用 ClickHouse 工具查看 trx.dictionary_tokens 的字段结构，并查询 5 条样例数据，然后解释这张表在链上分析中有什么作用。
```

### 9.3 历史交易表测试

```text
请使用 ClickHouse 工具查看 trx.distributed_histories 的字段结构，并根据字段名解释这张表可能记录了哪些 TRON 链上交易信息。
```

### 9.4 历史交易样例测试

```text
请使用 ClickHouse 工具从 trx.distributed_histories 中查询 5 条样例数据，只选择 Address、Counterpart、Value、TokenId、CreatedAt、BlockNumber、TxHash 这些字段，并解释这些字段在链上分析中的意义。
```

### 9.5 强制工具调用测试

```text
请务必调用 clickhouse_select 工具执行 SQL：SHOW TABLES FROM trx。不要凭经验回答，必须基于工具返回结果列出表名。
```

---

## 10. 后续需要继续补充的信息

当前文档是基于已观察到的 DBeaver 表名和部分字段结构整理的初始版本。后续应继续补充：

- `dictionary_trx_map_address_label` 的完整字段；
- `distributed_histories_trx` 的完整字段；
- `distributed_histories_trc20` 的完整字段；
- `distributed_histories_usdt` 的完整字段；
- `distributed_txs` 的完整字段；
- `distributed_tx_entries` 的完整字段；
- `distributed_smart_contracts` 的完整字段；
- 常见查询样例；
- 地址标签联表查询方式；
- token 精度换算规则；
- 风险分析相关查询模板。

---

## 11. 暂不确定内容说明

以下内容目前仅根据表名和部分字段进行初步判断，后续应以实际 `DESCRIBE TABLE` 和业务确认结果为准：

- 各 `Type`、`Action`、`TxType` 编码的业务含义；
- `Value` 是否需要结合 `Decimals` 换算；
- `TokenId` 与 `dictionary_tokens.Address` 的对应关系；
- `distributed_histories_*` 各专用表与总表的关系；
- `distributed_pending_*` 表的处理状态含义；
- `kafka_consumer_*` 表是否适合 Agent 直接查询。

---

## ClickHouse 多数据源查询说明

当前 Agent 通过统一工具查询多个 ClickHouse 数据源：

```text
clickhouse_list_datasources
clickhouse_select
```

使用原则：

1. 当用户问题可能涉及 ClickHouse 数据，或未明确指定数据源时，应先调用 `clickhouse_list_datasources` 查看可用数据源。
2. 根据数据源的 `id`、`label`、`description`、`databases` 和 `usage_notes` 选择合适的 `datasource_id`。
3. 数据源语义说明由 `config/clickhouse_datasources.json` 维护；如果其中存在 `TODO`，应说明该数据源语义尚待同事补充，不要自行猜测为确定事实。
4. 使用 `clickhouse_select` 时必须传入合适的 `datasource_id`，并先通过 `SHOW DATABASES`、`SHOW TABLES`、`DESCRIBE TABLE` 或 `SHOW CREATE TABLE` 确认库表结构。
5. 查询业务数据必须使用 `LIMIT`。
6. 回答时应说明结果来自哪个 `datasource_id`，不要把某个数据源中的局部结果说成全部链上事实。

