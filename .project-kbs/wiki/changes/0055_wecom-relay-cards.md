---
type: change
title: 企微中继卡体系——审批/clarify 交互卡 + 事件裁决路由 + 协议实测
slug: 0055_wecom-relay-cards
change_type: feature
risk_level: medium
status: completed
created: 2026-09-21
updated: 2026-09-21
affected_modules:
  - src/core/support/approvals.py
  - src/core/support/interaction.py
  - src/core/agent/agent.py
  - src/core/services/scheduler.py
  - src/gateway/platforms/wecom.py
  - src/gateway/bot.py
  - src/tools/clarify_tool.py
related_pages:
  - wiki/concepts/0037_approval-permission-system.md
  - wiki/insights/0056_wecom-aibot-card-protocol.md
---

# 企微中继卡体系——审批/clarify 交互卡 + 事件裁决路由 + 协议实测

## 摘要

企微网关的中继交互卡体系（pytest 相关 118 绿）：**审批按钮卡**（button_interaction：批准/拒绝/总是允许，点击即裁决 + 5 秒窗内卡面变终态 + desc 回填 summary）、**clarify 选择卡**（vote_interaction：单选/多选 + 提交按钮，选项原文随卡登记、提交事件 oN 序号折回原文作答）、**通用路由表**（approvals.py：register_card/card_click/card_meta，按 (kind,id) 精确命中，双击/迟到点击塌缩）、**健康探测持久化**（wecom_cards.json：事件回流过即通道证实，重启不再双发；2 张无事件自动停用回落文本）。文本 y/n/a 通道永远并存。协议实测结论（毒字段/卡型白名单真相/事件结构）沉淀于 [[0056_wecom-aibot-card-protocol]]。

## 变更内容

### 路由核心（core/support/approvals.py）

- `_card_routes` 表：`register_card(kind, id, resolve, meta)` / `card_click(kind, id, choice)` / `unregister_card`（返回处置状态 None/clicked/unclicked，调用方据此决定是否补发文本裁决）/ `card_meta`（summary、选项原文等随卡存取）
- 审批 kind 在锁外先把 event_key 折成 `parse_approval_reply` 语义；未识别 key 不消耗裁决权；与 MidturnHub 按 id 语义一致（桌面端 `_approve` 已验证该模式）

### 协议层（gateway/platforms/wecom.py）

- `send_card`（aibot_send_msg + chat_type=1/2，reply 路径走 respond_msg；无 ack 通道 0.5s 短窗后按已发出）/ `update_card`（aibot_respond_update_msg，req_id 透传，5 秒窗）
- `set_card_event_handler` + `_on_event_callback`：eventtype=template_card_event 分流；按钮数据嵌 `event.template_card_event`；vote/multiple 的选中项在 `selected_items.selected_item[].option_ids.option_id`（`_extract_selected_ids`）
- 未识别 cmd/事件从静默丢弃改为日志（本次排查的关键抓手段）
- 卡片构造纯函数：`build_approval_card` / `build_approval_result_card` / `build_clarify_card` / `build_clarify_result_card`——协议字段疑点全隔离此一处

### 语义层（gateway/bot.py）

- 审批/clarify 四个回调统一模式：`_cards_alive()` 卡优先 → 失败/未证实回落文本；事件路径 5 秒窗内就地更新卡面（respond_update_msg 只认事件 req_id，跨线程必超窗——终态回写只能在点击路径上完成）
- clarify 注册闭包捕获选项原文，`_handle_card_event` 按 oN 序号折回原文作答（多选拼"；"）——模型收到的答案与文本路径完全一致
- 陈旧点击（终态卡上的按钮）静默吞：回"该确认已处理"是对无意义点击的打扰
- `stream_consumer.cut()` 在发卡**之前**（审批/clarify 同款）——cut 阻塞等旧帧定格，发送顺序=思考帧→卡

### 后台审批（core/services/scheduler.py）

- cron 审批卡优先（`_send_card_to_chat` 线程→loop 跨越同 `_send_to_chat`），`register_card`+`register_pending` 双路由并存同一 resolve（MidturnHub 幂等），结果回调按处置状态去重

### clarify 工具（tools/clarify_tool.py）

- schema 加 `multi_select`（模型声明多选）——payload 经 MidturnHub 透明透传到卡片 checkbox.mode；description 压缩为三句，写明"确认场景也走 clarify"+"消息渠道上选项渲染为卡片，永不声称卡片不可用"（堵模型幻觉"卡片通道未启用"）

## 变更分析

### 为什么事件不走「转文本注入 MessageEvent」

调研文档建议 event_key 转文本塞进现有通道复用裁决链。否决：审批超时/已裁决后的迟到按钮点击会兜不住——裸 "y" 排队成新 turn，模型收到垃圾消息；按钮把误触窗口放大到 72 小时。改为 task_id 精确路由 + 已裁决塌缩 + 迟到点击静默。

### 双发与探测

send_card 是"发出即成功"语义（渲染成败发送侧永远看不见）。未证实期卡+文本双发（否则探测期审批无声挂到超时）；事件回流（唯一可靠的通道活性证据）落盘 wecom_cards.json，重启不重付双发噪音；发满 2 张 0 事件 → 停用回落纯文本（自动，无配置开关——静态开关的体验是"坏了自己不知道"）。

### 卡片时序与渲染

- clarify 卡主动推送不带 reply_to：reply 引用消息被客户端锚到会话上方，压过思考进度帧
- 审批终态按钮无 disable 字段（Button 结构体没有）——防重复点击靠路由表塌缩+终态文案

## 验证

- `tests/test_relay_cards.py` 17 用例：卡片形状契约（毒字段反向断言）、路由语义（裁决/塌缩/处置状态/meta 往返）、选项折算、selected_items 抽取
- 实测闭环（真凭证实测，见 [[0056_wecom-aibot-card-protocol]]）：审批卡点按钮→事件→卡面终态；clarify 单选/多选提交→原文作答→卡面"已收到回答"→选项区锁死

## 相关页面

- [[0056_wecom-aibot-card-protocol]] - 协议实测结论（毒字段/白名单/事件结构/单连接互顶）
- [[0037_approval-permission-system]] - 审批体系本体
