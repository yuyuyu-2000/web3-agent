# Agent PostgreSQL 数据库说明

> 当前版本：可访问 PostgreSQL 业务表说明
> 适用工具：`postgres_select`、`postgres_list_tables`、`postgres_table_schema`

---

## 1. 数据源与覆盖范围

当前 Agent 可访问的链上业务数据只有以下两张 PostgreSQL 表：

- `public.justlend`
- `public.croas_chain`

数据库中的 `agent_memories`、`agent_users` 属于 Agent 自身的记忆和用户管理表，不是链上业务数据，除非用户明确询问这些功能，否则不要查询。

### 重要限制

1. 当前没有可访问的 ClickHouse/TRON 全链数据库，不要调用或建议调用 ClickHouse 工具。
2. 这两张表只覆盖 JustLend 协议事件和跨链业务记录，不代表 TRON 全链交易。
3. 查询结果只能表述为“在当前可访问的 JustLend/跨链数据中……”，不得扩大为“TRON 全链……”。
4. 如果用户询问整个 TRON 链的交易、地址活动或全链统计，应先说明当前数据覆盖不足，然后提供这两张表范围内可以完成的分析。
5. 用户口中的 `cross_chain` 指当前数据库里的 `public.croas_chain`。生成 SQL 时必须使用真实表名 `croas_chain`，不要查询不存在的 `cross_chain`。

---

## 2. `public.justlend`

### 2.1 业务含义

`justlend` 保存 JustLend 协议相关的市场事件，可用于分析协议内的存款、借款、还款、赎回、清算等操作（具体操作含义以 `operation_type` 的实际值为准）。

该表不能用于判断 TRON 全链是否存在某类交易。

### 2.2 字段

| 字段 | PostgreSQL 类型 | 含义/使用注意事项 |
|---|---|---|
| `day` | `varchar` | 事件所属日期；实际格式应先通过小样本确认 |
| `occurred` | `varchar` | 事件发生时间；字符串字段，比较或排序前应确认格式 |
| `ingested_at` | `varchar` | 数据写入时间 |
| `tx_seq` | `bigint` | 交易序号 |
| `market_event_id` | `integer` | 市场事件 ID |
| `event_index` | `integer` | 同一交易内的事件索引 |
| `tx_hash` | `varchar` | 交易哈希 |
| `from_address` | `varchar` | 来源地址 |
| `to_address` | `varchar` | 目标地址 |
| `market_id` | `integer` | JustLend 市场 ID |
| `operation_type` | `varchar` | 操作类型；不要凭经验枚举，必要时先查询实际值 |
| `token_symbol` | `varchar` | Token 符号 |
| `token_address` | `varchar` | Token 合约地址 |
| `amount` | `real` | Token 数量 |
| `price_usd` | `real` | Token 的美元价格 |
| `amount_usd` | `real` | 事件金额的美元价值，优先用于统一口径的大额判断 |
| `updated_at` | `varchar` | 数据更新时间 |

### 2.3 推荐查询方式

按日期查询时优先使用 `day`，但首次使用前应以一次小样本查询确认日期格式：

```sql
SELECT day, occurred
FROM public.justlend
WHERE day IS NOT NULL
LIMIT 5
```

确认格式为 `YYYY-MM-DD` 后，可查询某日的大额事件：

```sql
SELECT
    occurred,
    tx_hash,
    from_address,
    to_address,
    operation_type,
    token_symbol,
    amount,
    price_usd,
    amount_usd
FROM public.justlend
WHERE day = '2026-08-06'
  AND amount_usd >= 1000000
ORDER BY amount_usd DESC
LIMIT 100
```

除非用户给出了其他定义，不能自行假定“大额”的固定阈值。应先询问阈值，或明确声明本次采用的临时阈值。

---

## 3. `public.croas_chain`

### 3.1 业务含义

`croas_chain` 保存跨链协议订单的充值侧和提现侧记录。用户可能把它称为 `cross_chain`，但数据库真实表名是拼写为 `croas_chain`。

它适合分析跨链充值、提现、金额、手续费、处理时长和交易哈希，不是 TRON 全链交易表。记录是否涉及 TRON，需要结合 `deposit_chain_id` 或 `withdraw_chain_id` 的实际取值判断；不要在未验证链 ID 映射时自行认定某个整数代表 TRON。

### 3.2 字段

