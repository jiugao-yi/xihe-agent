---
type: concept
title: 三模式调用架构——SharedContext / AgentTurnRunner / XiheAgent 分层
slug: 0052_three-mode-architecture
aliases:
  - turn runner
  - 三模式架构
  - 入口收敛
tags:
  - architecture
  - cli
  - gateway
  - serve
  - refactor
status: active
created: 2026-09-17
updated: 2026-09-17
related_pages:
  - wiki/changes/0053_architecture-refactor.md
  - wiki/concepts/0046_shared-session-terminal.md
  - wiki/concepts/0025_desktop-control-plane.md
---

# 三模式调用架构——SharedContext / AgentTurnRunner / XiheAgent 分层

## 摘要

三种运行模式（CLI / gateway / serve）共享同一个 agent 核心，但 2026-09 之前各入口**自己实现** turn 编排：agent 构造、回调装配、plan 批准交接、late-steer 处理散落三处且质量不一（serve 最完整，gateway 夹生，CLI 最简）。本轮收敛为四层，每层职责单一、依赖单向：

```
入口层（协议 + 串行化 + 投递）
   │  解析出 AgentTurnParams + TurnCallbacks
   ▼
AgentTurnRunner（core/agent/turn_runner.py，147 行）
   │  build_agent / run / plan_outcome
   ▼
XiheAgent（core/agent/agent.py）
   │  chat() 门面 → _chat_turn() 轮体 → _dispatch_tools()
   ▼
SharedContext（core/context.py）
   （重状态持有 + create_agent 工厂；bootstrap_process 进程组合根）
```

## 各层职责

### SharedContext（core/context.py）——应用装配零件

- 持有进程级重状态：`db`（SQLite）/ `aux`（辅助模型：压缩/标题/视觉）/ `compressor` / `llm_client`（主模型共享 OpenAI 客户端，httpx 连接池复用）。
- `create_agent()`：**唯一的 XiheAgent 构造实现**——把四样共享对象接线到新实例。是工厂+原材料仓库的合一（类比 SessionFactory.session()）；被三类消费方使用：serve/gateway 的 `runner.build_agent()`（每轮/每消息）、CLI 的 `_build_cli_agent`（进程一次）、cron 的 agent factory（bootstrap_process 注册）。
- `bootstrap_process(ctx)`：进程组合根（工具加载 → MCP 发现 → 专家注册 → cron 接线），每个模式入口显式调用一次；SharedContext 构造本身无副作用（测试可裸建）。
- **不认识任何模式**——CLI 的启动序列（`_build_cli_agent`）在 cli/cli.py 模式层，不在 core。

### XiheAgent（core/agent/agent.py）——一轮的执行机器

- `chat()`：25 行门面——plan 轮物理切换 roster 至 base 只读面（try/finally 恢复）+ 转发。
- `_chat_turn()`：turn 体——上下文准备（载入/修复悬空调用/auto-title/todo 水合/system 组装/轮前压缩）→ 工具循环（cron 取消检查/中断检查/steer 注入/API 调用/空响应三级升级）→ 收尾（usage 记账/持久化）。**每条 return 路径自行持久化**。
- `_dispatch_tools()`：批次调度——连续只读调用并组并行，写调用单跑（读写顺序保证）。
- `_apply_api_transforms()`：**API 边界注入**——turn 层（plan layer/regenerate hint）与 memory 快照在发往 LLM 前拼进 system 副本，`messages[0]` 原件不动。不变式：**messages 列表是账本（落库），API 形态是快照（用完即弃）**；轮中途压缩重建 messages[0] 后，边界注入自动补回 turn 层。
- 回调经 `TurnCallbacks` dataclass（8 可选字段）传入，`chat` 签名收窄为 7 参数。

### AgentTurnRunner（core/agent/turn_runner.py）——轮的装配与调度

