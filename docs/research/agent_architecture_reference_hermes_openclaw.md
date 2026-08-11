# Agent 架构参考调研：Hermes / OpenClaw 与 ChainCloud-AI 后续升级方向

> 文档状态：v0.2 增强版  
> 当前阶段：架构调研 + 目标架构设计 + MVP 路线规划  
> 适用项目：ChainCloud-AI / chaincloud-agent-service  
> 主参考对象：Hermes Agent、OpenClaw  
> 补充参考对象：OpenClaude  
> 本文目标：将 ChainCloud-AI 从当前可演示 MVP，进一步规划为一个对标 Hermes / OpenClaw、但面向链上数据分析和公司业务场景的 AI Agent 平台。

---

## 1. 调研背景与目标

当前 ChainCloud-AI 已经完成并合入 main 的能力包括：

- React / Vite Web Console；
- PostgreSQL-backed 用户注册登录；
- 长期 memory 持久化；
- 普通 thread checkpoint 持久化；
- Mac / Windows Docker PostgreSQL 本地部署；
- Windows ZIP 部署测试；
- 前端 memory not found 修复；
- Profile 选择、Chat 对话、Memory 列表、会话总结、Trace 展示等基础交互能力。

这些能力说明项目已经从单纯的后端 API Demo，逐步演进为一个可以本地部署、可以注册登录、可以保存记忆、可以通过前端进行交互的 AI Agent MVP。

但是，从整体架构看，当前项目仍然偏 MVP 和功能堆叠，Agent Runtime、Tool Registry、Profile 权限、Workspace / Thread / Memory 统一建模、Trace 持久化、安全边界、任务调度、多入口接入等能力还没有形成完整的系统架构。

因此，本次调研的目标不是直接照搬 Hermes 或 OpenClaw 的代码，而是从它们的整体架构中提炼可借鉴的设计思想，并结合 ChainCloud-AI 当前已有功能，提出后续可落地的 AI Agent 架构升级方向。

更进一步地，ChainCloud-AI 后续可以被规划为一个“面向链上数据分析场景的 AI Agent Gateway + Workspace 平台”。它可以参考 Hermes / OpenClaw 的整体逻辑，但不需要与它们完全一致。ChainCloud-AI 的差异化方向应当体现在：

- 更强的链上数据分析能力；
- 更清晰的用户、Workspace、Thread、Memory、Tool Trace 资源模型；
- 更适合企业内部使用的权限控制和审计能力；
- 更面向业务场景的 Profile / Agent Mode 设计；
- 更适合公司数据库、链上 RPC、风控分析工具接入的 Tool Registry；
- 更容易演示和部署的 Web Console。

---

## 2. 调研对象确认

### 2.1 Hermes Agent

Hermes Agent 是一个偏通用型、长期运行、自我改进的 AI Agent 项目。它强调长期记忆、技能沉淀、跨会话上下文、工具调用、多平台入口、定时任务、子代理以及模型提供商切换等能力。

Hermes 对 ChainCloud-AI 的参考价值主要体现在：

- Agent Runtime 如何组织；
- CLI / Gateway / API / Scheduler 等多个入口如何复用同一个 Agent Core；
- Tool Registry 如何注册、发现和调用工具；
- Memory / Session 如何持久化；
- Profile 如何隔离配置、记忆和会话；
- Trace / 工具调用过程如何展示；
- Scheduler / Cron / 自动化任务如何与 Agent 结合。

### 2.2 OpenClaw

OpenClaw 是一个 personal AI assistant 项目，强调 local-first、always-on、多渠道消息入口、Gateway 控制平面、Workspace、Memory、Skills、Tools、Sandbox 和多 Agent 路由等能力。

与 OpenClaude 相比，OpenClaw 和 Hermes 更相似。二者都更接近“长期运行的通用 AI Agent / Personal Assistant 平台”，而不是单纯的终端 Coding Agent。

OpenClaw 对 ChainCloud-AI 的参考价值主要体现在：

- Gateway 作为统一控制平面；
- Workspace 作为 Agent 的工作目录、上下文和记忆载体；
- Memory 作为显式、可查看、可管理的持久化知识；
- Skills / Tools 的插件化扩展方式；
- 多渠道入口与消息路由；
- 多 Agent / 多 Workspace 隔离；
- 安全边界、沙箱、工具权限控制；
- 本地优先和可部署性设计。

