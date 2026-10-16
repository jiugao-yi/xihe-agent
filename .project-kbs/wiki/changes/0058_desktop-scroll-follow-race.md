---
type: change
title: 桌面体验批——流式滚动竞态修复 + trace 子代理展示体系 + 输入/快捷键/文件查看
slug: 0058_desktop-scroll-follow-race
change_type: fix
risk_level: low
status: completed
created: 2026-09-23
updated: 2026-09-28
affected_modules:
  - desktop/src/renderer/src/components/ChatPanel.tsx
  - desktop/src/renderer/src/components/TurnTrace.tsx
  - desktop/src/renderer/src/components/EditorArea.tsx
  - desktop/src/renderer/src/components/FileTreePanel.tsx
  - desktop/src/renderer/src/components/FileOverlay.tsx
  - desktop/src/renderer/src/components/FindBar.tsx
  - desktop/src/renderer/src/App.tsx
  - desktop/src/main/index.ts
  - desktop/src/preload/index.ts
related_pages:
  - wiki/changes/0053_architecture-refactor.md
  - wiki/changes/0057_context-compression-v32.md
---

# 桌面流式滚动竞态修复

## 摘要

用户报「划动到上面看历史消息，下面对话输出后把页面拉到最下面去了」。根因是**竞态**而非贴底跟随缺失：`stickRef`（上滑豁免）靠 `onScroll` 事后清点距底距离解除，但流式 delta 每 ~50ms flush 一次，每次 flush 的 `scrollIntoView` 拽回底部后，这次**程序化滚动自己触发 onScroll → 距底≈0 → stickRef 重新置 true**——用户的手势被程序化滚动"洗掉"，除非在 flush 间隙滑出且恰好不被拽回。流式越快越不可能上滑成功。tsc 0 错 + build 绿。

## 变更内容（ChatPanel.tsx）

1. **用户滚动意图事件立即解除贴底**：`onWheel`（deltaY<0）/ `onTouchMove`（上滑触摸，lastTouchY 对比）/ `onKeyDown`（PageUp/ArrowUp/Home）任一向上意图 → `stickRef.current = false`，不等 onScroll 战后清点——手势不再和程序化滚动赛跑
2. **`programmaticScroll` 一次性标记**：每次 `scrollIntoView` 前置位，`onScroll` 消费（one-shot）——程序化拽回落底产生的"距底≈0"不再能把贴底模式重新武装
3. **`onScroll` 重写**：rollback 内容塌缩豁免保留（distance > clientHeight → 视为程序化跳变置 true）；距底 <80px 仅在**非程序化**滚动时重新武装——只有用户自己滚回底部才恢复跟随
4. 切换会话自动跳底逻辑不动（`activeConvId` effect 双 rAF + programmaticScroll 标记）
5. **滚动条拖拽盲区补丁**（09-28，第一版漏的第四通道）：拖滚动条不触发 wheel/touch/key——pointerdown 在容器右缘 16px 内标记拖拽中，拖拽期间 onScroll 离底 ≥80px 即解除跟随

## 踩坑

- **程序化滚动会 launder onScroll 判定**：scrollIntoView 触发的 onScroll 与用户滚动在事件流里不可区分（只看 scrollTop/scrollHeight），必须在源（调用前）打标记、在消费点（onScroll）一次性核销，才能把"谁滚的"区分开
- 原实现的 `distance > clientHeight` rollback 豁免是对的但不够——它只防了"内容塌缩"一种程序化跳变，没防"程序化拽回底部"这种
- **滚动条 gutter 是事件盲区**：意图通道按输入设备枚举时漏了拖拽——滚动条拖拽的 pointer 事件目标不在内容区

## trace 子代理展示体系（2026-09-24~28 追加，TurnTrace.tsx）

多轮实测驱动（「并行 subagent 思考交错刷屏」「怎么展示成 subagent-1 0个工具 ×7 段」「工具调用跟主 agent 分不清」），最终形态经业界调研校准（Claude Code 后台 spinner / Manus 活动侧栏 / Cursor 收件箱——共性：**无人做多路同时直播，默认低密度状态 + 按需单焦点**）：

1. **两级分组模型**：Pass-1 时序切段（连续同 `by` 成段 + 段内思考合并成段文字，delta 分片边界换行归一）；Pass-2 并行穿插坍缩（同源多段合成一块——三子代理毫秒级交错不会碎成 N 个时间切片）
2. **子代理仪表盘**（Manus 模式轻量版）：每个子代理一行状态条（✓/⟳/✗ + 名称 + 工具计数 + 最新工具调用一句话）= 全局感层；**点行展开单焦点详情**（一次一个），展开区含合并思考 + 工具行；完成后自然收敛紧凑行
3. **委派点位置保真**：仪表盘挂在**它所属的委派工具行**之后（逐块游走 + 挂起冲刷），主代理"思考→委派→拿结果→再动→汇总"叙事不断——用户实测纠正过"全部堆在最后面"的错误首版
4. **视觉区分**：子代理行 child 样式（无底色工具名/11px 淡色思考/缩进）+ **来源色条**（4 色循环：状态行名称与展开区 2px 左边条同色——颜色回答"谁"、轻重回答"主次"）；行级 ByBadge 删除（分组后冗余）
5. **历史 = 实时**：children 下钻事件（by 同构）直接进同一渲染，刷新后一致

**设计原则沉淀**：模型侧表达并行意图（协议原生多 tool_call），调度/展示侧验证副作用安全后兑现；仪表盘一个结构同时解决"怎么看并行"与"主/子怎么分辨"两个问题。

## 输入与快捷键（ChatPanel.tsx / App.tsx / FindBar.tsx）

- **Ctrl+F 页内搜索**：主进程 before-input-event 拦截 → renderer FindBar（app 风格搜索条）→ `webContents.findInPage`（Chromium 原生高亮）；已知边界：只搜已渲染 DOM，折叠 trace/虚拟化外消息不可见
- **Enter 语义定案**：裸 Enter 发送，Shift+Enter / Ctrl+Enter 换行（消息编辑框同规则）——placeholder 明示
- **重新生成去掉两步确认**：直接触发（title 保留后果提示）

## 工作台与文件查看（EditorArea / FileTreePanel / FileOverlay / App.tsx）

- **工作空间绑定入口**：头部工作台/文件树按钮未绑定时从置灰死胡同改为点击弹工作空间选择浮层（选择即绑 + 自动进 workbench；含取消绑定/无空间引导）——绑定动作 store 层 `bindConversationWorkspace`（~15 行，纯元数据，历史轮不重解释）
- **图片 tab**：工作台打开 png/jpg/svg 等走 xfile:// 特权协议 + 棋盘格透明底，不进文本管线
- **Markdown 预览/源码切换**：.md 文件右上角切换钮，默认预览（复用聊天 Markdown 组件，mermaid/代码高亮全带），预览渲染当前草稿、与编辑/保存联动
- **文件覆盖层快览**（非工作空间布局）：文件树抽屉加 ⤢ 放大 → 92% 内容区覆盖层（图片/MD/Monaco 只读 + Esc/遮罩关闭）——"快速看一眼"与"正式编辑"两条路径分离；IDEA 调研结论：开发工具嵌完整 Office 渲染低性价比，Office 预览明确不做（agent office 工具照常读写）

## 相关页面

- [[0053_architecture-refactor]] - ChatPanel 所在的桌面渲染层架构批次
- [[0057_context-compression-v32]] - 委派/trace 链的服务端配套（children 下钻数据源、bridge 修复）
