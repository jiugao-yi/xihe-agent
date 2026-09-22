---
type: change
title: 终端体系整备——agent 终端自动打开收敛、打印治乱、快速连接/本地 tab 移除、Run 面板改通用本地终端
slug: 0050_terminal-panel-overhaul
change_type: feature
risk_level: medium
status: completed
created: 2026-09-15
updated: 2026-09-15
affected_modules:
  - desktop/src/renderer/src/appStore.ts
  - desktop/src/renderer/src/components/TerminalPanel.tsx
  - desktop/src/renderer/src/components/ShellPanel.tsx
  - desktop/src/renderer/src/App.tsx
  - desktop/src/main/terminals.ts
  - desktop/src/main/run.ts
  - desktop/src/main/localPty.ts
  - desktop/src/preload/index.ts
  - desktop/src/renderer/src/lib/desktop.ts
  - desktop/src/renderer/src/lib/serveClient.ts
  - src/tools/_local_tap.py
  - src/tools/terminal.py
  - src/tools/process_tool.py
  - src/tools/ssh_tool.py
  - src/tools/_ssh_tap.py
  - src/gateway/serve/terminal.py
related_insights:
  - wiki/concepts/0046_shared-session-terminal.md
  - wiki/changes/0043_desktop-workbench-phase1.md
---

# 终端体系整备——agent 终端自动打开收敛、打印治乱、快速连接/本地 tab 移除、Run 面板改通用本地终端

## 摘要

用户报「terminal 自动打开很吵、Agent 标签里打印乱」，检视后按三期落地；后续追加减法（快速连接、本地 tab）与 Run 面板重做。核心方向：**agent 终端只在"有可看的活动"时打扰人**；**本地 shell 与对话/工作区彻底解耦**，独立成通用多开终端；打印侧治双流交错与 GBK 乱码。pytest 558 绿（后续 562 绿含其他改动）+ tsc 0 错 + 桌面 build 绿。

## 变更内容

### 1. agent 终端自动打开收敛（桌面 appStore/TerminalPanel）

- **触发收窄**：`ssh_connect`、`process action=start` 才立即弹（原来 4 个 ssh 工具 + 任何 process action 都弹）；`terminal` 改**延时打开**——tool_call 起 5s 倒计时（`TERM_OPEN_DELAY_MS`），该对话的 tool_result 到达即取消，超时才弹并聚焦 Agent tab。秒级命令永不弹（输出在聊天工具卡里），长构建到点弹开、attach 回放覆盖弹出前的输出。
- **审批暂停**：terminal 调用卡审批时倒计时挂起（`termArmAfterApproval`），批准后重计（命令此刻才真正开始跑），拒绝随 tool_result 取消。
- **静音改粘性**：`terminalAutoMuted` 不再随 sendMessage 重置——手动关 = 本会话不再自动弹，手动重开才恢复（浏览器面板保持按轮重置不变）。
- **不抢 tab**：TerminalPanel 加选中归属 `selOwnerRef`（'auto'→'user'），用户点过任何 tab 后 `agentTermTarget` 直接丢弃；面板 remount 重置。
- **turn 结束清理**：清残留倒计时 + 未解析的 proc target（原来 `process action=status` 之类设的挂起目标可能很晚误抓无关新频道）。

### 2. conv 通道打印治乱（serve `_local_tap.py`）

- 头部一行化：`—— $ {命令}`，**cwd 只在与上一条不同时显示**（Channel 记 `self.cwd` 比较）；命令内换行折成 `⏎`、超 160 字符截断加 `…`（`fmt_command`）。
- **LineTap**：读线程只发布到最后一个 `\n`/`\r` 边界——stdout/stderr 两线程交错时整行不被截断，`\r` 进度条保持实时刷新；尾巴随下一 chunk 或 EOF 发出。terminal/process 两个工具的 `_drain` 都换用它（捕获列表仍全文，模型结果不受纪律影响）。
- **StreamDecoder**：严格 utf-8 增量解码，遇非法序列一次性切宿主码页（GBK）。**坑：`locale.getpreferredencoding()` 在 UTF-8 模式下返回 utf-8（恰在需要回退时说谎），必须用 `locale.getencoding()`**（3.11+，3.10 有 shim）。

