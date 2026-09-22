---
type: concept
title: 桌面端通信协议与 xihe serve 模式
slug: 0024_desktop-serve-protocol
aliases:
  - xihe serve
  - serve 模式
  - Workspace Protocol
  - 桌面端通信协议
  - 桌面↔agent 协议
tags:
  - architecture
  - serve
  - desktop
  - protocol
  - websocket
status: active
created: 2026-08-10
updated: 2026-09-20
related_pages:
  - wiki/concepts/0011_gateway-architecture.md
  - wiki/concepts/0006_session-design.md
  - wiki/concepts/0023_multi-instance-config.md
  - wiki/concepts/0002_tool-registry-and-dispatch.md
  - wiki/entities/0001_xihe-agent.md
---

# 桌面端通信协议与 xihe serve 模式

## 摘要

`xihe serve` 是 xihe 的**第三种运行模式**（继 `xihe chat` 交互式 CLI、`xihe gateway` 消息平台之后）。它把**同一个 agent 内核**用 aiohttp 包成本地 HTTP+WebSocket 服务，让外部前端（桌面 app、脚本、web UI）能在不内嵌引擎的前提下驱动 xihe——桌面连 `/stream`，就像任意客户端连后端。

架构复刻 gateway：一个 `SharedContext`（SQLite / 辅助 LLM / 压缩器，启动建一次）+ **每轮对话新建一个薄 `XiheAgent`**（见 [[0011_gateway-architecture]]）。关键差异在**并发模型**：serve 用 `loop.run_in_executor` 把 agent 回合丢进工作线程，事件循环**不被阻塞**——从而避开了 gateway「`thread.join()` 卡死单线程循环」的已知顽疾。agent 工作线程的同步流式回调通过 stdlib `queue.Queue` 桥接到 WebSocket，与 `gateway.stream_consumer.StreamConsumer` 同思路。

桌面端 ↔ serve 的这套 REST + WS 契约，本文档记为 **Workspace Protocol**。它是桌面端（控制面，见 [[0025_desktop-control-plane]]）与 xihe（执行内核）之间的中立接口——桌面按能力描述符分支，**永不 sniff 引擎名**。

## 第三种运行模式

| 模式 | agent 生命周期 | 入口 | 前端 | 事件循环阻塞？ |
|------|----------------|------|------|----------------|
| `chat` | 一个长生命周期 `XiheAgent` | `cli/chat.py` | 终端 REPL | — |
| `gateway` | 每条消息新建薄 agent | `gateway/bot.py` | 平台 adapter（WeCom/Feishu） | **否**（`asyncio.to_thread` join，2026-08 起，见 [[0011_gateway-architecture]]） |
| **`serve`** | **每轮对话新建薄 agent** | `gateway/serve/`（server.py 组装） | 任意 HTTP/WS 客户端（桌面/脚本/web） | **否**（`run_in_executor`） |

serve 本质上是「把 gateway 模式跑在一个中立协议上」，把平台 adapter 换成了 aiohttp。`SharedContext`（`core/context.py`，`bootstrap_process` 做进程编排）跨轮复用重对象；`SharedContext.create_agent(main_toolsets, main_skills, cwd)` 每轮调一次——**薄壳**，构造廉价（同 [[0011_gateway-architecture]] 的前提）；名单来自 config 顶层键（见 [[0034_three-layer-agent-roster]]），无 DEFAULT_TOOLSETS。

入口注册：`app/main.py:cmd_serve` → `load_config(args.config)` → `setup_logging(INFO, also_file=True)`（serve 起初漏了 logging，回调日志被吞，已修）→ `run_serve(config, host=args.host, port=args.port, version=VERSION)`。CLI 子命令：`xihe serve [--host 127.0.0.1] [--port 7788] [--config X]`。端口/主机**不在 config.yaml 里**，是 CLI flag（默认 7788 / 127.0.0.1）；桌面端硬编码 7788（`setServeBase` 可覆盖）。

## REST 接口（无状态）

