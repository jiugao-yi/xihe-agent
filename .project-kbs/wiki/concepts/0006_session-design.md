---
type: concept
title: 会话两层 ID 设计
slug: 0006_session-design
aliases:
  - SessionDB
  - session_key
  - SessionSource
tags:
  - architecture
  - session
  - core
status: active
created: 2026-07-01
updated: 2026-09-15
related_pages:
  - wiki/entities/0001_xihe-agent.md
  - wiki/changes/0020_session-management-commands.md
sources:
  - path: raw/sources/session-design.md
    date: 2026-07-01
---

# 会话两层 ID 设计

## 摘要

会话用**两层 ID**: `session_key`（逻辑标识，「哪段对话」，确定性）+ `session_id`（物理实例，「哪一轮」，重置后变化）。所有消息存 `session_id`，`session_key` 只做路由、映射到当前活跃 `session_id`。对外只暴露 source-only API（`get_or_create_session(SessionSource)`），key 的生成内部封装。会话 key 同时是 cron job / 中断的单元。

## 核心要点

- **`session_key`（逻辑）**: 由消息来源确定性计算，同一对话永不改变。
  格式: `agent:main:{platform}:{chat_type}:{chat_id}[:{thread_id}][:{user_id}]`，例 `agent:main:wecom:dm:chat123`。
- **`session_id`（物理）**: `{YYYYMMDD_HHMMSS}_{uuid8}`，重置时换新。
- **`chat_type`**: `dm`（Direct Message 私聊，按 chat_id 隔离）/ `group`（按 chat_id+user_id，若 `group_sessions_per_user`）/ `channel`（按 chat_id）/ `thread`（按 thread_id，默认跨用户共享）。
- **`thread_id`**: 子话题 ID（Telegram topic / Discord thread / Slack thread_ts）。（订正 2026-09-14：原本文档的「DM thread seeding——新建 thread 会话自动拷父 DM 历史」**并未实现**，thread 会话从空白开始。）
- **`build_session_key(source)` 规则**:
  - DM: 有 chat_id+thread_id 用两者；只有其一用其一；都没有 → `...:dm`。
  - 群/频道: base `...:{chat_type}:{chat_id}`，可选追加 `:{thread_id}`；群隔离追加 `:{user_id}`。
- **`SessionSource`**: 消息来源数据类（platform / chat_id / chat_name / chat_type / user_id / user_name / thread_id）。平台适配器的 `MessageEvent.to_session_source()` 构造它；`XiheAgent.chat(source, msg, ...)` 只收 source。
- **特殊来源**: cron → `SessionSource(platform="cron", chat_id="cron_{job_id}_{ts}")`；delegate → `platform="delegate"`；CLI → `platform="cli"`；serve/桌面 → `platform="serve", chat_id=conv_id, user_id="desktop"`（key `agent:main:serve:dm:{conv_id}`，见 [[0024_desktop-serve-protocol]]）。
- **重置策略** (`config.yaml` 的 `session` 段): `none`（**默认**，2026-09-14 起——同一会话永久延续）/ `idle`（`idle_minutes` 分钟不活动，默认 1440）/ `daily`（每日 `daily_reset_hour` 点，默认 4）/ `both`。per-platform 覆盖在 `session.platforms.<name>`（子键 `reset`/`idle_minutes`/`daily_reset_hour`）。重置 = 换新 session_id：模型上下文从空白开始；**转录视图跨轮可见、标题/model 保留**（2026-09-15 起，messages 带 session_key 列）。详见 [[0048_sessions-messages-reset]]。
- **per-session model 覆盖**: sessions 表 `model` 列 + `get/set_session_model`，`/model` 切换跨「每消息新 agent」存活。
- **system prompt**: 每轮 `_build_system_prompt` 重建 + 记忆快照在 API 边界注入（不落库、不缓存到 SQLite——sessions 表无 `system_prompt` 列；订正原文档）。

## 适用场景

- 新增平台适配器: 实现 `MessageEvent` + `to_session_source()`，不要手动拼 session_key。
- 调整会话隔离粒度: 改 `group_sessions_per_user` / `thread_sessions_per_user`。
- 排查「会话串了」: 检查 chat_type / thread_id / user_id 是否参与了 key 生成。

## 涉及文件

`core/session.py`（SessionSource / build_session_key / ResetPolicy / SessionEntry / SessionDB）/ `gateway/platforms/base.py`（MessageEvent）/ `gateway/platforms/wecom.py`/`feishu.py` / `gateway/bot.py` / `gateway/commands.py` / `core/services/scheduler.py` / `tools/delegate_tool.py` / `cli/chat.py`。表结构与数据流细节见 [[0048_sessions-messages-reset]]。

## 相关页面

- [[0001_xihe-agent]] — 会话是 agent loop 的持久化基础
- 原文快照: [raw/sources/session-design.md](../../raw/sources/session-design.md)