- `AgentTurnParams`：入口解析产出的统一输入（source/text/cwd/plan/regenerate/approval_key）。
- `__init__(ctx, params, callbacks, agent=None)`：**构造即就绪**——传入 agent 则复用（CLI 长生命周期实例交接）；未传则**立即**经 `ctx.create_agent` 构造（构造错误即刻暴露，serve 据此发 onboarding WS 事件）。构造好的 Runner 恒处可 run 状态，无"未就绪"中间态。
- `run() -> TurnResult`：调 `agent.chat`，结果归一（text/exit_reason/usage/error）。
- `plan_outcome() -> PlanOutcome | None`：plan 轮批准后（结构化批准——用户点 clarify 卡「批准执行」，`resolve_clarification` 置位 `plan_approved`，**零文本解析**）落盘计划到 `AGENT_HOME/scratch/plans/` 并产出执行轮消息文本。
- Runner **不实现任何 turn 内逻辑**——它把 params/callbacks 接到 agent 上跑一轮，归一结果。

### 入口层——协议翻译与投递

| | serve | CLI | gateway |
|---|---|---|---|
| 入口文件 | serve/conversations.py（`_handle_send`） | cli/cli.py（`run_turns_threaded`） | gateway/bot.py（`_handle_message`） |
| 回调载体 | Emitter（→队列→WS） | _CliEmitter（→console） | 闭包（→跨线程适配器投递） |
| turn 串行化 | `_turn_lock` | 单线程 | `_get_session_lock` |
| 流式 | Emitter→WS 帧 | 缓冲→markdown 渲染 | StreamConsumer→平台消息编辑 |
| plan 执行轮发起 | `create_task` 重入 _handle_send | `message_queue.append` | 合成 MessageEvent 重入 |
| 特有 | attach/grace/断线宽限 | 消息队列续跑 | ACK/late-steer 重排队 |

## 关键决策与理由

1. **构造即就绪（不在 Runner 上留 build_agent 两步仪式）**：serve 需要构造错误提前发 WS onboarding 事件——从 `__init__` 抛与从 `prepare()` 抛对 try/catch 无区别，两步仪式纯增噪音。CLI 复用长实例走 `agent=` 参数，构造器单一形态。
2. **turn 层 API 边界注入而非改 messages[0]**：messages[0] 会被持久化——曾因 plan layer 借道 messages[0] 而泄漏进历史（后续普通轮把轮级指令当常驻指令读）。与 memory 快照同机制后，崩溃/中断也不泄漏。
3. **plan 批准走结构化 clarify 而非文本标记**：旧协议模型输出 `PLAN_APPROVED` 行 + 入口正则解析——模型服从性依赖 + 正文引用误触发。新协议：模型调 clarify 提交，用户点击产生**确定性输入**，`resolve_clarification` 仅在 plan 轮存活期且答案精确等于选项时置位标志。
4. **CLI 保持长生命周期 agent**（不模仿 gateway per-message）：request_tools 扩权、todo 内存、steer 队列边界是 REPL 需要的实例态；gateway 的 per-message + hydrate 是 webhook 场景的 workaround，不是理想形态。构造门脸统一（同走 ctx 工厂），生命周期差异只是调用次数。
5. **gateway late-steer 重排队 vs serve 丢弃**：消息渠道的 late steer 是真实用户输入必须回答；桌面的 late steer 多为多余（用户已转向）。政策不同是业务事实，注释已在两处说明。
6. **gateway 三条发起新轮路径（send/steer 重排/plan-exec）不强制合并**：各自清理语义不同（steer 重排队无 pending 清理、plan-exec 继承 plan 标志关闭），合并需旗标参数反而劣化。

## 排查指引

- **plan 轮批准没反应**：检查 runner 所在入口是否读了 `agent.plan_approved`；模型是否调了 clarify（看轮 trace）；自由文本回答=修订意见不是批准。
- **重新生成答"我刚答过"**：确认走的是 `runner` 带 `regenerate=True`（桌面按钮两段确认后触发），REGENERATE_HINT 在 system 尾部。

## 相关

- 变更记录：[[0053_architecture-refactor]]
- 轮级层细节：core/agent/turn_layers.py（PLAN_MODE_LAYER/REGENERATE_HINT/PLAN_SUBMIT_OPTIONS）
- 终端面板（agent 侧通道）：[[0046_shared-session-terminal]]
