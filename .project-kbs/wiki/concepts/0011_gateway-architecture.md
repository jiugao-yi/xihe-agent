---
type: concept
title: Gateway 架构（消息处理与并发模型）
slug: 0011_gateway-architecture
aliases:
  - gateway 架构
  - SharedContext
  - fresh agent per message
  - 消息并发
tags:
  - architecture
  - gateway
  - concurrency
status: active
created: 2026-07-06
updated: 2026-09-14
related_pages:
  - wiki/entities/0001_xihe-agent.md
  - wiki/concepts/0006_session-design.md
  - wiki/concepts/0002_tool-registry-and-dispatch.md
  - wiki/concepts/0005_mcp-dynamic-registration.md
  - wiki/changes/0010_cdp-default-and-cron-job-forms.md
---

# Gateway 架构（消息处理与并发模型）

## 摘要

xihe 一个 agent 内核、三种运行模式（CLI 长生命周期 agent / gateway 每消息新建 agent / serve 同模式走 HTTP+WS，见 [[0024_desktop-serve-protocol]]）：**Gateway 模式每来一条消息就新建一个 `XiheAgent`**，但这是廉价操作——贵的状态都在 `SharedContext`（和一批模块全局）里跨消息共享，对话连续性靠 SQLite，不靠 agent 对象。消息处理在专用线程里跑 agent 回合，主事件循环 `asyncio.to_thread` 等待（**2026-09-14 订正**：本页长期描述的「同步 join 阻塞事件循环」并发缺陷已修复——见下文并发节）。

## SharedContext：gateway 启动时建一次

`core/context.py:SharedContext.__init__` 拥有跨消息复用的重对象：
- `db` — `SessionDB`（SQLite，对话历史）
- `aux` — `AuxiliaryClient`（视觉/压缩等辅助 LLM 调用）
- `compressor` — `ContextCompressor`
- `client` — 共享 OpenAI client（每消息 agent 复用连接池）

并接线轻量工具依赖（`session_search_tool.set_session_db`、`vision/image_generation/tts_tools.set_auxiliary`）+ 解析主 agent 名单（`main_toolsets`/`main_skills`，来自 config.yaml 顶层键，见 [[0034_three-layer-agent-roster]]）。**进程级编排不在构造器里**（构造保持无副作用）：MCP 后台发现、专家注册、`start_scheduler()` 都在 `bootstrap_process()`（各模式入口显式调用一次）。

gateway 进程启动建一个 `SharedContext`，存活整个进程生命周期。

## create_agent：每条消息新建，薄壳

`SharedContext.create_agent(enabled_toolsets=..., skills_allowed=..., cwd=...)` 构造仍极廉价——持有 config + 共享引用；正式回合传 `main_toolsets`/`main_skills`，slash 命令/无参调用保持全量。`gateway/bot.py` 每条入站消息调一次（slash 命令上下文 `:326`、正式回合 `:352`）。

> 「廉价」是这套设计成立的前提：如果是贵对象就不敢每条消息建。

## 连续对话靠 SQLite，不靠 agent 对象

每条消息的新 agent 拿 `session_key` 去 `db` 里 load 历史消息 → 接着跑。agent 用完即弃。session_key 由 `SessionSource`（platform + chat_id + user）确定性生成（见 [[0006_session-design]]）。

## 模块级全局共享状态

除 SharedContext 外，这些状态跨消息持久（在各自 `tools/*.py` 模块顶层）：
- **浏览器**：`tools/browser_tool.py` 的 `_page`/`_context`/`_browser_instance`（CDP 托管真实 Chrome，登录态跨消息/跨重启保留，见 [[0003_browser-tools]]）
- **MCP 连接**：`tools/mcp_tool.py` 的 `_servers`（每个 server 一个长生命周期 asyncio Task，见 [[0005_mcp-dynamic-registration]]）
- **cron 调度器**：`core/services/scheduler.py` 的 daemon 线程 + `_jobs`（`tools/cronjob_tools.py` 只是工具面，见 [[0009_cron-jobs]]）；由 `bootstrap_process()` 在**每个模式**启动（gateway / serve / CLI），跨进程用 `cron/jobs.lock` 文件锁防双发

### cron 跑 job 需要 agent，由 gateway 启动时注入 factory

cron 的 daemon 线程每 60s tick，但执行 job 要调 `agent.chat(...)`。agent 来源有两种：
- **`_agent`（旧）**：聊天里调用 `cronjob` 工具时注入（`_inject_agent(parent_agent)`，handler 收到的 `kw["parent_agent"]`）。
- **`_agent_factory`（新，2026-07-07）**：`set_agent_factory(shared_ctx.create_agent)` 由 `run_gateway` 启动时注入。`_get_agent()` 优先用 factory **每个 job 取一个新 agent**（并发安全，不共享 `_agent`），`_has_agent()` 守卫 `_agent_factory or _agent`。

