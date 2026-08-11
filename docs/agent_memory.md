# Agent Memory 设计与使用说明

本文档说明 ChainCloud-AI 中 Memory v1 的设计目标、接口用法、内部结构与当前限制。

## 1. 背景与目标

Memory v1 的目标是完成一个清晰、可测试的长期记忆闭环：

```text
/chat 产生 thread 历史
→ /memory/summarize 根据 thread_id 生成摘要
→ 保存为 memory_key
→ /chat 携带 memory_key
→ 将保存的 memory 作为 SystemMessage 注入当前对话
```

该能力用于解决长对话中重要背景信息难以持续保留的问题。当前版本优先追求低耦合、可测试和易扩展，而不是一次性实现复杂的向量检索、自动事实抽取或数据库持久化。

## 2. 当前功能范围

Memory v1 当前支持：

- 手动保存 memory；
- 查询单条 memory；
- 列出所有 memory；
- 删除 memory；
- 根据 thread_id 的 checkpoint 历史生成摘要 memory；
- 在 `/chat` 请求中通过 `memory_key` 注入已保存的 memory；
- 提供 store、service、routes、chat injection 对应测试。

当前暂不支持：

- 向量检索；
- 自动事实抽取；
- 数据库持久化；
- 多租户隔离；
- 自动每 N 轮总结；
- graph 内部自动 memory retrieve / summarize 节点。

## 3. 模块结构

```text
src/chaincloud_agent_service/memory/
├── __init__.py
├── models.py
├── store.py
└── service.py

src/chaincloud_agent_service/api/
├── auth.py
└── routes/
    ├── chat.py
    └── memory.py

tests/
├── test_memory_store.py
├── test_memory_service.py
├── test_memory_routes.py
├── test_memory_summarize_route.py
└── test_chat_memory_injection.py
```

各模块职责如下：

| 文件 | 职责 |
| --- | --- |
| `memory/models.py` | 定义 `MemoryRecord` 数据结构 |
| `memory/store.py` | 提供 `InMemoryMemoryStore` 内存存储 |
| `memory/service.py` | 负责 transcript 构造、摘要 prompt、memory 注入消息构造 |
| `api/routes/memory.py` | 提供 `/memory` 相关 HTTP 接口 |
| `api/routes/chat.py` | 支持 chat 请求中的 `memory_key` 注入 |
| `api/auth.py` | 提供共享 Bearer Token 鉴权逻辑 |
| `main.py` | 初始化 `MemoryService`、`memory_llm` 并挂载 memory 路由 |

## 4. MemoryRecord 数据结构

`MemoryRecord` 是 Memory v1 的核心数据结构，示例如下：

```json
{
  "memory_key": "chaincloud-memory-demo",
  "summary": "用户正在按小步提交和测试优先的方式重构 ChainCloud Memory v1。",
  "source_thread_id": "memory-demo-thread",
  "metadata": {
    "stage": "manual-test",
    "summary_source": "thread_checkpoint"
  },
  "updated_at": "2026-04-27T00:00:00+00:00"
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `memory_key` | memory 的唯一键，后续 `/chat` 可通过它引用 |
| `summary` | 长期记忆摘要文本 |
| `source_thread_id` | 该 memory 的来源 thread_id |
| `metadata` | 附加元数据 |
| `updated_at` | 最后更新时间 |

## 5. HTTP 接口

### 5.1 手动保存 memory

```bash
curl -X POST http://127.0.0.1:8000/memory \
  -H "Content-Type: application/json" \
  -d '{
    "memory_key": "chaincloud-memory-demo",
    "summary": "用户正在按小步提交方式重构 ChainCloud Memory v1。",
    "source_thread_id": "memory-demo-thread",
    "metadata": {
      "stage": "manual-save"
    }
  }'
