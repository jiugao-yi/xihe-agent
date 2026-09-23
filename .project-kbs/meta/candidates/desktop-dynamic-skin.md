---
type: candidate
title: 候选：桌面端动态皮肤（背景/主题）功能
slug: desktop-dynamic-skin
status: open
created: 2026-09-15
updated: 2026-09-15
related_topic: 0025_desktop-control-plane
derived_from:
  - raw/sources/dsh-dynamic-skin.md
why_it_matters: 调研完整、实现路径清晰且对现有 UI 零侵入，但价值偏锦上添花；立项与否待定，先存结论避免重复调研。
next_action: 决定是否立项；若做，展开皮肤 manifest schema + serve 暴露接口 + 桌面 BackgroundStage 组件设计。
---

# 候选：桌面端动态皮肤（背景/主题）功能

> **状态**: open（待决定）
> **日期**: 2026-09-15
> **调研结论**: 路线已明确——「皮肤是数据不是代码」+ 皮肤目录协议 + 桌面内置渲染组件，不引入插件运行时。对现有 UI 的影响完全可控（默认保守时关掉皮肤=像素级回到今天）。做不做未决。

## 背景与调研对象

DSH（DeepSeek Harness）生态的动态皮肤玩法有三条路线，完整快照见 `raw/sources/dsh-dynamic-skin.md`：

| 路线 | 代表 | 形态 |
|---|---|---|
| 宿主内插件 | dsh-live-wallpaper | 插件装进 DSH Web UI，页面内挂载点；离线 WebGL2 shader + ShaderToy + 主题中心 |
| 外部 core + adapter | BeautiCode | 独立进程，同时服务 DSH 与 Codex Desktop；core（媒体存储/事务/校验/媒体服务器）+ 各宿主 adapter |
| 桥接 | Wallpaper Engine 插件 | 把本机 WE 壁纸转成 Web UI 背景 |

## 可搬到 xihe 的核心机制

1. **皮肤 = 声明式数据 + 宿主内置渲染器**（最重要）。皮肤包只声明素材 + 调色板 + 参数，渲染统一由宿主 stage 完成。对应 xihe：`~/.xihe-agent/skins/<name>/skin.yaml` manifest（type: video|image|shader + palette + overlay 参数），素材拷贝进皮肤目录。与现有结构同构：皮肤包↔技能包（SKILL.md 目录）、主题↔specialist YAML——声明式资产目录 + 宿主内置执行是 xihe 已两次验证的模式。
2. **前端工程细节直接抄 BeautiCode**：poster-first + 双缓冲切换（旧背景常驻到新视频首帧 ready，消灭黑屏）；generation 单调递增比对（快速切换竞态防护）；`data-*` 属性状态机（CSS 按明确状态显隐，不散落临时 class）。
3. **配色 palette 走现有 token 机制**：`index.css` 已是「一个外观 = 一个 `html[data-theme]` 块」，皮肤 palette delta 就是再写一个块，与现有 light/dark 零冲突。
4. **交互**：页面隐藏 / `prefers-reduced-motion` 自动暂停；摸鱼模式 = 属性切换不销毁视频。

**明确不搬的**：DSH 万物皆插件运行时（xihe 扩展机制已定型 skills + specialists + MCP）；BeautiCode 的防御性工事（Apply Transaction/live verify/127.0.0.1 token 媒体服务器/snapshot 回滚）——它防"第三方改别人应用"，xihe 改自己窗口无此对抗面，素材经 serve 本地提供即无 CSP/文件权限坑；ShaderToy 联网玩法（内网不可用）——但**内置离线 WebGL2 shader 动画**恰好完美适配，可做默认皮肤，视频背景属用户自备素材的进阶功能。

## 对现有桌面端的影响评估（调研第二问的核心结论）

现状（2026-09-15 代码）：

- `desktop/src/renderer/src/index.css`：全套 RGB 三元组 token（`--c-app`/`--c-panel`/`--c-ink*`…），Tailwind 侧 `<alpha-value>` 映射；dark 默认 + light 两外观
- **面板几乎全不透明**：全 renderer 共 49 处 `bg-app`/`bg-panel` 基本不带 alpha；唯一 `backdrop-blur`+rgba 玻璃效果在 `StorePage.tsx`

推论：

- 背景层是独立 fixed 图层（`pointer-events: none`，z-index 在内容下），不改任何现有组件 DOM/CSS；**关掉皮肤 = 像素级回到今天**
- 但若什么都不改，背景会被不透明面板完全盖住——**透明度是连续旋钮，不是二值**：

| 档位 | 做法 | 影响 |
|---|---|---|
| A 定点露出 | 只在聊天页/空状态可见，工作台不动 | 零影响，价值有限 |
| B scrim 遮罩（推荐起步） | 背景上压 `rgb(var(--c-app) / 0.85~0.92)` | 观感近似，对比度损失可控 |
| C 全透+玻璃 | panel 也带 alpha + blur | 明显改变观感（StorePage 已示范此风格） |

对比度数学（dark 主题）：主文字 ink(235) vs app(10) ≈ 15:1（AA 要求 4.5:1）；scrim 0.85 时混合背景最亮 ≈ 47，对比度仍 ~11:1——**主文字余量很大；先翻车的是 `--c-ink-3`/`--c-ink-4`（本来就 4~5:1）和亮背景帧上的 accent/warning 色**，scrim 参数要保的是这批。light 主题对称同理。

**必须默认不透的区域**：MonacoPane / MonacoDiffPane / DiffBlock / TerminalPanel / Markdown 代码块——高密度等宽文本 + 语法高亮按不透明背景调色。即背景默认只在聊天列和留白区可见，正好符合产品语义（聊天页=待着的地方，工作台=干活的地方）。

性能：视频/WebGL 背景走 GPU 合成层 CPU 占用低；大面积 `backdrop-blur` 才有 GPU 成本（StorePage 已验证可接受）。

## 落地形态草案（若立项）

- `~/.xihe-agent/skins/<name>/skin.yaml`：manifest schema（background type/source/poster + palette + overlay 参数）
- serve 侧：皮肤目录的枚举/读取/素材本地提供（同进程树，无跨进程问题）
- 桌面侧：`<BackgroundStage>` 组件 + `useSkin` hook + scrim 分区规则（工作台默认不透）
- 暴露方式：设置面板 UI 即可；是否做成 agent 工具（"给我换个雨夜皮肤"一句话换肤，BeautiCode 的 `/bg` 路线）待定

## 待验证事项

- 立项本身：价值排序 vs 其他 roadmap（用户明确"后面再看要不要做"）
- 若做：manifest schema 定稿、serve 暴露接口、聊天列 vs 全局的 scrim 分区边界、是否暴露 agent 换肤工具

## 相关

- [[0025_desktop-control-plane]] — 桌面端整体架构
- [[0024_desktop-serve-protocol]] — 桌面端与 serve 的协议（皮肤素材若经 serve 提供走这里）
- 快照: `raw/sources/dsh-dynamic-skin.md`