> **坑（已修）**：曾长期表现为「gateway 重启后 cron job 静默不执行」。根因是 `_agent` 只在聊天侧 cronjob 工具被调用时注入，重启后归零；调度器 `if not _agent: continue` 把每个 tick 全跳过，直到有人在聊天里碰巧用了 cronjob 工具——所以表现为「时灵时不灵、重启就废」。改为 gateway 启动注入 factory 后，cron 自主执行、重启不丢。
- **platform adapter**：`send_message_tool._adapter` / `scheduler._platform_adapter`（经 `set_platform_adapter` 注入；cron 投递优先走 `register_channel` 注册的通道，adapter 是旧回落）

## 消息处理线程模型与并发

每条消息：新 agent → 专用 `threading.Thread` 跑 `agent.chat()` → 主 asyncio 事件循环 `await asyncio.to_thread(agent_thread.join, 5.0)` 等它结束（`gateway/bot.py:459-468`，带 "Do NOT revert to blocking join" 注释）。**事件循环全程空闲**。

> **订正（2026-09-14）**：本页曾长期把「同步 `join` 阻塞事件循环 → 同 chat 被动串行 / 跨 chat 互相拖累 / interrupt 触发不了 / 回合期间无心跳」作为当前架构的已知特征。这些已全部修复（2026-08 随 serve 模式一并整改）：
> - **同 chat 显式串行**：per-session `asyncio.Lock` 从回合开始持有到投递完成（`bot.py:185-198, 366-369`）——不再靠事件循环被动阻塞的巧合。
> - **跨 chat 并发**：不同 session 的回合真并发。
> - **停止走带外**：`set_interrupt_handler` 注册的 handler 直接调 `interrupt_session`（`bot.py:226-244, 634`），不排队等 agent 线程。
> - **中途消息 = steer**：回合期间来的普通消息经 `set_steer_handler` 注入正在跑的回合（自然结束后未消费的 steer 转新回合，`bot.py:601-624`），不再堆积。
> - 事件循环空闲后心跳/流式全程可用。

## 消息处理流（gateway/bot.py:handle_message）

1. 抽取媒体（图片走 vision 预描述；无视觉模型时给 `image_ocr` 提示不自动跑）→ 拼成 text
2. `session_key = db.build_key(source)`
3. 同一 session 有活跃 agent → 停止意图直接带外中断；普通消息走 steer（见上）
4. **slash 命令 / 自然语言停止词**优先（"停"/"取消"/"stop" 等整词也当停止，`commands.py:15-51`）→ `handle_command`（见下）
5. `create_agent(main_toolsets, main_skills)` → 登记进 active 表
6. 专用线程跑 `agent.chat()`，主循环 to_thread 等待，超 `ACK_THRESHOLD_SECONDS`(10s) 且无思考帧在屏时发「正在处理」
7. 回合结束 → 发最终回复（流式或分片）+ 排空 pending media；**审批回调**（`approval_request_callback`）从工具线程桥回事件循环弹卡，裸 y/n/a 回复折批复（`bot.py:257-274, 405-434`，见 [[0037_approval-permission-system]]）

## Slash 命令路由（gateway/commands.py）

`handle_command` 在 agent 回合**之前**处理 `/` 开头的消息，返回约定：
- 非空字符串（且非 `__CLEAR__`/`__QUIT__`）→ 直接回复、**不跑 agent**
- `None` → 流入 agent 回合（如 `/login`）
- `__CLEAR__`/`__QUIT__` → CLI 特殊语义

命令：`/new /reset /title /model /status /history /sessions /resume /tools /compress /clear /stop /cancel /quit /exit /ping /login /reload-mcp /help`（`gateway/commands.py:75-99`）。

## ACK 与流式

- **ACK**：长任务超阈值（10s）发「⏳ 正在处理」——流式思考帧已在屏时跳过（`bot.py:475-492`）。
- **流式**：平台支持 `send_stream`/`edit_message` 时，`StreamConsumer` 跑成独立 asyncio task，agent 线程通过回调把 delta 推给它；事件循环空闲，流式期间照常收新消息/停止/steer。

## 「新 agent + 活 registry」带来的特性

工具注册表（`core/registry.py` 的 `registry` 单例）是进程级 live 对象。每条消息的新 agent 调 `registry.get_schemas()` 实时读 → **运行时注册的工具下条消息自动可见，不用刷缓存**：
- MCP `/reload-mcp` 后，下条消息的 agent 自动拿到新工具集（见 [[0005_mcp-dynamic-registration]]）
- 浏览器 `check_fn` 门控变化同理

## 相关页面

- [[0001_xihe-agent]] — 项目总览，CLI/Gateway/serve 三模式
- [[0006_session-design]] — session_key/session_id 两层，对话连续性的基础
- [[0002_tool-registry-and-dispatch]] — 工具注册表 + check_fn 门控（新 agent 实时读）
- [[0005_mcp-dynamic-registration]] — MCP 工具的动态注册与 `/reload-mcp`
- [[0010_cdp-default-and-cron-job-forms]] — 含 gateway `run_gateway` 改动记录