### 2.3 OpenClaude 的补充参考价值

OpenClaude 更偏 Claude Code / Codex CLI 这一类终端 Coding Agent，重点在命令行交互、多模型 Provider Profile、MCP、文件编辑、代码搜索、bash 工具、Web 工具和 VS Code 集成。

因此，OpenClaude 可以作为补充参考对象，但不作为本次主线。它适合参考：

- CLI Coding Agent 的交互方式；
- Provider Profile 的配置方式；
- 文件编辑和命令执行工具；
- MCP 工具接入；
- 开发者工作流集成。

### 2.4 调研对象选择结论

综合项目定位、架构形态和 ChainCloud-AI 后续演进方向，本次调研将 Hermes 和 OpenClaw 作为主参考对象，OpenClaude 作为补充参考对象。

| 项目 | 产品定位 | 与 ChainCloud-AI 的相关性 | 本次调研角色 |
|---|---|---|---|
| Hermes | 通用型、自我改进的 AI Agent，强调 memory、skills、gateway、scheduler、profile isolation | 高 | 主参考对象 |
| OpenClaw | Local-first personal AI assistant，强调 gateway、workspace、memory、skills、tools、sandbox、多渠道入口 | 高 | 主参考对象 |
| OpenClaude | 终端 Coding Agent，强调 CLI、代码编辑、provider profiles、MCP、文件工具和命令执行 | 中 | 补充参考对象 |

选择 Hermes 和 OpenClaw 作为主参考对象的原因是：二者都更接近长期运行的 AI Agent / Personal Assistant 系统，而不是单纯的代码助手。ChainCloud-AI 当前已经具备用户登录、memory、thread checkpoint、Web Console 和本地部署能力，后续更需要参考这类完整 Agent 产品的架构方式，而不仅仅是参考终端 Coding Agent 的交互方式。

---

## 3. 资料来源

### 3.1 Hermes Agent

- Hermes Agent GitHub README  
  https://github.com/nousresearch/hermes-agent

- Hermes Agent Architecture 文档  
  https://hermes-agent.nousresearch.com/docs/developer-guide/architecture

- Hermes Agent Session Storage 文档  
  https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage

### 3.2 OpenClaw

- OpenClaw GitHub README  
  https://github.com/openclaw/openclaw

- OpenClaw Gateway / Security / Sandboxing 文档  
  https://docs.openclaw.ai/gateway/sandboxing

- OpenClaw Agent Workspace 文档  
  https://docs.openclaw.ai/concepts/agent-workspace

- OpenClaw Memory 文档  
  https://docs.openclaw.ai/concepts/memory

- OpenClaw Skills 文档  
  https://docs.openclaw.ai/tools/skills

### 3.3 ChainCloud-AI 当前项目资料

- 当前 main 分支代码；
- docs/demo/；
- docs/sql/；
- frontend/chaincloud-agent-web；
- 已完成的用户登录、memory、thread checkpoint、本地部署和验收文档。

---

## 4. ChainCloud-AI 当前架构基线

### 4.1 已完成能力

当前项目已经完成：

- React / Vite Web Console；
- Profile 选择；
- Chat 对话；
- Trace 展示；
- 工具状态展示；
- 用户注册 / 登录 / 退出；
- 登录态恢复；
- PostgreSQL-backed 用户表；
- PostgreSQL-backed 长期 memory；
- thread checkpoint 持久化；
- Mac / Windows Docker PostgreSQL 本地部署；
- Windows ZIP 部署测试；
- 前端 memory not found 修复。

### 4.2 当前架构特点

当前项目的优点：

- 已经具备可演示的前后端闭环；
- 已经具备基础用户系统；
- 已经具备长期记忆和普通会话 checkpoint；
- 已经具备本地部署和跨平台验证；
- 已经具备继续架构升级的工程基础；
- 已经有 Profile、Tools、Trace 等 Agent 产品雏形。

当前项目的不足：

