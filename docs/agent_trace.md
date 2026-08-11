# Agent Trace

## 目的

在不改变 `/chat` 默认行为的前提下，为调试、评估和开发场景提供结构化工具调用 trace。

## 接口行为

### 普通请求

请求：

```json
{
  "thread_id": "t1",
  "message": "你好"
}
```
返回：
```
{
  "reply": "..."
}
```
调试请求

请求：
```
{
  "thread_id": "t1",
  "message": "请调用 ethereum_jsonrpc 查询交易",
  "debug": true
}
```
返回：
```
{
  "reply": "...",
  "trace": [
    {
      "step": 1,
      "message_index": 1,
      "type": "tool_call_request",
      "tool": "ethereum_jsonrpc",
      "tool_call_id": "call_xxx",
      "status": "requested",
      "args_preview": "..."
    },
    {
      "step": 2,
      "message_index": 2,
      "type": "tool_result",
      "tool": "ethereum_jsonrpc",
      "tool_call_id": "call_xxx",
      "status": "completed",
      "content_preview": "..."
    }
  ]
}
```
### trace 字段说明
tool_call_request：模型请求调用工具
tool_result：工具执行后的返回结果
invalid_tool_call：工具调用请求格式不合法或被判定无效
### 安全设计
debug 默认关闭
trace 中的敏感字段会做脱敏处理
trace 内容会按最大长度截断，避免过长输出
## 当前适用范围

### 当前 trace 主要面向：

开发调试
Agent 能力评估
工具调用验证
后续自动化测试

### 后续可扩展：

节点耗时
异常类型
trace 入库
前端展示

---

## 2. 提交文档

先看 diff：

```bash
git diff -- docs/agent_trace.md
```
然后提交：
```
git add docs/agent_trace.md
git commit -m "docs(trace): document debug trace usage and response format"
```