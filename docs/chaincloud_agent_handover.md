# ChainCloud-AI 项目交接文档

> 交接时间：2026-06-10  
> 当前分支：`feat/clickhouse-multi-datasource`  
> 当前阶段：实习阶段主要功能已基本完成，本次改动重点为 ClickHouse 多数据源工具与后续数据源语义说明接口。

---

## 1. 当前项目整体状态

ChainCloud-AI 当前已经具备一个基础 Agent 平台形态，主要包括：

1. 用户登录与认证流程；
2. 用户级长期记忆隔离；
3. 工具注册与 Agent 工具调用；
4. Web Search 公开资料检索；
5. PostgreSQL / ClickHouse / 链上 RPC 等数据查询能力；
6. Answer Composer 回答编排层；
7. 前端 Markdown 渲染、表格展示、地址/哈希复制等展示优化；
8. ClickHouse 多数据源查询能力与数据源语义说明接口。

目前项目已经从“单一工具演示”逐步过渡到“可根据用户问题调用多类工具、组合公开资料和内部数据源进行回答”的 Agent 应用原型。

---

## 2. 已完成的核心功能

### 2.1 Web Search 工具

已接入公开信息检索工具，支持 Agent 在需要外部公开资料时调用搜索能力，并将搜索结果纳入回答上下文。

主要价值：

- 支持查询最新公开资料；
- 可用于事件背景、媒体报道、协议公告等信息补充；
- 与 Answer Composer 配合后，可以在最终回答中区分“公开资料支持”和“模型推测”。

### 2.2 用户级 Memory 隔离

已完成用户级 memory 隔离，避免不同登录用户之间共享或误读记忆数据。

主要价值：

- 登录用户拥有独立 memory_key；
- 前端只展示当前用户可见的 memory；
- 后端对 memory 操作增加用户维度隔离；
- 为后续多用户 Agent 平台化使用打基础。

### 2.3 Answer Composer 回答编排层

已新增 `src/chaincloud_agent_service/agent/answer_composer/` 模块，用于在 Agent 完成工具调用和原始分析后，对最终回答进行统一编排。

主要能力：

- 简单问题走轻量化回答；
- 复杂问题走结构化回答；
- 回答开头亮点先行；
- 区分公开资料、公司数据库、链上 RPC、用户提供信息、模型推测、待验证信息；
- 避免把不同证据等级和不同资金流口径混在一起；
- 避免输出“根据上下文/原始草稿整理”等内部过程描述。

相关文件：

```text
src/chaincloud_agent_service/agent/answer_composer/
src/chaincloud_agent_service/agent/graph.py
tests/test_answer_composer.py
```

### 2.4 前端 Markdown 渲染优化

前端已增强 Agent 回复的 Markdown 展示能力。

当前支持：

- 标题渲染；
- 无序列表 / 有序列表；
- 表格渲染；
- 行内代码；
- 代码块；
- 链接点击；
- 地址 / 哈希高亮复制。

相关文件：

```text
frontend/chaincloud-agent-web/src/App.tsx
frontend/chaincloud-agent-web/src/styles.css
```

---

## 3. 本次最终改动：ClickHouse 多数据源工具

### 3.1 改动背景

上级反馈当前 Agent 无法查询 DBeaver 中 `10.8.6.153:5887` 数据源中的内容。原项目中 ClickHouse 查询工具只支持单一数据源，主要通过以下环境变量配置：

```env
READONLY_CLICKHOUSE_HOST
READONLY_CLICKHOUSE_PORT
READONLY_CLICKHOUSE_USER
READONLY_CLICKHOUSE_PASSWORD
READONLY_CLICKHOUSE_DATABASE
READONLY_CLICKHOUSE_SECURE
```

这种方式只能让 Agent 看到一个默认 ClickHouse 数据源，不适合后续接入多个数据库端口和多个业务数据源。

### 3.2 当前方案

本次将 ClickHouse 查询能力改造为统一多数据源模式：

```text
clickhouse_list_datasources
clickhouse_select
```

其中：

- `clickhouse_list_datasources`：列出当前 Agent 可用的 ClickHouse 数据源、默认库、业务说明和使用提示；
- `clickhouse_select`：通过 `datasource_id` 指定数据源，并执行只读 SQL。

示例调用语义：

```json
{
  "datasource_id": "analytics_5887",
  "sql": "SHOW DATABASES"
}
```

### 3.3 数据源语义说明接口

新增配置文件：

```text
config/clickhouse_datasources.json
```

这个文件用于维护各个 ClickHouse 数据源的公开元信息和业务语义说明，方便后续同事补充。

当前预留字段包括：

```json
{
  "id": "analytics_5887",
  "label": "ClickHouse 5887 数据源",
  "host": "10.8.6.153",
  "port": 5887,
  "default_database": "default",
  "description": "TODO: 请同事补充该数据源的数据内容说明、覆盖范围和适用问题。",
  "databases": [
    {
      "name": "default",
      "description": "TODO: 请补充 default database 中主要表的含义。"
    }
  ],
  "usage_notes": [
    "TODO: 请补充该数据源适合回答的问题类型。",
    "查询业务数据前先 SHOW DATABASES / SHOW TABLES / DESCRIBE TABLE。",
    "不要把 5887 数据源中的局部结果说成全部链上事实。"
  ]
}
```

注意：

- 该 JSON 文件不存放密码；
- 密码通过环境变量注入；
- 后续同事只需要维护 `description`、`databases.description`、`usage_notes` 等语义信息；
- Agent 会根据这些说明选择合适的数据源，而不是自行猜测数据库内容。

### 3.4 凭据配置方式

`.env.example` 中已增加多数据源凭据示例：