- 用户、Workspace、Thread、Memory、Trace 还没有统一资源模型；
- memory 还没有完全按 user_id / workspace_id 隔离；
- thread 历史会话还没有产品化展示；
- Tool Registry / Profile 权限还不够清晰；
- Agent Runtime 与 API Route 的分层还可以进一步明确；
- Trace 目前偏运行时展示，尚未系统持久化；
- 安全策略、工具权限、沙箱和命令执行边界仍需加强；
- Scheduler / 自动化任务能力还没有形成产品功能；
- 缺少 Agent Run 级别的任务记录和审计。

---

## 5. Hermes 架构分析

### 5.1 整体定位

Hermes 是一个可长期运行的 self-improving AI Agent。它不是单纯聊天机器人，而是围绕 memory、skills、tools、sessions、profiles、gateway、scheduler 构建的 Agent 系统。

Hermes 的重要特点是：不同入口可以复用同一个 Agent Core。CLI、Gateway、ACP、Batch、API Server 等入口层不应该各自实现一套 Agent 逻辑，而应该把平台差异限制在入口层，真正的 agent loop、prompt assembly、provider resolution、tool dispatch、session persistence 应该由统一的 runtime 负责。

### 5.2 架构分层

Hermes 的重要架构思想包括：

- 多入口复用同一个 Agent Core；
- Tool Registry 负责工具注册和发现；
- Profile 隔离不同用户或不同运行配置；
- Session Storage 持久化会话、消息和模型配置；
- Memory / Skills 支持跨会话能力沉淀；
- Gateway 负责多平台消息入口；
- Scheduler / Cron 支持无人值守的定时任务；
- Observability 让工具调用过程对用户可见；
- Optional subsystems 通过 registry pattern 和 check_fn gating 降低耦合。

### 5.3 对 ChainCloud-AI 的启发

ChainCloud-AI 可以重点借鉴：

- 将 Agent Runtime 从 API route 中进一步抽象出来；
- 建立统一 Tool Registry；
- 为 Profile 增加 allowed_tools、model_config、prompt_config；
- 将 thread、memory、tool trace 持久化到统一资源模型；
- 让前端 Web Console 能展示 thread history、tool trace、memory context；
- 后续支持定时分析任务、链上监控任务、风险预警任务等 scheduler 场景；
- 建立 Agent Run 级别的执行记录，支持排查一次回答背后的模型、工具、memory 和 trace。

---

## 6. OpenClaw 架构分析

### 6.1 整体定位

OpenClaw 是一个 local-first personal AI assistant。它通过 Gateway 接入多个消息渠道，通过 Workspace 管理上下文和记忆，通过 Skills / Tools 扩展能力，并通过 sandbox / access profile 控制不同 Agent 的权限边界。

OpenClaw 的核心启发是：Agent 不是孤立的一次问答，而是运行在某个 workspace 中，拥有自己的记忆、工具、技能、会话状态和权限边界。

### 6.2 Gateway

OpenClaw 的 Gateway 是统一控制平面，负责 channels、nodes、sessions、hooks 等能力。它不是产品本体，产品本体是 assistant。

ChainCloud-AI 当前虽然有 FastAPI 后端，但还没有形成类似 Gateway 的统一控制平面概念。后续可以将 FastAPI + Agent Runtime + Tool Registry + Trace Persistence 逐步演进为 ChainCloud-AI 的 Agent Gateway。

ChainCloud-AI 的 Agent Gateway 可以负责：

- 统一入口：Web Console、API、后续 CLI、Scheduler；
- 鉴权：Bearer token / JWT / user identity；
- 会话管理：thread、workspace、agent_run；
- 工具调度：profile allowed_tools、tool policy；
- 记忆管理：memory read / write / summarize / search；
- Trace 记录：tool call、model call、error、latency；
- 安全控制：危险工具限制、只读数据库工具、链上 RPC 权限；
- 任务调度：定时链上监控、周期性风险分析。

### 6.3 Workspace

OpenClaw 的 Workspace 是 Agent 的 home。它既是文件工具的默认工作目录，也是上下文和记忆的载体。

ChainCloud-AI 当前已经有 user、thread_id、memory_key、checkpoint，但缺少 Workspace 这一层。后续可以引入：

User
  -> Workspace
      -> Thread
          -> Messages / Checkpoints
          -> Memories
          -> Tool Traces
          -> Agent Runs

