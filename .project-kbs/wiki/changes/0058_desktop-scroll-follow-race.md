---
type: change
title: 桌面流式滚动竞态修复——用户滚动意图立即解除贴底 + 程序化滚动不洗白贴底标记
slug: 0058_desktop-scroll-follow-race
change_type: fix
risk_level: low
status: completed
created: 2026-09-23
updated: 2026-09-23
affected_modules:
  - desktop/src/renderer/src/components/ChatPanel.tsx
related_pages:
  - wiki/changes/0053_architecture-refactor.md
---

# 桌面流式滚动竞态修复

## 摘要

用户报「划动到上面看历史消息，下面对话输出后把页面拉到最下面去了」。根因是**竞态**而非贴底跟随缺失：`stickRef`（上滑豁免）靠 `onScroll` 事后清点距底距离解除，但流式 delta 每 ~50ms flush 一次，每次 flush 的 `scrollIntoView` 拽回底部后，这次**程序化滚动自己触发 onScroll → 距底≈0 → stickRef 重新置 true**——用户的手势被程序化滚动"洗掉"，除非在 flush 间隙滑出且恰好不被拽回。流式越快越不可能上滑成功。tsc 0 错 + build 绿。

## 变更内容（ChatPanel.tsx）

1. **用户滚动意图事件立即解除贴底**：`onWheel`（deltaY<0）/ `onTouchMove`（上滑触摸，lastTouchY 对比）/ `onKeyDown`（PageUp/ArrowUp/Home）任一向上意图 → `stickRef.current = false`，不等 onScroll 战后清点——手势不再和程序化滚动赛跑
2. **`programmaticScroll` 一次性标记**：每次 `scrollIntoView` 前置位，`onScroll` 消费（one-shot）——程序化拽回落底产生的"距底≈0"不再能把贴底模式重新武装
3. **`onScroll` 重写**：rollback 内容塌缩豁免保留（distance > clientHeight → 视为程序化跳变置 true）；距底 <80px 仅在**非程序化**滚动时重新武装——只有用户自己滚回底部才恢复跟随
4. 切换会话自动跳底逻辑不动（`activeConvId` effect 双 rAF + programmaticScroll 标记）

## 踩坑

- **程序化滚动会 launder onScroll 判定**：scrollIntoView 触发的 onScroll 与用户滚动在事件流里不可区分（只看 scrollTop/scrollHeight），必须在源（调用前）打标记、在消费点（onScroll）一次性核销，才能把"谁滚的"区分开
- 原实现的 `distance > clientHeight` rollback 豁免是对的但不够——它只防了"内容塌缩"一种程序化跳变，没防"程序化拽回底部"这种

## 相关页面

- [[0053_architecture-refactor]] - ChatPanel 所在的桌面渲染层架构批次
