# Agent Memory PostgreSQL Persistence

本文档说明如何将 Agent Memory 从进程内存模式切换为 PostgreSQL 持久化模式。

## 1. 设计目标

Memory v1 已支持手动保存记忆、从 thread checkpoint 生成摘要，并通过 `memory_key` 注入 `/chat`。本次改造将存储层抽象为 `MemoryStore`，使业务层不直接依赖具体存储实现。

当前支持两种后端：

| backend | 说明 |
| --- | --- |
| `memory` | 默认内存模式，适合本地开发和单元测试，服务重启后数据丢失 |
| `postgres` | PostgreSQL 持久化模式，适合共享环境和生产部署 |

## 2. 本地默认模式

默认不需要 PostgreSQL，`.env` 中保持：

```env
MEMORY_STORE_BACKEND=memory
```

启动服务：

```bash
uv run uvicorn chaincloud_agent_service.main:app --reload --host 0.0.0.0 --port 8001
```

## 3. PostgreSQL 模式配置

拿到数据库权限后，在 `.env` 中配置：

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://user:password@host:5432/database
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

说明：

- `MEMORY_DATABASE_URL` 是 memory 独立使用的数据库连接，不必和 `DATABASE_URL` 或 `READONLY_DATABASE_URL` 相同。
- `MEMORY_POSTGRES_TABLE` 默认是 `agent_memories`。
- `MEMORY_POSTGRES_AUTO_CREATE=1` 时服务启动会自动建表；生产环境更推荐设为 `0`，手动执行 SQL 建表。

## 4. 建表

推荐由有权限的开发者或 DBA 执行：

```bash
psql "$MEMORY_DATABASE_URL" -f docs/sql/init_memory_tables.sql
```

如果不能在本地执行 `psql`，可以把 `docs/sql/init_memory_tables.sql` 交给数据库负责人执行。

## 5. 接口验证

启动后端后，先保存一条 memory：

```bash
curl -X POST http://127.0.0.1:8001/memory \
  -H "Content-Type: application/json" \
  -d '{
    "memory_key": "demo-memory",
    "summary": "用户正在测试 PostgreSQL 持久化 memory。",
    "source_thread_id": "manual-test",
    "metadata": {"source": "curl"}
  }'
```

读取 memory：

```bash
curl http://127.0.0.1:8001/memory/demo-memory
```

重启后端后再次读取：

```bash
curl http://127.0.0.1:8001/memory/demo-memory
```

如果仍能读取，说明 PostgreSQL 持久化成功。

## 6. 在聊天中使用 memory

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "请根据之前的记忆继续回答。",
    "thread_id": "demo-thread",
    "memory_key": "demo-memory"
  }'
```

## 7. 常见问题

### 启动时报 `MEMORY_DATABASE_URL is required`

说明配置了：

```env
MEMORY_STORE_BACKEND=postgres
```

但没有填写：

```env
MEMORY_DATABASE_URL=
```

### 查询时报 relation does not exist

说明还没有建表。执行：

```bash
psql "$MEMORY_DATABASE_URL" -f docs/sql/init_memory_tables.sql
```

或临时设置：

```env
MEMORY_POSTGRES_AUTO_CREATE=1
```

### 不希望本地依赖数据库

使用默认配置：

```env
MEMORY_STORE_BACKEND=memory
```

## 8. 测试

无数据库权限时可以运行默认单元测试：

```bash
uv run pytest tests/test_memory_store.py \
              tests/test_memory_service.py \
              tests/test_memory_routes.py \
              tests/test_memory_summarize_route.py \
              tests/test_chat_memory_injection.py \
              tests/test_memory_factory.py -q
```

完整测试：

```bash
uv run pytest -q
```