| 方法·路径 | 响应 |
|-----------|------|
| `GET /health` | `{ok, version, mode:"serve", model, capabilities:[...]}` |
| `GET /readiness` / `POST /test-connection` | 结构化缺项报告（onboarding UI）/ 服务端模型连通探测（key 不出端） |
| `GET /sessions` | `{sessions:[{conv_id, session_key, title, updated_at, msg_count}]}` —— serve 平台会话，按更新时间倒序 |
| `GET /convs/{conv_id}/messages` | `{conv_id, messages:[{role, content, id?, tools?, has_reasoning?, incomplete?, usage?, ts?, attachments?}]}` —— 历史转录（assistant 帧带 trace 锚点/工具数折叠元数据；user 帧带附件元数据 chips，2026-09-20 起；`id` 为 uuid 字符串） |
| `GET /convs/{conv_id}/trace/{msg_id}` | 单轮工具轨迹（懒加载；工具事件可带 `approval` 审批徽章数据） |
| `POST /convs/{conv_id}/reset` / `truncate` / `title`；`DELETE /convs/{conv_id}` | 重开一轮 / 回滚到某用户行（重发；`from_msg_id` 为 uuid）/ 改名 / 删会话 |
| `GET /toolresult` / `GET /toolargs` | 溢出落盘的完整工具结果 / 完整入参 |
| 管理/资源面 | `/mcp /skills /cron /specialists[/{slug}]`（CRUD）/ `/store*` / `/memory /kbs /kbs/page`；浏览器面板 `/browser/*`；终端 `/ssh/live* /local/live*`（见 [[0046_shared-session-terminal]]） |

`get_messages` 的折叠有两处**不能去掉**的容错：「无 content 的 assistant 帧」（纯工具调用框架、[[0011_gateway-architecture]] 提到的 dangling 修复/恢复提示等内部脚手架）；`role=="system"` 过滤分支已删（2026-09-20 起 system 行不再持久化——prompt 每轮重建，见 [[0054_messages-uuid-meta-refactor]]）。

## WebSocket `/stream` 事件契约

**客户端 → 服务端：**
| type | 字段 | 语义 |
|------|------|------|
| `send` | `conv_id`, `text`, `cwd?` | 发起一轮对话（`cwd`=工作空间绑定，透传给 agent） |
| `attach` | `conv_id` | 重连后认领会话（serve 返回 `attached{running}` ack） |
| `steer` | `conv_id`, `text` | 中途改向（不打断） |
| `interrupt` | `conv_id` | 中断该会话当前回合 |
| `approve` | `conv_id`, `id`, `approved`, `always?` | 审批卡批复（见 [[0037_approval-permission-system]]） |
| `clarify` | `conv_id`, `id`, `answer` | 澄清卡作答（选项点击=选项文本；自由输入原样） |

**服务端 → 客户端：**
| type | 字段 | 何时发 |
|------|------|--------|
| `hello` | `version`, `mode`, `model`, `capabilities` | 连接建立即发（无 `turn_id`/`conv_id`） |
| `attached` | `conv_id`, `running` | `attach` 的 ack（`running:false` + 本地 pending 气泡 → 强制重拉转录） |
| `turn_start` | `turn_id`, `conv_id`, `session_key` | 每轮回合开始 |
| `text_delta` | `turn_id`, `conv_id`, `text`, `by?` | 正文增量 |
| `thought_delta` | `turn_id`, `conv_id`, `text`, `by?` | 推理增量（`kind=="reasoning"`；`by` 归因外部引擎子活动） |
| `tool_call` | `turn_id`, `conv_id`, `name`, `args`, `by?`, `id?` | 工具开始 |
| `tool_result` | `turn_id`, `conv_id`, `name`, `result`, `elapsed`, `truncated?`, `by?` | 工具结束（**带 result 不带 args**；`elapsed` 秒） |
| `approval_request` / `approval_resolved` | `conv_id`, `id`, `name`, `summary`, `args?` / `id`, `approved`, `reason` | 审批卡弹出 / 各方落定 |
| `clarify_request` / `clarify_resolved` | `conv_id`, `id`, `question`, `options[]` / `id`, `status`, `answer?` | 澄清卡弹出（agent 中途提问并阻塞等待）/ 落定（answered 带答案文本） |
| `complete` | `turn_id`, `conv_id`, `text`, `reason?`, `usage?` | 回合结束（`reason`: interrupted/api_error/…；`usage` token 用量） |
| `cron_result` | `conv_id`, `text` | DesktopChannel 推送的定时任务结果 |
| `error` | `turn_id?`, `conv_id?`, `message`, `code?` | 回合失败或入参非法；`hello` 后的通用错误可无 `turn_id`/`conv_id` |

心跳：`WebSocketResponse(heartbeat=60)` —— aiohttp 自动 ping/pong 保活。

> **`args` 是摘要不是全文**：`tool_call` 的 `args` 被截到 **500 字符**（`_WS_ARGS_LIMIT`），用于 UI 展示；审批卡的 `args` 上限 64K；完整入参/结果可经 `GET /toolargs` / `GET /toolresult` 回捞（溢出侧存储，见 [[0002_tool-registry-and-dispatch]]）。

## 会话映射

桌面一个对话 id（`conv_id`）→ `SessionSource(platform="serve", chat_id=conv_id, user_id="desktop", chat_type="dm")` → 确定性 `session_key` = **`agent:main:serve:dm:{conv_id}`** → 一个持久 xihe 会话。

