---
type: insight
title: 企微智能机器人长连接卡片协议实测结论
slug: 0056_wecom-aibot-card-protocol
tags:
  - wecom
  - template-card
  - gateway
  - 协议实测
status: active
created: 2026-09-21
updated: 2026-09-21
confidence: high
sources:
  - path: raw/sources/xihe-agent × 企业微信集成方案（调研 + 设计）.md
    date: 2026-09-21
related_stories:
  - wiki/changes/0055_wecom-relay-cards.md
---

# 企微智能机器人长连接卡片协议实测结论

## 摘要

airgap 环境无法核对企微文档，卡片协议经七轮 spike 真连接实测（2026-09-21）+ 官方文档逐条对照，得出以下**文档没写或与文档冲突**的结论。全部结论已固化在 `src/gateway/platforms/wecom.py` 注释；实现见 [[0055_wecom-relay-cards]]。

## 毒字段：静默丢弃不报错

服务端校验层认识卡片结构（错误 schema 报 errcode 42014），但以下情况**校验放行、投递层静默丢弃**（ack 正常、客户端无渲染、无任何错误信号）：

| 毒点 | 说明 |
|---|---|
| `task_id` 含冒号 | 二分实验坐实：`approval:x` 不渲染、`approval-x` 渲染。官方文档说 task_id 允许 `数字字母_-@`——冒号确实非法，但**不报错**。编码一律用连字符 |
| `source` 字段 | 带任意 source（含 desc）整卡不渲染（button/text_notice 型均验证） |
| **错误 schema 的 vote/multiple 卡** | 顶层 `option_list` 不属于任何已知卡型（vote 的选项在 `checkbox` 子对象、multiple 在 `select_list` 三层嵌套）——**这是"卡型白名单"假设的真相**：不是通道禁卡，是 schema 错了 |

**方法论**：静默丢弃掩盖一切 schema 错误。定位手段只有二分变体实验（同一路径连发 N 张结构递减的卡，对照哪张渲染）。

## 卡型可用性（button_interaction 一族实测可用）

| 卡型 | 状态 | 关键点 |
|---|---|---|
| `button_interaction` | ✅ 渲染+事件+更新全通 | button_list `{text, style(1~4), key}`；无 disable 字段；文案实测 >4 字截断 |
| `vote_interaction` | ✅（官方 schema） | 选项在 `checkbox{question_key, option_list[{id,text,is_checked}], mode(0单选/1多选)}` + 必配 `submit_button{text,key}`；提交事件 `event_key=submit_button.key`，选中项在 `selected_items`；`checkbox.disable` 仅更新时有效（锁定已提交的卡） |
| `multiple_interaction` | ✅ 渲染（官方 schema） | `select_list[]` 三层嵌套 + submit_button；clarify 场景 vote 语义更贴，未采用 |
| `text_notice` / `news_notice` | ❌ 未验证渲染成功 | 曾因 source 字段误判"卡型被禁" |

## 事件结构（template_card_event）

```
body.event.eventtype = "template_card_event"
body.event.template_card_event.{
  card_type, task_id, event_key,
  selected_items.selected_item[].{question_key, option_ids.option_id[]}  ← 仅 vote/multiple
}
```

- 按钮卡的 `event_key` = 被点按钮的 `key`
- vote/multiple 的提交事件 `event_key` = `submit_button.key`（固定值），**选中项不在 event_key 里**，在 `selected_items`（XML 风格嵌套数组命名）

## 卡片更新（aibot_respond_update_msg）

- 命令：`aibot_respond_update_msg`，`headers.req_id` **透传卡片点击事件的 req_id**，`body.response_type: "update_template_card"` + 完整 template_card
- **5 秒窗口**（官方明示）：终态回写只能在点击事件处理路径上完成，跨线程等结果回调必超窗
- **task_id 必须原样回传**：换新 task_id 服务端匹配不到目标卡——ack 照发但卡面不重绘（假成功）
- `response_code` 回写机制（旧文档/回调 URL 模式）在长连接模式不存在；事件里的 `response_url` 是备用通道，未采用

## 连接与通道

- **一个 bot 一条长连接**（官方明示）：新连接踢旧连接，旧连接收 `disconnected_event`。spike/多进程共用凭证会互顶、消息丢失——排查一切"没收到"问题先查连接独占
- 卡片发送**可能无 ack**（等待即 Timeout）：0.5s 短窗后按已发出处理；渲染成败靠健康探测兜底（事件回流=通道活）
- `aibot_send_msg` 的 `chat_type`：1 单聊/2 群聊，**不填按群聊优先解析**——单聊 userid 不带 chat_type 可能静默丢弃
- 心跳 ping 的 ack 帧无 cmd 无 req_id（30s 一条），dispatch 需降为 debug 防日志噪音
- `aibot_event_callback` 是共享事件通道：disconnected_event / enter_chat / template_card_event / feedback_event 全走它，按 `eventtype` 分流

## 调研文档的教训

`raw/sources/` 那份调研文档（部分 AI 生成）的卡片示例把 vote 卡字段（question_key/option_list）混进 button 卡、建议的"event_key 转文本注入"路由会放大迟到点击误触、response_code 回写机制在长连接模式不存在——**AI 生成的协议代码/文档必须逐字段对官方文档+真连接实测，"长得对"和"跑得通"之间隔着整个传输层**。

## 相关页面

- [[0055_wecom-relay-cards]] - 承载这些结论的实现
- [[0037_approval-permission-system]] - 审批体系本体