| 字段 | PostgreSQL 类型 | 含义/使用注意事项 |
|---|---|---|
| `protocol_id` | `integer` | 跨链协议 ID |
| `protocol_order_id` | `varchar` | 协议订单 ID |
| `status` | `integer` | 订单状态；含义需从实际数据或业务定义确认 |
| `deposit_chain_id` | `integer` | 充值侧链 ID；链 ID 映射需验证 |
| `deposit_token_address` | `varchar` | 充值侧 Token 地址 |
| `deposit_amount` | `real` | 充值数量；不同 Token 之间不可直接比较 |
| `deposit_from_address` | `varchar` | 充值发起地址 |
| `deposit_to_address` | `varchar` | 充值接收地址 |
| `deposit_tx_hash` | `varchar` | 充值侧交易哈希 |
| `withdraw_chain_id` | `integer` | 提现侧链 ID；链 ID 映射需验证 |
| `withdraw_token_address` | `varchar` | 提现侧 Token 地址 |
| `withdraw_amount` | `real` | 提现数量；不同 Token 之间不可直接比较 |
| `withdraw_from_address` | `varchar` | 提现发起地址 |
| `withdraw_to_address` | `varchar` | 提现接收地址 |
| `withdraw_tx_hash` | `varchar` | 提现侧交易哈希 |
| `deposit_fee` | `real` | 充值侧手续费 |
| `deposit_fee_token_address` | `varchar` | 充值手续费 Token 地址 |
| `withdraw_fee` | `varchar` | 提现侧手续费；该字段是字符串，数值计算前需验证并转换 |
| `withdraw_fee_token_address` | `varchar` | 提现手续费 Token 地址 |
| `deposit_chain_time` | `varchar` | 充值侧链上时间；格式需先确认 |
| `withdraw_chain_time` | `varchar` | 提现侧链上时间；格式需先确认 |
| `duration` | `integer` | 跨链处理时长；单位需由业务定义确认 |
| `created_at` | `varchar` | 记录创建时间 |
| `updated_at` | `varchar` | 记录更新时间 |

### 3.3 查询注意事项

- 表中没有美元价格或美元金额字段。不能仅凭 `deposit_amount`/`withdraw_amount` 跨 Token 判断美元口径的大额交易。
- 若用户按 Token 原始数量定义大额，可以在明确 Token 地址、链和阈值后查询。
- 日期字段均为字符串。首次查询前先查看少量非空值，确认格式后再构造日期条件。
- 判断 TRON 相关记录前，必须先确认链 ID 映射。可以查询链 ID 分布，但不能仅凭猜测解释链名称。

用于确认链 ID、Token 和时间格式的小样本：

```sql
SELECT
    deposit_chain_id,
    withdraw_chain_id,
    deposit_token_address,
    withdraw_token_address,
    deposit_chain_time,
    withdraw_chain_time
FROM public.croas_chain
LIMIT 10
```

---

## 4. Agent 查询规则

### 4.1 优先使用已知结构

本文档已经给出两张业务表的字段。通常应直接生成针对目标表的 `SELECT`，不要每次都重复执行以下元数据查询：

- `postgres_list_tables`
- `information_schema.tables`
- `postgres_table_schema`

只有在 SQL 报字段/表结构错误，或用户明确要求查看数据库结构时，才重新探查 schema。

### 4.2 控制工具调用预算

一次分析应优先遵循：

1. 根据用户问题选择 `justlend`、`croas_chain` 或两者；
2. 只有字符串日期格式、链 ID、状态值等确实未知时，执行一次小样本或 `DISTINCT` 查询；
3. 随后直接执行目标聚合或明细查询；
4. 避免并行请求多个不必要的 schema 查询；
5. 基于工具真实结果回答，不得把未执行的查询说成“未查到数据”。

### 4.3 “TRON 大额交易”问题

当用户询问某日 TRON 链大额交易时：

1. 先说明当前数据库不是 TRON 全链数据，只覆盖 JustLend 和跨链记录；
2. 确认“大额”的资产和阈值；若用户未提供，可提出澄清，或在明确披露假设后采用临时阈值；
3. `justlend` 优先以 `amount_usd` 判断统一美元口径金额；
4. `croas_chain` 没有美元金额，必须结合 Token、链 ID 和用户指定的原始数量阈值，不能跨 Token 直接比较；
5. 最终结论必须分别列出两张表的覆盖范围、查询条件和结果；
6. 结论使用“当前可访问数据中发现/未发现”，不得使用“TRON 全链存在/不存在”。

### 4.4 可靠性边界

- `real` 是浮点类型，金额筛选和展示可能存在精度误差。
- 多个时间字段为 `varchar`，没有确认格式前不要直接强制转换，以免整条查询失败。
- Token 符号可能重名；精确分析优先使用 Token 地址。
- `NULL` 不等于零，聚合时应明确是否使用 `COALESCE`。
- 查询结果最多返回工具允许的行数；明细查询应使用 `ORDER BY` 和 `LIMIT`。
- 没有结果可能表示没有匹配记录、数据覆盖不完整或条件/格式不匹配，回答时应区分这些可能性。