- 历史落 `sessions.db`，**跨 serve 重启存活**。
- 与 CLI / gateway 会话**完全隔离**（platform 段不同：`serve` vs `cli`/`wecom`/`feishu`），除非你手动复用 key。
- `_PLATFORM = "serve"`、`_DEFAULT_USER = "desktop"` 是 `gateway/serve/_common.py` 顶部两个常量——刻意匹配 `xihe serve` 命令名以便按进程 grep；若偏好 "server" 改这一处即可（cosmetic）。

会话 key 推导见 [[0006_session-design]]。

## 能力描述符（capability descriptor）

`_capabilities()` 向桌面声明能力 flag：

```
["text","streaming","tools","interrupt","sessions","thoughts","approvals"]   # 基线常驻
+ "browser"            # 有任一 browser_* 工具
+ "vision"             # 有 vision_analyze 或 image_ocr
+ "image_generation"   # 有 image_generation 工具
+ "mcp"                # 有任一 mcp_* 工具
```

后半段从 `registry._tools` 实时推导——也就是说能力 flag 反映**当前进程真正过 `check_fn` 门控的工具**（如 Playwright 没装则 `browser` 自动消失，见 [[0002_tool-registry-and-dispatch]] 的 check_fn 门控）。`/health` 与 WS `hello` 两处带这份描述符（`/agents` 端点已随多 agent 骨架清除删除，见 [[0047_desktop-single-agent-collapse]]）。

~~桌面 UI 按这些 flag 分支，永不 sniff 引擎名~~（2026-09-14 订正：桌面当前展示模型名，不按 capability 分支；「一套 UI 容纳异质 provider」的多引擎前提已随单 agent 收敛消失，描述符保留为通用自描述面）。

## Emitter：跨线程回调 → WS 的桥

这是 serve 最关键的技术点。`agent.chat()` 的回调（`stream_delta_callback` / `tool_call_start_callback` / `tool_call_callback`）是**从工作线程同步触发**的（agent 跑在 `run_in_executor` 的线程里），不能直接调 `ws.send_json`（asyncio 对象非线程安全，且该线程没有 running loop）。

`Emitter`（`gateway/serve/emitter.py`）解法：
- 回调把 JSON 事件 `put` 进一个 **stdlib `queue.Queue`**（线程安全，无需 loop）。
- WS handler 协程**自己**排空这个队列：`get_nowait()` 取不到就 `await asyncio.sleep(0.02)` 再试，循环往复直到工作线程结束。
- `_DONE` 哨兵：工作线程 `finally` 里 `emitter.finish()` 推一个 `_DONE`，排空循环见到它即收尾。

**为什么不用 `asyncio.Queue`？** 工作线程是 ThreadPoolExecutor 线程，**没有 running loop**，`asyncio.Queue.put` 既非线程安全、也无 loop 可用。stdlib `queue.Queue` 是唯一不需要 loop 的线程安全原语——这是 load-bearing 选择，不是风格偏好。

**与 gateway 的对比**：[[0011_gateway-architecture]] 的 `StreamConsumer` 是一个**独立 asyncio task**，agent 线程把 delta 推给它。serve **没有**单独的 consumer task——排空发生在 **WS handler 协程内**（`_handle_send` 里的 while 循环）。两者都是「同步工作线程回调 → 事件循环」的桥，但 serve 把桥并进了 handler。

**`on_delta` 的 `None` 哨兵**：agent 在工具分派前会用 `text is None` 标记段落边界（`core/agent/agent.py`）。Emitter 见 `None` 直接 skip——不产生 WS 事件，因为紧随其后的 `tool_call` 事件本身已承载了阶段切换。

## 并发与中断

serve 用 `loop.run_in_executor(None, _worker)` 跑 agent 回合，事件循环在排空间隙**完全空闲**——可以收新 WS 帧、发心跳、处理别的会话。（gateway 的 `thread.join` 阻塞问题也已用 `asyncio.to_thread` 修复，见 [[0011_gateway-architecture]]；两模式的差异现在主要是协议面而非循环健康度。）Emitter 内置 **delta 合并**（0.2s / 160 字符窗口 + flusher 线程），长流不逐 chunk 发帧。

两条锁：

- **`_turn_locks`**（`conv_id → asyncio.Lock`）：同一会话的回合**串行**，防止历史被并发写坏 / 回复交错。仅在单事件循环线程访问，dict 变更无需额外锁；`asyncio.Lock` 守的是 await 区间。
- **`_active`**（`conv_id → 当前 XiheAgent`）+ `threading.Lock`：给 `/interrupt` 用。`_handle_send` 开回合时登记 agent、结束时按身份比对 `pop`（防把后一轮的 agent 误删）。