这样可以把当前已经完成的用户登录、memory、checkpoint 和未来 trace 持久化整合到同一个产品模型中。

在 ChainCloud-AI 中，Workspace 不一定要像 OpenClaw 那样直接对应本地文件夹。结合公司业务，可以设计成“项目空间”或“分析空间”：

- 一个链上调查任务可以是一个 Workspace；
- 一个客户 / 项目 / 地址集可以是一个 Workspace；
- 一个风险分析主题可以是一个 Workspace；
- Workspace 下可以包含多个 thread、memory、tool trace、agent run。

### 6.4 Memory

OpenClaw 的 memory 强调“写入可见文件”，例如 MEMORY.md 和 memory/YYYY-MM-DD.md。它的核心思想是：模型不会真正隐藏地记住东西，只有被写入持久化介质的内容才算 memory。

ChainCloud-AI 当前已经使用 PostgreSQL 存储长期 memory。后续可以借鉴 OpenClaw 的思想，将 memory 拆成：

- durable memory：长期稳定事实、用户偏好、业务结论；
- workspace memory：某个项目空间中的背景信息、链上地址、调查对象、分析假设；
- thread summary：某一次对话结束后的结构化摘要；
- daily notes：某一天或某阶段的分析记录；
- memory search：按语义或关键词召回；
- memory provenance：记录 memory 来源 thread、时间和证据。

### 6.5 Skills / Tools

OpenClaw 的 Skills 是带有 SKILL.md 的目录，用于教 Agent 如何使用特定工具或完成特定工作流。它还支持不同来源的 skills，并通过优先级和 allowlist 控制不同 Agent 能使用哪些 skill。

ChainCloud-AI 可以借鉴这个思想，将当前 tools 逐步整理为：

- Tool Metadata；
- Tool Schema；
- Tool Executor；
- Tool Permission；
- Profile -> Allowed Tools；
- User / Workspace -> Allowed Profiles；
- Tool Call Trace。

对于 ChainCloud-AI，Tool Registry 不只是“有哪些函数可以调用”，还应该描述：

- 这个工具做什么；
- 输入参数 schema 是什么；
- 输出结构是什么；
- 是否只读；
- 是否访问公司数据库；
- 是否访问链上 RPC；
- 是否需要登录；
- 是否需要管理员权限；
- 是否需要用户确认；
- 调用过程是否需要记录审计日志。

### 6.6 Security / Sandbox

OpenClaw 的安全模型强调：不要把 session key 当成授权边界，真实的权限边界应该来自 Gateway auth、Agent access profile、sandbox、tool allow/deny、独立 host / OS user 等。

ChainCloud-AI 后续也需要明确：

- 哪些接口必须登录；
- 哪些 tools 需要额外权限；
- 哪些 profile 可以调用链上数据库工具；
- 哪些 profile 只能调用只读工具；
- 是否允许用户自定义工具；
- Tool call 是否需要审计；
- 是否需要给高风险工具加 confirm / deny 策略；
- 公司内部多用户使用时如何避免资源串扰；
- 如何避免 prompt injection 诱导 Agent 调用高风险工具。

---

## 7. Hermes / OpenClaw / OpenClaude 架构对比

| 维度 | Hermes | OpenClaw | OpenClaude | 对 ChainCloud-AI 的启发 |
|---|---|---|---|---|
| 产品定位 | 通用 AI Agent / self-improving agent | Personal AI Assistant / local-first agent | Coding Agent / Claude Code 替代方向 | ChainCloud-AI 更接近 Hermes / OpenClaw |
| 入口层 | CLI、Gateway、API、Scheduler 等 | 多消息渠道、Gateway、CLI / TUI | CLI、headless server、VS Code extension | 后续可以将 FastAPI 演进为 Agent Gateway |
| Agent Core | 统一 Agent Runtime | Gateway + Agent + Workspace | CLI-driven Agent loop | 需要从 API Route 中抽象 Agent Runtime |
| Memory | 长期记忆、跨会话上下文 | Workspace Markdown memory、daily notes、memory tools | 更偏项目上下文和文件上下文 | 当前 PostgreSQL memory 可继续深化 |
| Workspace | Profile / Session 维度较强 | Workspace 是 Agent 的 home | 项目目录即上下文 | ChainCloud-AI 建议新增 workspace 模型 |
| Tools / Skills | Tool Registry、skills、plugins | Skills、tools、sandbox、allowlist | bash、file、grep、glob、MCP、web tools | 需要 Tool Registry + Profile 权限 |
| Session / Thread | Session Storage | Sessions / workspace context | CLI conversation context | 当前 thread checkpoint 可产品化 |
| Observability | Tool trace、debug、observable execution | Gateway logs、tool execution visibility | CLI streaming output | 当前 trace 应持久化并前端展示 |
| Security | Profile isolation、tool control | Sandbox、access profile、tool allow/deny | 命令执行风险控制 | 公司内部项目需要权限边界和审计 |
| 适合借鉴程度 | 高 | 高 | 中 | 主线参考 Hermes / OpenClaw，补充参考 OpenClaude |