```env
CLICKHOUSE_DATASOURCES_CONFIG_PATH=config/clickhouse_datasources.json

CLICKHOUSE_TRX_5886_USER=test
CLICKHOUSE_TRX_5886_PASSWORD=<CLICKHOUSE_TRX_5886_PASSWORD>

CLICKHOUSE_ANALYTICS_5887_USER=test
CLICKHOUSE_ANALYTICS_5887_PASSWORD=<CLICKHOUSE_ANALYTICS_5887_PASSWORD>
```

本地 `.env` 中需要填入真实密码。不要把真实密码提交到仓库。

### 3.5 只读安全限制

当前 ClickHouse 工具仍然保留只读约束：

- 允许 `SELECT` / `WITH`；
- 允许 `SHOW` / `DESCRIBE` / `DESC`；
- 禁止多条 SQL；
- 禁止写操作；
- 工具最多返回 500 行；
- 查询业务数据时应使用 `LIMIT`。

---

## 4. 后续同事接手建议

### 4.1 优先补充数据源语义说明

请后续接手同事优先完善：

```text
config/clickhouse_datasources.json
```

重点补充：

1. 每个数据源代表什么业务数据；
2. 每个 database 的含义；
3. 核心表名和字段说明；
4. 适合回答的问题类型；
5. 哪些结论可以直接基于该数据源给出；
6. 哪些结果只能作为局部样例或待验证信息。

这一步很重要。Agent 是否能正确选择数据源，主要依赖这个文件中的说明。

### 4.2 建议新增数据源文档

后续可以进一步拆出更细的文档，例如：

```text
docs/clickhouse_datasources.md
docs/database_semantics.md
```

用于给非开发同事维护数据库业务语义。

### 4.3 建议补充真实连通性验证脚本

当前单元测试不连接真实 ClickHouse，因为 CI 环境未必能访问公司内网数据库。后续可以新增一个本地 smoke test，例如：

```text
scripts/smoke_clickhouse_datasources.py
```

用于手动验证：

1. 是否能列出数据源；
2. 是否能连接 5886；
3. 是否能连接 5887；
4. `SHOW DATABASES` 是否成功；
5. `SHOW TABLES` 是否成功。

### 4.4 建议继续建设 Evidence Verifier

Answer Composer 目前是回答编排层，不是严格事实核验器。后续如果要进一步提高金融 / 链上分析类回答可信度，建议新增：

```text
Evidence Verifier / Claim Checker
```

目标：

- 对金额、地址、时间、交易哈希做来源校验；
- 要求关键结论必须绑定工具结果；
- 对未验证信息强制标注“待验证”；
- 避免模型根据公开报道和数据库结果自行补全过多细节。

### 4.5 建议在 trace 中标记最终回答编排阶段

当前 Agent 已经有 trace 相关能力，后续可以在 trace 中显式标记：

```text
agent_raw_answer
answer_composer_input
answer_composer_output
selected_datasource_id
```

这样调试时可以更清楚地看到：

1. Agent 原始回答是什么；
2. 工具查询了哪些数据源；
3. Composer 如何改写最终答案；
4. 是否正确使用了 5887 等新数据源。

---

## 5. 常用验证命令

后端编译检查：

```bash
uv run python -m compileall src/chaincloud_agent_service
```

Answer Composer 测试：

```bash
uv run --with pytest pytest tests/test_answer_composer.py
```

ClickHouse 多数据源测试：

```bash
uv run --with pytest pytest tests/test_clickhouse_multi_datasource.py
```

联合测试：

```bash
uv run --with pytest pytest tests/test_clickhouse_multi_datasource.py tests/test_answer_composer.py
```

前端 TypeScript 检查：

```bash
pnpm --dir frontend/chaincloud-agent-web exec tsc --noEmit
```

---

## 6. 本地真实 ClickHouse 连通性验证示例

在 `.env` 中配置真实密码后，可运行：

```bash
uv run python - <<'PY'
from chaincloud_agent_service.config import load_settings
from chaincloud_agent_service.tools.clickhouse_select import (
    load_clickhouse_datasources,
    make_clickhouse_list_datasources_tool,
    make_clickhouse_select_tool,
)

settings = load_settings()
datasources = load_clickhouse_datasources(settings)

list_tool = make_clickhouse_list_datasources_tool(datasources)
select_tool = make_clickhouse_select_tool(datasources=datasources)

print("=== datasources ===")
print(list_tool.invoke({}))

print("=== 5887 databases ===")
print(select_tool.invoke({
    "datasource_id": "analytics_5887",
    "sql": "SHOW DATABASES"
}))
PY
```

---

## 7. 当前分支改动范围

本次 ClickHouse 多数据源改动主要涉及：

```text
.env.example
config/agent_database_schema.md
config/clickhouse_datasources.json
src/chaincloud_agent_service/config.py
src/chaincloud_agent_service/tools/clickhouse_select.py
src/chaincloud_agent_service/tools/registry.py
tests/test_clickhouse_multi_datasource.py
```

当前提交：

```text
feat(tools): support clickhouse multi datasource
```

---

## 8. 交接总结

当前项目已经完成了从基础 Agent 到多工具、多数据源、结构化回答的关键升级。后续最重要的工作不再是单纯增加代码，而是补齐“数据源语义”和“事实核验链路”。

建议后续接手优先顺序：

1. 完善 `config/clickhouse_datasources.json` 中每个数据源的说明；
2. 使用真实 `.env` 密码验证 5886 / 5887 连通性；
3. 让 Agent 在实际对话中测试 5887 数据源查询；
4. 补充本地 smoke test；
5. 建设 Evidence Verifier / Claim Checker；
6. 进一步完善 trace 与前端展示体验。

