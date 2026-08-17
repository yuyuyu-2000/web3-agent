# 当前后台异步监控通知模块说明

## 1. 模块概述

项目当前已实现一套基于 PostgreSQL、APScheduler 和飞书 Webhook 的后台交易监控通知模块。

实际链路如下：

```text
登录用户通过聊天创建监控规则
→ Agent 调用 create_monitor_rule
→ 权限模块要求用户确认
→ 确认后规则写入 PostgreSQL
→ APScheduler 按全局间隔调用 MonitorWorker
→ Worker 增量读取配置的 PostgreSQL 交易表
→ 新交易匹配全部启用规则
→ 命中事件写入 PostgreSQL
→ 按用户配置的飞书 Webhook 发送消息
```

这里的“后台异步”表示扫描由 FastAPI 进程内的 APScheduler 后台线程执行，不依赖用户保持页面或 HTTP 请求连接。当前没有使用 Celery、Redis Queue 等独立分布式任务队列。

## 2. 代码位置

| 文件 | 当前职责 |
|---|---|
| `src/chaincloud_agent_service/main.py` | 启动时初始化监控存储、Worker 和周期扫描任务 |
| `src/chaincloud_agent_service/config.py` | 读取监控环境变量 |
| `src/chaincloud_agent_service/monitoring/models.py` | 规则、交易记录和通知事件模型 |
| `src/chaincloud_agent_service/monitoring/store.py` | PostgreSQL 持久化和多实例扫描锁 |
| `src/chaincloud_agent_service/monitoring/worker.py` | 增量扫描、规则匹配、事件生成和通知发送 |
| `src/chaincloud_agent_service/monitoring/runtime.py` | 当前存储实例和当前用户上下文 |
| `src/chaincloud_agent_service/tools/monitor_tools.py` | Agent 使用的监控规则工具 |
| `src/chaincloud_agent_service/api/routes/monitoring.py` | 用户飞书 Webhook 配置接口 |
| `src/chaincloud_agent_service/notification/service.py` | 飞书消息组装与发送 |
| `src/chaincloud_agent_service/agent/permission.py` | 规则写操作的权限确认 |
| `docs/sql/init_monitor_tables.sql` | 监控表结构参考 |
| `tests/test_monitor_worker.py` | Worker 单元测试 |

## 3. 启动和调度

FastAPI 进入 `lifespan` 后先启动全局 APScheduler。若 `MONITOR_ENABLED=1`，应用会：

1. 校验监控库和交易库连接；
2. 创建 `MonitorStore` 并执行 `ensure_schema()`；
3. 创建 `PostgresTransactionSource`；
4. 创建 `NotificationService` 和 `MonitorWorker`；
5. 注册固定 ID 为 `chaincloud-monitor-scan` 的 interval Job。

Job 当前配置为：

- `replace_existing=True`；
- `coalesce=True`；
- `max_instances=1`；
- 扫描间隔最小 5 秒。

所有用户和规则共用同一个扫描 Job 和全局扫描间隔。

## 4. 环境变量

```env
# 开启监控
MONITOR_ENABLED=1

# 保存规则、通知事件、扫描游标和通知地址
# 未设置时依次回退到 AUTH_DATABASE_URL、MEMORY_DATABASE_URL
MONITOR_DATABASE_URL=postgresql://user:password@host:5432/agent

# 被扫描交易表所在数据库
# 未设置时回退到 READONLY_DATABASE_URL
MONITOR_TRANSACTION_DATABASE_URL=postgresql://readonly:password@host:5432/business

# 监控表名前缀，默认 monitor
MONITOR_TABLE_PREFIX=monitor

# 全局扫描间隔，单位秒，默认 30；十分钟填写 600
MONITOR_SCAN_INTERVAL_SEC=600

# 交易表名，默认 transactions
MONITOR_TRANSACTION_TABLE=transactions

# 统一字段到实际字段的映射，JSON 字符串
MONITOR_TRANSACTION_COLUMNS={"id":"id","hash":"transaction_hash","from_address":"from_address","to_address":"to_address","amount":"amount","amount_usd":"amount_usd","chain":"chain","token":"token","occurred_at":"created_at"}

# 每轮最多读取条数，默认 1000
MONITOR_SCAN_BATCH_SIZE=1000

# 首次启动是否处理表中已有交易，默认关闭
MONITOR_PROCESS_EXISTING=0
```

开启监控时，`MONITOR_DATABASE_URL` 和 `MONITOR_TRANSACTION_DATABASE_URL` 必须最终解析出有效连接，否则应用启动失败。

## 5. 当前数据库表

默认前缀为 `monitor`，应用启动时自动创建四张表。

### 5.1 `monitor_rules`

保存正式监控规则。主要字段如下：