---

## 8. Hermes / OpenClaw 共同设计模式

### 8.1 Agent Core 与入口层解耦

CLI、Web、Gateway、Scheduler、API 都不应该各自实现一套 Agent 逻辑，而应该复用统一 Agent Runtime。

对 ChainCloud-AI 来说，后续可以将当前 /chat route 中的执行逻辑抽象为 Agent Runtime：

- route 层负责鉴权、参数校验、响应格式；
- runtime 层负责 profile、memory、tool、checkpoint、trace 的协调；
- persistence 层负责 users、workspaces、threads、memories、agent_runs、tool_traces 的存取。

### 8.2 Provider 抽象与模型路由

模型供应商、base_url、api_key、model_name、fallback 策略应该配置化，而不是散落在业务代码中。

ChainCloud-AI 后续可以设计：

- ProviderConfig；
- ModelProfile；
- Profile -> ModelConfig；
- fallback provider；
- 成本 / latency / token 记录；
- 不同 profile 使用不同模型。

### 8.3 Tool Registry 与权限控制

工具不只是函数，还应该有 metadata、schema、permission、profile allowlist、运行 trace 和错误处理。

ChainCloud-AI 可以建立：

- tools/registry.py；
- ToolSpec；
- ToolPermission；
- ProfileAllowedTool；
- ToolExecutor；
- ToolTraceRecorder。

### 8.4 Memory / Context / Session 分层

短期上下文、长期 memory、会话 checkpoint、workspace context 应该分清楚，避免所有信息都混在 prompt 或 thread_id 里。

建议区分：

- thread context：当前对话上下文；
- checkpoint：LangGraph 或执行框架的状态恢复；
- long-term memory：长期可复用记忆；
- workspace context：某个项目空间的背景材料；
- profile prompt：Agent 模式和角色说明；
- tool trace：Agent 实际执行过的操作记录。

### 8.5 Workspace / User / Profile 隔离

一个真实 Agent 产品需要清楚区分：

User：谁在使用系统  
Workspace：用户在哪个项目空间中工作  
Thread：某一次连续对话或任务  
Memory：长期沉淀的知识和偏好  
Profile：Agent 的模式、模型和工具权限  
Tool Trace：Agent 实际做过什么  
Agent Run：一次完整执行过程  

### 8.6 Observability

Agent 系统必须能回答：

- 这次回答用了哪个模型；
- 读取了哪些 memory；
- 调用了哪些工具；
- 工具输入输出是什么；
- 哪一步失败了；
- 是否发生了重试；
- 最终结果来自哪些证据；
- 是否有敏感工具调用；
- 是否有异常成本或异常延迟。

---

## 9. ChainCloud-AI 目标定位：对标但不复制 Hermes / OpenClaw

### 9.1 目标定位

ChainCloud-AI 后续可以定位为：

> 面向链上数据分析、风险识别和业务调查场景的企业级 AI Agent Gateway + Workspace 平台。

它可以借鉴 Hermes / OpenClaw 的基本逻辑：

- 有统一 Agent Runtime；
- 有 Gateway / API 入口；
- 有 Workspace；
- 有 Memory；
- 有 Skills / Tools；
- 有 Profile；
- 有 Session / Thread；
- 有 Trace / Observability；
- 有权限边界和安全策略。