中断路径（两条）：
1. **客户端主动**：WS 收 `{type:"interrupt","conv_id":...}` → `_interrupt()` 从 `_active` 取 agent → `agent.interrupt()`。
2. **客户端掉线**：`stream()` 的 `finally` 不再立刻中断——起 **60s 宽限窗**（`_GRACE_SECONDS`），回合**脱离连接继续跑**并持续落库；重连的客户端发 `attach` 认领同一回合继续收流（`attached{running}` ack；宽限窗烧完仍无人认领才真正 `_interrupt`）。

`_safe_send(ws, obj) -> bool`：发帧前先判 `ws.closed`，发送异常一律返回 `False`。

## CORS 与桌面端接法

Electron renderer（`file://`/`app://` origin）跨域 fetch `http://127.0.0.1:<port>` 的 REST。WS 不受同源约束，故 CORS 只需给 REST 加：

- `on_response_prepare` 钩子给所有响应加 `Access-Control-Allow-Origin: *`、`Allow-Headers: Content-Type`、`Allow-Methods: GET, POST, PUT, DELETE, OPTIONS`；`WebSocketResponse` 跳过。
- `OPTIONS /{tail:.*}` 返回 204，满足预检。

桌面端（renderer 原生 `WebSocket` + `fetch`，无 axios）：
- `baseUrl = http://127.0.0.1:7788`、`wsUrl = ws://127.0.0.1:7788/stream` 硬编码；`setServeBase(url)` 可改（给设置项留口）。
- `connectStream` 在 `onopen` resolve、`onclose`/`onerror` reject / 触发 `onStatus(false)`；store 据此做 3s 指数重连兜底。
- React StrictMode 在 dev 下会 double-invoke `useEffect` → 两次 `/health` + `/stream`，无害（生产无）。

## 落地文件

- **serve 内核**：`gateway/serve/` 按业务分模块——`server.py`（ServeApp 组装 / `_capabilities` / CORS / banner）、`chat.py`（/stream 回合引擎 + 会话/历史/审批路由）、`system.py`（health/readiness/test-connection）、`admin.py`（specialists/store/mcp/skills/cron）、`browser.py`/`terminal.py`/`knowledge.py`、`emitter.py`（跨线程桥）、`_common.py`（常量/限长）
- **CLI 接线**：`app/main.py`（`cmd_serve` + `serve` 子解析器：`--host` / `--port` / `--config`）
- **桌面端客户端**：`desktop/src/renderer/src/lib/serveClient.ts`（事件联合类型 + REST/WS 客户端）

## 设计权衡与坑

- **漏 logging（已修）**：`cmd_serve` 起初没调 `setup_logging`，回调里的 `logger.info` 全被吞（stdout 还是块缓冲）。现已在 `cmd_serve` 加 `setup_logging(level=INFO, also_file=True)`，`agent.log` 能看到 `[gateway.serve] xihe serve listening...`。排查 serve 问题先看 `agent.log`。
- **平台名 `serve` vs `server`**：刻意选 `serve` 匹配命令名，便于 grep；单常量，纯 cosmetic，要改改一处。
- **端口在 CLI 不在 config**：`--port`/`--host` 是 flag，不进 `config.yaml`。要开多实例用 `xihe --config X serve --port N`（实例隔离见 [[0023_multi-instance-config]]：`agent_home` 决定数据根，各实例 sessions/log/browser/cron 独立）。
- **历史过滤不可去**：system prompt + 空 assistant 帧是 xihe 内部脚手架，泄漏到桌面会暴露 recovery hint / dangling 修复（见 [[0011_gateway-architecture]] 的 `_repair_dangling_tool_calls` / `_inject_recovery_hint`）。
- **排空用 stdlib queue 而非 asyncio.Queue**：见 Emitter 节，工作线程无 loop。
- **客户端掉线不再立即中断**（订正 2026-09-14）：60s 宽限窗内回合继续跑，重连 `attach` 认领；窗内无人认领才中断——兼顾「不烧死轮」与「短暂闪断不丢工作」。

## 相关页面

- [[0011_gateway-architecture]] —— SharedContext + 每消息薄 agent（serve 同构）+ serve 解决的 `thread.join` 阻塞顽疾
- [[0025_desktop-control-plane]] —— 桌面端完整设计：三层模型、两种 provider 形态、capability-driven UI、demo/live 兜底、roadmap
- [[0006_session-design]] —— session_key 推导，会话映射的基础
- [[0023_multi-instance-config]] —— `xihe --config X serve --port N` 多实例隔离
- [[0002_tool-registry-and-dispatch]] —— 能力描述符的推导来源（registry + check_fn 门控）
- [[0001_xihe-agent]] —— 项目总览，三种运行模式