| 字段 | 说明 |
|---|---|
| `rule_id` | 规则 UUID |
| `user_id` | 所属用户 |
| `rule_type` | `address_transaction` 或 `large_transaction` |
| `address` | 地址过滤条件 |
| `min_amount` | Token 数量下限 |
| `min_amount_usd` | 美元金额下限 |
| `chain` | 链过滤条件 |
| `token` | Token 过滤条件 |
| `enabled` | 是否启用 |
| `notification_channel` | 通知渠道，默认 `feishu` |
| `last_triggered_at` | 最近命中时间 |

### 5.2 `monitor_notification_events`

保存规则命中和通知状态。当前使用的状态为：

```text
pending / failed / sent
```

唯一约束是：

```text
UNIQUE(rule_id, transaction_id)
```

发送失败的事件会在后续扫描中重试，最多尝试 5 次。

### 5.3 `monitor_scan_state`

保存 Worker 最近处理到的单字段游标 `last_processed_id`。默认 Worker 名称为 `transactions`。

### 5.4 `monitor_notification_configs`

按 `user_id + channel` 保存通知目标。飞书场景下 `destination` 是用户的机器人 Webhook URL。

## 6. Agent 监控工具

当 `MONITOR_ENABLED=1` 且 `MONITOR_DATABASE_URL` 有值时，Agent 注册四个工具。

### `create_monitor_rule`

参数：

```text
rule_type
address
min_amount
min_amount_usd
chain
token
notification_channel
```

- `address_transaction` 必须提供 `address`；
- `large_transaction` 必须提供 `min_amount` 或 `min_amount_usd`；
- 创建操作需要前端用户确认。

### `list_monitor_rules`

查询当前登录用户自己的规则，是只读操作。

### `delete_monitor_rule`

删除当前登录用户自己的规则，需要确认。

### `set_monitor_rule_enabled`

启用或禁用当前登录用户自己的规则，需要确认。

## 7. 用户身份和权限确认

监控工具要求真实登录用户身份。

`POST /chat` 验证用户 Token 后将 `user_id` 写入 Agent 状态。Graph 执行工具时用 `ContextVar` 绑定该用户，工具创建和查询的规则因此归属于当前用户。

如果只使用静态 `CHAT_API_TOKEN` 而没有登录用户，工具会报错：

```text
monitor tools require an authenticated user
```

当前创建规则流程为：

1. Agent 计划调用 `create_monitor_rule`；
2. Graph 返回 `permission_required`；
3. 前端展示通用权限确认卡片；
4. 用户点击“确认执行”；
5. 前端调用 `POST /chat/permission`；
6. Graph 恢复执行并写入规则。

确认目前绑定 `step_id + tool_name`。项目当前没有独立监控任务草稿表、草稿版本，也没有专门的反复修订接口。前端展示的是通用工具权限信息，不是完整结构化任务预览。

## 8. 规则匹配

Worker 对每条新交易依次匹配所有启用规则：

1. 设置了 `chain` 时，交易链必须相同；
2. 设置了 `token` 时，交易 Token 必须相同；
3. 设置了 `address` 时，必须等于转入或转出地址；
4. 设置了 `min_amount` 时，要求 `amount >= min_amount`；
5. 设置了 `min_amount_usd` 时，要求 `amount_usd >= min_amount_usd`。

多个已设置条件之间是 AND 关系。

当前限制：

- 比较符固定为“大于等于”；
- 规则没有 `protocol` 字段；
- 扫描协议范围由配置的交易表间接决定；
- 阈值是每条规则自己的值，并非代码中的统一业务阈值。

## 9. 增量扫描

### 首次扫描

默认 `MONITOR_PROCESS_EXISTING=0`。Worker 第一次扫描只查询交易表的最大 ID 并保存为游标，不处理已有交易，以避免首次启动发送大量历史通知。

设置 `MONITOR_PROCESS_EXISTING=1` 后，第一次扫描会从表头开始分批处理已有数据。

### 后续扫描

当前使用单字段键集分页：

```sql
WHERE id > last_cursor
ORDER BY id ASC
LIMIT batch_size
```

每次扫描只处理一批，积压超过批量上限时等待后续轮次继续处理。

### 多实例保护

扫描前使用 PostgreSQL advisory lock。只有获得锁的应用实例执行该轮扫描，其他实例记录跳过状态。

## 10. 通知流程

匹配成功后，Worker 生成以下 payload：

```json
{
  "rule_id": "...",
  "address": "...",
  "transaction_hash": "...",
  "amount": "...",
  "amount_usd": "...",
  "chain": "...",
  "token": "...",
  "triggered_at": "..."
}
```

事件先写入 `monitor_notification_events`，然后 Worker 查询所有满足以下条件的事件：

```text
status IN ('pending', 'failed') AND attempts < 5
```

发送成功后标记为 `sent`；失败后标记为 `failed`、增加尝试次数并保存错误，下一轮继续尝试。