但 ChainCloud-AI 不应该简单复制它们，而应该形成自己的业务特色：

- 链上数据分析工具更强；
- 风险分析 Profile 更明确；
- 数据库只读工具和链上 RPC 工具更工程化；
- 适合公司内部部署和审计；
- 适合前端演示和业务验收；
- 支持面向一个调查主题长期沉淀上下文；
- 能把一次 Agent 分析过程沉淀成可复盘的 evidence trail。

### 9.2 与 Hermes / OpenClaw 的关系

ChainCloud-AI 可以学习 Hermes 的：

- platform-agnostic Agent Core；
- Tool Registry；
- Profile Isolation；
- Session Storage；
- Observable execution；
- Cron / Scheduler；
- skills from experience 的思路。

ChainCloud-AI 可以学习 OpenClaw 的：

- Gateway 作为控制平面；
- Workspace 作为 Agent home；
- Memory 显式持久化；
- Skills / Tools 目录化或插件化；
- Sandbox / Tool Policy；
- Multi-agent routing；
- Local-first / self-hosted 部署意识。

ChainCloud-AI 可以保留和强化自己的：

- FastAPI 后端；
- React/Vite Web Console；
- PostgreSQL 持久化；
- 公司数据库工具；
- 链上 RPC 工具；
- Profile 业务模式；
- Trace 可视化；
- 用户登录和权限系统。

---

## 10. ChainCloud-AI 后续目标架构草案

推荐演进为：

Frontend Web Console
        |
        v
FastAPI / Agent Gateway
  - /auth
  - /chat
  - /memory
  - /profiles
  - /tools
  - /threads
  - /workspaces
  - /agent-runs
        |
        v
Agent Runtime
  - Chat Session Runner
  - Profile Resolver
  - Model / Provider Resolver
  - Prompt / Context Builder
  - Tool Call Orchestrator
  - Trace Recorder
        |
        +--------------------+
        |                    |
        v                    v
Context Manager        Tool Registry
  - Thread Context       - Tool Metadata
  - Long-term Memory     - Tool Schema
  - User Profile         - Tool Permission
  - Workspace Context    - Tool Executor
  - Checkpoint State     - Tool Policy
        |                    |
        +---------+----------+
                  |
                  v
Persistence Layer
  - Users
  - Workspaces
  - Threads
  - Messages / Checkpoints
  - Memories
  - Tool Traces
  - Agent Runs
  - Profiles
  - Tool Permissions
                  |
                  v
Observability
  - Run Trace
  - Tool Call Logs
  - Model Call Logs
  - Error Records
  - Debug View
  - Cost / Latency

### 10.1 核心模块说明

#### 10.1.1 Agent Gateway

Agent Gateway 是对外统一入口，可以由当前 FastAPI 服务逐步演进而来。它负责：

- 统一认证；
- 接收 Web Console / API / Scheduler 请求；
- 创建或恢复 workspace / thread；
- 调用 Agent Runtime；
- 记录 agent_run；
- 返回结构化响应和 trace。

#### 10.1.2 Agent Runtime

Agent Runtime 是核心执行层，负责：

- 解析 profile；
- 装配 system prompt；
- 读取 workspace context；
- 读取 thread checkpoint；
- 召回 memory；
- 调用 LLM；
- 解析 tool calls；
- 调用 Tool Registry；
- 写入 trace；
- 更新 checkpoint；
- 输出最终回答。

#### 10.1.3 Context Manager

Context Manager 负责管理上下文：

- 当前 thread 历史；
- 长期 memory；
- workspace context；
- profile prompt；
- tool results；
- checkpoint state；
- context compression。

#### 10.1.4 Tool Registry

Tool Registry 负责工具生命周期：

- 工具注册；
- 工具 schema；
- 工具权限；
- 工具执行；
- 工具错误处理；
- 工具 trace；
- 工具分组；
- profile allowed_tools。

#### 10.1.5 Persistence Layer

Persistence Layer 负责所有持久化资源：

- users；
- workspaces；
- threads；
- memories；
- checkpoints；
- agent_runs；
- tool_traces；
- profiles；
- tool_permissions。

#### 10.1.6 Observability

