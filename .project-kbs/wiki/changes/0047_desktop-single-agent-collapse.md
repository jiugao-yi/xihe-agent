---
type: change
title: 桌面端单 agent 收敛——多 agent 骨架清除
slug: 0047_desktop-single-agent-collapse
change_type: refactor
risk_level: medium
status: completed
created: 2026-09-14
updated: 2026-09-14
affected_services:
  - xihe-desktop
  - xihe serve
affected_modules:
  - desktop/src/renderer/src/appStore.ts
  - desktop/src/renderer/src/components
  - desktop/src/renderer/src/lib/serveClient.ts
  - src/gateway/serve/system.py
related_insights:
  - wiki/insights/0026_desktop-agent-model-built-in-xihe.md
---

# 桌面端单 agent 收敛——多 agent 骨架清除

## 摘要

按 [[0026]] 的终态把桌面端残留的**多 agent 骨架形状**清除：桌面唯一的 agent 是 xihe 自己（main 托管 `xihe serve`），claude/codex 经 xihe 的 `external_agent` 工具（[[0040]]）访问，不作为并列引擎。改动前桌面**实质上已是单 agent**（ClaudeRunner 双引擎、demo 种子、花名册 UI 早已删除），剩下的是纯转发层——`agents: Agent[]` 数组 + `selectedAgentId` + 所有 action 的 `agentId` 首参，每个"哪个 agent？"查找永远返回同一元素。本次把 store 扁平化、删 serve 侧 vestigial `/agents` 端点、清死代码与过时文案。

## 变更内容

### 桌面 store 扁平化（appStore.ts）

- **删除**：`Agent` 接口（engine/shape/status/capabilities/description/dataRoot 描述符字段全部无 UI 消费者，唯 `model` 在 Sidebar 显示）、`EngineKind`/`AgentShape`/`AgentStatus` 类型、`SEED_AGENTS`、`LIVE_SLOT_ID`、`selectedAgentId`、`select()`（唯一调用方 connectServe）、`owningAgent()` 反查。
- **新扁平状态**：`conversations: ConvMeta[]` + `activeConvId` + `serveModel`（connectServe 从 `/health` 取，替代原 `/agents` 采adopt）。
- **全部 action 去 `agentId` 首参**（sendMessage/interrupt/steer/approve/newConversation/selectConversation/reset/delete/rename/refresh/resend/editAndResend/regenerate + 内部 rollbackAndSend/syncConversations/loadActiveHistory）。
- **`serveBacked` 标志消失**：它只在 connectServe 里随 `serveConnected: true` 同置、从不复位——所有 `serveBacked && serveConnected` 门 ≡ `serveConnected`（12 处）。
- connectServe：删 `getAgents()` 采adopt 块 → `set({serveConnected, serveVersion, serveModel: health.model})`；`select(LIVE_SLOT_ID)` → `syncConversations().then(loadActiveHistory)`（保序：先 sync 自动选会话再加载历史）。
- 两个模块级 subscribe（tab bucket 换仓 / 视图持久化）与 `tabKeyOf`/`windowRecordOf` 改读扁平 `activeConvId`。

### 有意的行为改进（勿"修复"回去）

- **cron_result 列表刷新不再要求 conv 在本地列表**：原 `owningAgent(convId)` 找不到 owner 就不 sync，cron 新建的会话要手动刷新才出现；现在无条件 `syncConversations()`。
- **complete 事件的列表 sync 同理放宽**（conv 被删也刷新，`syncConversations` 合并 server 行 + localOnly 不会复活已删会话）。

### 组件与协议

- ChatPanel/App/Sidebar/RunPanel/TerminalPanel/SettingsPanel：去 `agent` prop 与查找（SettingsPanel 的 prop 本就未使用；App 的「选择左侧的 agent 开始」分支不可达，删）。ChatPanel 焦点 effect 的 conversations 读取走 `getState()` 快照**故意不进 deps**——列表 sync 不得抢 sidebar 改名中的焦点。
- **删除 `components/common.tsx`**（EngineBadge/StatusDot/CapChip，零 importer；不删会挂 tsc——它 import 被删类型）。
- serveClient.ts：删 `getAgents()` + `ServeAgent` 类型。
- **serve 侧删 `GET /agents` 端点**（system.py handler + 路由 + `__init__.py` REST 文档 + server.py 启动 banner）：单元素列表封包（id/engine/shape/status/dataRoot/description）是多 agent 时代为桌面花名册设计的表面；唯一消费者 connectServe 只取 `[0]`，`model`/`capabilities` 改从 `/health`（本就返回）拿。无 Python 测试依赖。

### 文案

- package.json description 单数化；desktop README 删「mock 流式回落」失实句、「能力驱动 UI 按 flag 分支」改如实描述（桌面当前不按 capability 分支）、删 roadmap「codebuddy / 其它引擎类型」；external_engine 表述统一为 claude/codex 双引擎。

## 转换陷阱（复查要点）

- **deleteConversation 回退加载判断用 set 前快照**：扁平版先 `wasActive = get().activeConvId === convId` 再 set，最后 `if (wasActive) loadActiveHistory()`——set 后重读会拿到回退会话而误跳过。
- connectServe 顺序无竞态：set 同步在前，syncConversations 首行读 `serveConnected` 已为 true。
- 持久化零迁移：workspaces.json 只存 workspaces/convWorkspace/windows（convId+tabs），**会话列表不落盘**（每次从 `/sessions` 重建），agents 数组从未进持久化文件。

## 验证

- `pytest` 全量 **542 passed, 20 skipped**；serve 相关 5 文件零改动全绿。
- `npx tsc --noEmit` 0 错；`npm run build` 绿（1m23s）。
- 手动冒烟待跑（`npm run dev`）：首启自动选最近会话、重启恢复上次会话、cron 推送到未加载会话、断线重连 attach、删活动会话回退、工作空间视图行操作。

## 影响

- 破坏性：serve API 删 `GET /agents`（仅本桌面消费，已同步改）；无其它调用方。
- 相关页订正：[[0028]]/[[0029]]（桌面 claude 传输层/双引擎 hub——所述 ClaudeRunner 已不存在，加过时注记）、[[0025]]（Agent 层订正注记更新到终态）、[[0024]]（REST 表删 /agents 行）、[[0026]]（补演进记录：双引擎已撤，external_agent 为唯一外部引擎通道）。

## 相关页面

- [[0026_desktop-agent-model-built-in-xihe]] — Agent 模型定调（本变更是其终态落地）
- [[0040_external-agent-adapter-protocol]] — claude/codex 经 xihe 内核访问的通道（取代桌面并列引擎）
- [[0024_desktop-serve-protocol]] — 协议页（/agents 已删）