### 3. 减法：快速连接 + 本地 tab

- **快速连接整链删除**（连接统一交给 agent）：面板表单 → serveClient `sshConnect` → serve `POST /ssh/live/connect` 端点 → `ssh_tool` 的 `origin`（agent/desktop 来源标记）全链路拔除（origin 唯一用途就是标记桌面发起）。ssh 会话里**手敲仍可用**（面板仍是第二键盘）。
- **本地 tab 删除**：`__local__` 哨兵、`main/localPty.ts`、5 个 localPty IPC、preload/desktop.ts 的 localPty API 全删。本地 shell 能力迁移到 ShellPanel（见 5）。

### 4. tab 可见性 + viewer 不建频道

- **watchable 规则**：频道 tab = 当前对话的频道 ∨ 有活跃运行（命令在跑/常驻进程活着）；后台对话的空闲频道（纯历史）不占位，其对话被打开时重新出现（输出从 ring 回放）。选中不可见时自动清。
- **viewer 永不创建频道**：serve `local_live_stream` 删掉「`conv:` key 不存在按需建空通道」分支，直接 404。修的 bug：面板默认选当前对话控制台 + serve 按需创建 = **开着面板切换对话就孵化空 Agent tab**（用户报「怎么这么多 agent tab」的根因之一）。
- 默认选中改存在性判定：当前对话的频道已存在才 seed，否则空态。

### 5. Run 面板 → ShellPanel（通用本地终端）

- 定位重做：**IDEA 式终端**——多 tab（+ 新建、× 关闭、死 tab 重启）、完整交互（stdin 接通）、**不绑 workspace**（按钮常可用；绑了工作空间时新 tab 的 cwd 只作种子）。与对话无关。
- `main/terminals.ts`（新，替代删除的 `run.ts`）：多会话 ConPTY 注册表，每会话 16K ring + 字符游标（`w`）；`termAttach` 返回回放快照、事件带游标——切 tab 回来不重不漏。`run.ts` 的一次性执行 + per-workdir 历史随之删除（shell 内 PSReadLine ↑ 历史覆盖）。
- 布局：**窗口级全宽底部 dock**（两种布局一致）——曾试挂编辑器区下方，用户指出"本地 shell 跟工作区无关"后定稿：谁的地盘都不进。
- Play 入口升级：文件树/编辑器 ▶ = 在新 tab 里执行命令，**起出来的程序可交互**（`shellCmdDraft`）。
- store 更名：`showRun→showShell`、`runCmdDraft→shellCmdDraft`、`openRunPanel→openShellPanel`。

### 6. 命名与文案

- 顶栏按钮：**agent终端**（SquareTerminal，"对话的命令与 SSH 会话实时输出"）在前、**本地终端**（Terminal 图标）紧随其后。
- 空态/提示文案与行为对齐：agent 终端空态如实写"SSH 连接后新增标签页，点击查看"（ssh 不自动选中是有意的不抢 tab）。
- README「Why xihe」补终端条目（此前无 agent 终端说明）。

## 验证

- pytest 558→562 passed（少的 4 个 = 删除的 /ssh/live/connect 用例；两个断言旧头部格式的用例随契约更新）
- tsc --noEmit 0 错 ×多轮；桌面 electron-vite build 绿
- 临时脚本实测后删：LineTap 部分行 held/整行发布、GBK 跨 chunk 分割解码、cwd 省略/⏎ 折叠/截断

## 回滚

单批次 git revert 即可（桌面 renderer/main + serve 三处同批）。注意回滚后 serve 需重启、桌面需重载。

## 已知边界

- Run 面板时代的历史文件 `~/.xihe-desktop/run-history.json` 成为死数据（不清理无影响）。
- agent 终端 ssh_connect 弹开抽屉时不自动选中会话 tab（不抢 tab 约定的代价；如需可给 agentTermTarget 加 ssh 类型目标）。