Observability 负责让 Agent 执行过程可解释、可复盘、可调试：

- 当前回答用了哪些 memory；
- 调用了哪些 tools；
- 每个 tool 的输入输出；
- 模型调用耗时；
- 工具调用耗时；
- 错误原因；
- 是否触发权限拒绝；
- 是否使用 fallback model。

---

## 11. 建议数据模型

### 11.1 用户与 Workspace

建议新增：

- agent_workspaces
- agent_threads
- agent_runs
- agent_tool_traces

当前已经存在：

- agent_users
- agent_memories
- checkpoint_* tables

后续可以逐步改造 agent_memories，增加 user_id 和 workspace_id。

### 11.2 推荐关系

User
  -> Workspace
      -> Thread
          -> Checkpoint
          -> Agent Run
              -> Tool Trace
          -> Memory

### 11.3 建议表结构方向

#### agent_workspaces

字段建议：

- workspace_id
- user_id
- name
- description
- default_profile
- metadata
- created_at
- updated_at
- archived_at

#### agent_threads

字段建议：

- thread_id
- workspace_id
- user_id
- title
- profile_id
- last_message_at
- metadata
- created_at
- updated_at
- archived_at

#### agent_runs

字段建议：

- run_id
- thread_id
- workspace_id
- user_id
- profile_id
- model
- status
- input_message
- output_message
- started_at
- ended_at
- latency_ms
- error_message
- metadata

#### agent_tool_traces

字段建议：

- trace_id
- run_id
- thread_id
- workspace_id
- user_id
- tool_name
- tool_input
- tool_output
- status
- started_at
- ended_at
- latency_ms
- error_message
- metadata

#### agent_memories 后续增强

当前 agent_memories 可以增加：

- user_id
- workspace_id
- thread_id
- memory_type
- tags
- source_run_id
- source_trace_id
- importance
- last_used_at

---

## 12. 可落地 MVP 方向

### MVP 1：Workspace / Thread / Memory 统一资源模型

目标：

- 新增 workspace 概念；
- 将 thread、memory、tool trace 逐步挂到 workspace 下；
- 为后续历史会话、项目级上下文、用户隔离打基础。

推荐优先级：最高。

原因：

- 能承接当前已经完成的用户登录、memory、checkpoint；
- 不会大规模破坏已有功能；
- 前端可演示性强；
- 后续可以自然扩展到 tool trace 和 agent run。

### MVP 2：Tool Registry / Profile 权限增强

目标：

- 梳理当前工具；
- 为每个工具增加 metadata；
- 增加 profile.allowed_tools；
- 前端展示 profile 可用工具；
- 为高风险工具预留权限控制。

推荐优先级：中高。

### MVP 3：Trace 持久化与前端展示

目标：

- 将当前运行时 trace 保存到 PostgreSQL；
- 支持按 thread 查看 tool call 历史；
- 支持 debug 页面展示 agent run。

推荐优先级：中高。

### MVP 4：Agent Runtime 分层重构

目标：

- 将 /chat 中的 Agent 执行逻辑进一步抽象；
- API route 只负责鉴权、参数校验和响应；
- Agent Runtime 负责 profile、memory、tool、checkpoint、trace 协调。

推荐优先级：中。

### MVP 5：Scheduler / 链上监控任务

目标：

- 支持用户创建定时链上监控任务；
- 定期调用链上 RPC 或只读数据库；
- 自动生成风险报告；
- 通过 Web Console 展示任务结果。

推荐优先级：中长期。

---

## 13. 推荐实施路线

### 第一阶段：架构调研文档

目标：

- 完成 Hermes / OpenClaw 架构分析；
- 梳理 ChainCloud-AI 当前差距；
- 给出目标架构草案；
- 给出 MVP 优先级。

输出：

- docs/research/agent_architecture_reference_hermes_openclaw.md

### 第二阶段：Workspace / Thread / Memory 模型设计

目标：

- 设计数据库表；
- 设计 API；
- 设计前端交互；
- 保持现有 memory / checkpoint 不被破坏。

建议分支：

- feat/workspace-thread-memory-model

### 第三阶段：Tool Registry / Profile 权限增强

目标：

