---
type: change
title: 可视化体系——skill 落地、桌面 html renderer 与本地图片渲染、image_render 统一工具
slug: 0051_visualization-image-render
change_type: feature
risk_level: medium
status: completed
created: 2026-09-16
updated: 2026-09-16
affected_modules:
  - src/skills/visualization/
  - src/tools/image_render_tool.py
  - src/tools/assets/mermaid.min.js
  - src/core/toolsets.py
  - src/core/agent/prompts.py
  - src/gateway 无改动（能力全在 tools + desktop）
  - desktop/src/renderer/src/components/HtmlRenderer.tsx
  - desktop/src/renderer/src/components/Markdown.tsx
  - desktop/src/renderer/src/components/ChatPanel.tsx
  - desktop/src/renderer/src/lib/localImage.ts
  - desktop/src/main/index.ts
related_insights:
  - wiki/concepts/0046_shared-session-terminal.md
---

# 可视化体系——skill 落地、桌面 html renderer 与本地图片渲染、image_render 统一工具

## 摘要

补齐可视化能力并按"三模式同步考虑"硬规则（同日写入 CLAUDE.md Gotchas 首条）适配三种运行模式：调研豆包 doubao-visualization 后落地 **visualization skill**（触发白名单/数据真实性/输出结构借鉴豆包，交付通道按 xihe 现实改写）；桌面聊天新增 **html renderer 沙箱渲染**与**本地图片渲染链（xfile:// 协议）**；新增统一工具 **image_render**（四源确定性出图，转图定位为"无渲染能力通道的补偿手段"）。pytest 568 绿 + tsc 0 错 + 桌面 build 绿。

## 变更内容

### 1. visualization skill（src/skills/visualization/SKILL.md v1.2）

- **调研来源**：doubao-visualization（IWanalq/doubao-office-skills 提取）——借鉴触发白名单/反触发（地图绝对禁止）、数据真实性（无数据不编数、示例结构必须标注）、交替输出结构（结论→图→数据表→解读口径）、稳定优先哲学；WH-2099/mermaid-skill 等 mermaid 手册派补充语法陷阱节。
- **通道**：A mermaid（桌面原生渲染 v11）/ B GFM 表格 / C `html type="renderer"` 沙箱块 / D HTML 文件（明确要文件时）。
- **三模式路由**（description 硬规则，模型不打开正文也能看到）：桌面默认 mermaid/renderer 原生；mermaid 表达不了的（**横向条形 mermaid 没有**、精确多系列）才 image_render；网关 image_render + send_image；CLI 表格优先。

### 2. 桌面 html renderer（Markdown.tsx + HtmlRenderer.tsx）

- ` ```html type="renderer" ` 围栏（info string 含 `renderer` 关键字，读 code 节点 `data.meta`）→ 沙箱 iframe 就地渲染：`sandbox="allow-scripts"`（无同源、无导航）、白底。
- **高度自适应**：向 srcdoc 注入上报脚本（load + ResizeObserver → postMessage 高度），父窗口校验 `e.source` 后设高（160–640px，超高内部滚动）。普通 ` ```html ` 仍显示源码。

### 3. image_render 工具（src/tools/image_render_tool.py）

- **合并历程**：先有 render_chart（matplotlib）+ render_image（无头截图）两个工具，按"少而精"原则合并为一个 `image_render`（`source: data/mermaid/html/markdown` 判别字段）；命名对齐 image_generate/image_ocr 家族。
- data 源：matplotlib（`_plot_lock` 防 pyplot 线程不安全；中文字体链 YaHei→SimHei→Noto）；内容源：**独立无头 Playwright 实例**（chrome→msedge→bundled，同 browser_tool 偏好但不碰交互浏览器），mermaid 经 vendor 的 `src/tools/assets/mermaid.min.js`（3.5MB，`</script>` 检查 0 处；`--add-data` 进冻结包）内联注入 + `window.__done` 完成信号。
- 落点 `AGENT_HOME/charts`（曾误用 agent_base_dir 落到 serve cwd）；结果带 `success=True`；hint 按平台分派（网关→send_image / serve→正文必须提路径且禁 `![](...)` / CLI→补表格）。
- 依赖：matplotlib、markdown（requirements + pyproject 双写）。

### 4. 本地图片渲染链（xfile:// 协议）

- main：`registerSchemesAsPrivileged`（ready 前）+ `protocol.handle('xfile')`——**仅图片扩展名**放行，`net.fetch(pathToFileURL(...))` 服务本地文件。
- renderer：`lib/localImage.ts` 把本地绝对路径/`file://` 转 `xfile://local/<整段 encodeURIComponent>`；Markdown img 覆盖（scheme 过滤：仅 xfile/http/data/blob 渲染，烂路径直接不渲染；`onError` 自隐藏）。
- **附件条**（ChatPanel）：回复正文里的绝对图片路径渲染为消息下方图片——**正文单源**（内容即持久化真相，live 与刷新后行为一致；曾用 trace∪正文双源，会 live 显示刷新消失，删 trace 源）。

### 5. 平台提示（prompts.py）

wecom/feishu platform prompt 补 send_image 引导（charts/diagrams: image_render first, then send_image）。

## 踩坑存档

1. **自定义协议 URL 双坑**：裸盘符 `xfile:///C:/...` 的 `C:` 被当 host 吞掉；空 host 直接判非法 URL（standard scheme 要求非空 host）。解法 = 占位 host + 整段 percent-encode。独立 Electron 最小实例验证法值得复用。
2. **markdown 反斜杠转义吃路径**：模型写 `![](C:\Users\...)` 时 `\.` 等被转义消费，路径烂成 `C:Userszzmao...`——所以桌面禁止模型写嵌图语法，显示走正文路径 + 附件条。
3. **模型 hint 服从性**：工具 description 与结果 hint 信号矛盾时模型听 description；hint 措辞要给"可照抄的确切行为"而非开放指令。
4. mermaid v11 无横向条形图（xychart-beta 只有竖向 bar + line）——"mermaid 表达不了"是 image_render 在桌面的合法出口。

## 回滚

单批次 git revert（python tools/skill + desktop renderer/main 同批）。serve 与桌面需重启。

## 已知边界

- 正文提路径依赖模型服从（hint 为 MUST；实测路径必提，失败均为嵌图语法问题）。
- 冻结打包环境（build-cli.ps1 CoreDeps）不含 matplotlib——source=data 明确报错，内容源正常。
- 历史会话中的烂 `![](...)` 消息不显示图（scheme 过滤挡住空标记，无视觉污染）。