```

该接口会直接保存一条 memory，适合手动写入已知的长期背景信息。

### 5.2 查询单条 memory

```bash
curl http://127.0.0.1:8000/memory/chaincloud-memory-demo
```

如果存在，会返回对应的 `MemoryRecord`；如果不存在，会返回 404。

### 5.3 列出所有 memory

```bash
curl http://127.0.0.1:8000/memory
```

返回结构示例：

```json
{
  "memories": [
    {
      "memory_key": "chaincloud-memory-demo",
      "summary": "用户正在按小步提交方式重构 ChainCloud Memory v1。",
      "source_thread_id": "memory-demo-thread",
      "metadata": {
        "stage": "manual-save"
      },
      "updated_at": "2026-04-27T00:00:00+00:00"
    }
  ]
}
```

### 5.4 删除 memory

```bash
curl -X DELETE http://127.0.0.1:8000/memory/chaincloud-memory-demo
```

删除成功时返回 204；如果 memory 不存在，返回 404。

### 5.5 根据 thread_id 自动生成摘要 memory

先通过 `/chat` 产生一段历史对话：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "memory-demo-thread",
    "message": "我正在做 ChainCloud-AI 的 Memory v1 重构，希望后续回答都按小步提交、测试优先的方式带我做。"
  }'
```

然后调用 `/memory/summarize`：

```bash
curl -X POST http://127.0.0.1:8000/memory/summarize \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "memory-demo-thread",
    "memory_key": "chaincloud-memory-demo",
    "metadata": {
      "stage": "summarize-test"
    }
  }'
```

该接口会完成以下步骤：

1. 根据 `thread_id` 从 graph checkpoint 中读取历史 messages；
2. 将最近若干条 messages 转换为 transcript；
3. 使用 `memory_llm` 生成长期记忆摘要；
4. 保存为 `memory_key` 对应的 `MemoryRecord`。

### 5.6 在 /chat 中注入 memory

当 memory 已经保存后，可以在 `/chat` 请求中携带 `memory_key`：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "memory-demo-followup-thread",
    "memory_key": "chaincloud-memory-demo",
    "message": "请根据之前的项目进展，告诉我下一步应该做什么。"
  }'
```

内部会将 memory 转换为 `SystemMessage`，放在用户本轮 `HumanMessage` 之前：

```text
SystemMessage: 以下是该用户或该任务此前沉淀的长期记忆摘要...
HumanMessage: 请根据之前的项目进展，告诉我下一步应该做什么。
```

如果 `memory_key` 不存在，接口返回 404：

```json
{
  "detail": "memory not found"
}
```

## 6. 鉴权说明

Memory 路由复用 `/chat` 的 Bearer Token 鉴权逻辑。

如果 `settings.chat_api_token` 没有配置，则不启用鉴权。

如果配置了 `chat_api_token`，请求需要携带：

```bash
-H "Authorization: Bearer <token>"
```

否则会返回 401。

## 7. 测试

Memory v1 相关测试包括：

```bash
uv run pytest tests/test_memory_store.py \
              tests/test_memory_service.py \
              tests/test_memory_routes.py \
              tests/test_memory_summarize_route.py \
              tests/test_chat_memory_injection.py -q
```

建议合并前再运行一次全量测试：

```bash
uv run pytest -q
```

## 8. 当前实现限制

当前 Memory v1 使用 `InMemoryMemoryStore`，因此 memory 只保存在当前进程内：

- 服务重启后 memory 会丢失；
- 多进程部署时不同 worker 之间不共享 memory；
- 不适合作为生产环境长期存储。

这个设计是有意为之。Memory v1 的目标是先完成可运行、可测试、低耦合的工程闭环。后续可以在不改变上层接口的前提下，将 store 替换为数据库版本。

## 9. 后续可扩展方向

后续可以继续做：

1. 增加 `PostgresMemoryStore`，实现持久化；
2. 支持自动每 N 轮总结；
3. 支持 memory 自动合并和覆盖策略；
4. 支持 `user_id` / `tenant_id` 隔离；
5. 引入向量检索或关键词检索；
6. 在 graph 内部加入 memory retrieve / summarize 节点；
7. 增加 memory 的来源 trace 和审计字段。