当前飞书文本格式固定包含规则 ID、地址、交易 Hash、Token 数量、USD 金额、链和触发时间。当前不能由每条规则自由选择通知字段。

## 11. 飞书配置接口

```http
PUT /monitor/notification/feishu
Authorization: Bearer <登录用户 Token>
Content-Type: application/json

{
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/..."
}
```

要求：

- 用户已经登录；
- 监控模块已启用；
- URL 以 `https://open.feishu.cn/open-apis/bot/v2/hook/` 开头。

配置按用户保存。该用户的规则命中时，Worker 查询相应 Webhook 并发送消息。

当前前端没有飞书设置页面，Vite 也没有 `/monitor` 开发代理，所以目前需要直接调用后端接口。

## 12. 当前交易表要求

`PostgresTransactionSource` 要求交易表能映射以下全部字段：

| 统一字段 | 默认实际字段 |
|---|---|
| `id` | `id` |
| `hash` | `transaction_hash` |
| `from_address` | `from_address` |
| `to_address` | `to_address` |
| `amount` | `amount` |
| `amount_usd` | `amount_usd` |
| `chain` | `chain` |
| `token` | `token` |
| `occurred_at` | `created_at` |

可以用 `MONITOR_TRANSACTION_COLUMNS` 覆盖实际列名，但所有统一字段目前仍会被查询。

表名和字段名只接受简单标识符：

```text
[A-Za-z_][A-Za-z0-9_]*
```

所以不能将 `public.justlend` 直接配置为表名，只能配置 `justlend` 并依赖数据库 `search_path`。

## 13. 当前 JustLend 适配状态

项目说明中的 `public.justlend` 使用 `tx_seq`、`event_index`、`tx_hash`、`token_symbol` 和 `occurred` 等字段。当前通用交易源尚未为它做专用适配，主要不匹配包括：

- JustLend 没有默认的 `id`、`transaction_hash`、`token` 和 `created_at` 字段名；
- Worker 要求 `chain` 列，而表说明中没有该列；
- `occurred` 是字符串，当前通知代码预期时间对象支持 `isoformat()`；
- 当前游标是单字段，不能表示 `tx_seq + event_index`；
- 单一 `transaction_id` 不能可靠区分同一交易中的多个事件；
- 规则没有 `protocol=justlend` 条件。

因此当前后台扫描与通知框架已经存在，但不能只靠环境变量字段映射可靠完成 JustLend 监控。需要代码适配，或者在数据库提供一张满足当前统一字段要求的视图。

## 14. 当前前端支持范围

已经支持：

- 用户注册、登录和 Token；
- 发送聊天请求；
- 接收 `permission_required`；
- 展示权限确认卡片；
- 点击确认或取消；
- 接收 `clarification_required` 并补充信息。

尚未支持：

- 结构化监控任务预览；
- 确认前反复编辑同一任务草稿；
- 监控规则列表和管理页面；
- 飞书 Webhook 设置页面；
- 扫描记录和通知历史；
- `/monitor` Vite 开发代理。

## 15. 当前能力边界

当前已有：

- 登录用户创建地址或大额交易规则；
- 创建、删除和启停前的通用副作用确认；
- PostgreSQL 规则与事件持久化；
- 全部规则共享固定扫描周期；
- 统一格式 PostgreSQL 交易表的增量扫描；
- 飞书固定格式通知；
- 失败通知最多重试 5 次；
- PostgreSQL 锁降低多实例重复扫描风险。

当前没有：

- 任务草稿和版本化反复修订；
- 每条任务独立扫描频率；
- 每条规则自定义比较符和通知字段；
- 协议字段和多协议适配器；
- JustLend 专用适配和复合游标；
- 独立队列或独立 Worker 服务；
- 完整前端监控管理页面；
- 通知死信队列和指数退避；
- 扫描状态与通知历史查询 API。

## 16. 当前模块验证步骤

1. 配置并开启监控相关环境变量；
2. 确认被扫描表满足统一字段要求；
3. 启动 FastAPI，确认四张监控表自动创建；
4. 注册或登录用户并取得访问 Token；
5. 调用飞书配置接口保存该用户的 Webhook；
6. 前端发送创建规则的明确指令；
7. 在权限卡片点击“确认执行”；
8. 检查 `monitor_rules` 中是否已写入规则；
9. 向交易表插入一条 ID 大于当前游标且满足条件的新记录；
10. 等待一个扫描周期；
11. 检查游标是否前进、通知事件是否生成且变为 `sent`；
12. 检查飞书是否收到消息。

Worker 每轮日志包含：

```text
new_transactions
enabled_rules
matched_rules
notification_success
notification_failure
error
duration_ms
```

这些指标可以定位问题发生在数据扫描、规则匹配还是飞书发送阶段。