- 梳理 tools；
- 增加 metadata；
- 增加 allowed_tools；
- 增加工具权限展示。

建议分支：

- feat/tool-registry-profile-permission

### 第四阶段：Trace 持久化

目标：

- 设计 agent_runs；
- 设计 tool_traces；
- 前端增加 trace history 页面。

建议分支：

- feat/agent-run-tool-trace-persistence

### 第五阶段：Agent Runtime 分层重构

目标：

- 将 Agent 执行链路从 API routes 中抽象出来；
- 形成可被 Web、CLI、Scheduler 复用的 Agent Runtime。

建议分支：

- refactor/agent-runtime-layer

---

## 14. 近期最推荐的开发任务

综合当前项目状态和可落地性，最推荐的下一项开发任务是：

> MVP 1：Workspace / Thread / Memory 统一资源模型

### 14.1 为什么优先做这个

原因包括：

- 它能承接已经完成的用户登录；
- 它能承接已经完成的长期 memory；
- 它能承接已经完成的 thread checkpoint；
- 它能为后续历史会话、trace、agent_run 打基础；
- 它不需要立即重构整个 Agent Runtime；
- 它的前端展示效果明显，方便向上级汇报；
- 它与 OpenClaw 的 workspace 思想高度对应；
- 它与 Hermes 的 session/profile isolation 思路也能对齐。

### 14.2 建议 MVP 范围

第一版不要做太大，只做：

- 新增 workspace 表；
- 登录用户默认创建一个 workspace；
- 前端展示当前 workspace；
- thread_id 绑定 workspace；
- memory 绑定 workspace；
- /chat 接口接收 workspace_id；
- /memory 接口按 workspace 过滤；
- 保持旧逻辑兼容。

### 14.3 不建议第一版就做的内容

暂时不要在第一版做：

- 多 workspace 高级管理；
- 完整权限系统；
- 复杂 memory search；
- 完整 Agent Runtime 重构；
- 复杂 sandbox；
- Scheduler；
- 多渠道 Gateway。

这些可以作为后续阶段继续推进。

---

## 15. 可以向上级汇报的版本

可以这样向上级说明当前思路：

> 我重新确认了参考对象后，认为 OpenClaw 和 Hermes 更适合作为 ChainCloud-AI 后续架构升级的主参考。OpenClaude 更偏 Coding Agent，可以作为补充参考。  
> 
> 当前 ChainCloud-AI 已经完成 Web Console、用户登录、长期 memory、thread checkpoint 和本地部署能力，已经具备 Agent MVP 的基础。下一步我建议不只是继续堆功能，而是参考 Hermes / OpenClaw 的架构，把项目逐步演进为面向链上数据分析场景的 Agent Gateway + Workspace 平台。  
> 
> 具体来说，短期先补 Workspace / Thread / Memory 统一资源模型，把用户、项目空间、会话、记忆、工具调用记录和 Agent Run 串起来。中期再做 Tool Registry / Profile 权限增强和 Trace 持久化。长期再考虑 Scheduler、链上监控任务、多入口 Gateway 和更完整的 Agent Runtime 分层。  
> 
> 这样做既能继承当前已经完成的工作，也能让项目朝 Hermes / OpenClaw 那类完整 Agent 架构演进，同时保留 ChainCloud-AI 在链上数据分析、风险识别和企业内部工具接入方面的特色。

---

## 16. 阶段性结论

Hermes 和 OpenClaw 对 ChainCloud-AI 的最大启发不是某一个具体功能，而是“Agent 产品应该有清晰的系统架构”。

当前 ChainCloud-AI 已经具备用户登录、memory、checkpoint、Web Console 和本地部署能力，下一步不宜继续零散增加功能，而应该围绕 Workspace / Thread / Memory / Tool Trace / Profile Permission 建立统一资源模型。

因此，建议优先落地：

Workspace / Thread / Memory 统一资源模型

在此基础上，再逐步推进 Tool Registry、Profile 权限、Trace 持久化、Agent Runtime 分层重构和 Scheduler / 链上监控任务。

最终目标不是复制 Hermes 或 OpenClaw，而是把 ChainCloud-AI 打造成一个具有类似基础架构逻辑、但更适合链上数据分析和公司业务场景的 AI Agent 平台。
