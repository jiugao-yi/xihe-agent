# DSH 生态动态皮肤方案调研快照

> 抓取日期: 2026-09-15。摘录式快照（保留技术骨架，略去叙事性文字），来源均为公网开源项目。

## 来源 1: dsh-live-wallpaper（宿主内插件路线）

- URL: https://github.com/hinayoung23/dsh-live-wallpaper
- 形态: DeepSeek Harness Web UI（`dsh web`，本地 127.0.0.1:3080）的插件，`dsh plugin --profile web add ...` 安装，零 npm 依赖

核心机制：

- 四款**离线程序化动画**（极光/星云/落日流光/霓虹网格）——纯 WebGL2 shader，零网络零依赖，作为可靠性基线
- ShaderToy 源：无 App Key 时嵌官方 Embed iframe；配 Key 后经 DSH Host 服务（同源 JSON 请求，固定 API 非通用代理）取 `Common + Image` 源码本地 WebGL2 渲染；不兼容（Buffer A–D/纹理/音频/摄像头）自动退回官方播放器
- 壁纸层 `pointer-events: none`，不抢鼠标
- 主题中心：5 套预设 + 底色/强调色/按钮色独立自定义、5 种按钮形态、4 种字体风格
- localStorage 持久化配置；**关闭主题或卸载插件自动恢复 DSH 原生外观**（不 patch 宿主文件）
- 页面隐藏 / 系统"减少动态效果"时自动暂停动画
- 凭据（App Key）存 DSH 凭据服务，浏览器只能查配置状态不能读明文；仅固定官方 Embed 给 `allow-same-origin`，用户自定义远程网页在更严格 sandbox iframe 中

已知限制（对 xihe 的启示）：

- 无 App Key 模式需持续联网（内网环境不可用）
- 本地文件权限刷新后丢失（浏览器安全模型）；本地视频解码取决于系统编解码器，建议 H.264 MP4 / VP8-VP9 WebM
- 本地渲染仅支持一个 Image 通道 + 可选 Common 通道

## 来源 2: BeautiCode（外部 core + 宿主 adapter 路线）

- URL: https://github.com/starsstreaming/beautiCode
- 设计长文（中文）: https://deepseek.csdn.net/6a829cb710ee7a33f29bf92c.html（2026-08-17 发布）
- 形态: 同时支持 DeepSeek Harness 与 Codex Desktop 的动态背景工具，Node.js/TS 多包结构（core + adapter-dsh + adapter-codex），MIT

架构：

```
             ┌── adapter-dsh ── DeepSeek Harness（Cordis Plugin + 本地鉴权桥）
Core ────────┤
             └── adapter-codex ─ Codex Desktop（CDP 连 renderer，运行时注入 stage）
```

core 只做宿主无关的事：Background Store（active/staging/saved/snapshots 目录）、媒体校验、Apply Transaction、127.0.0.1-only 随机 token 媒体服务器、File Lock。adapter 只解决"怎么把最终状态送进 host"。不改官方宿主安装（无 fork、无 patch、无 app.asar 修改）。

前端渲染细节：

- **背景是宿主页面内容层后方的 stage**（`#beauticode-bg-stage`，`position: fixed; pointer-events: none`），不是置顶播放器窗口
- **poster-first 加载**：每个视频主题 = `poster + background.mp4` 一对。显示 poster → 建 video → 等第一帧真正解码完成 → 显示 video → poster 退出。切换不杀旧背景，新视频后台加载到 ready 才切换；失败恢复旧背景
- **generation 竞态防护**：每次 Apply 分配单调递增 generation，异步回调先比对 `callbackGeneration === currentGeneration`，旧世界线事件直接丢弃；manifest 记 `schema: beauticode.background/v1` + generation
- **data-* 属性状态机**：`data-bc-active/media/video-ready/generation/working/fish`，CSS 按明确状态决定显示隐藏，不散落临时 class
- **Fish mode**（Ctrl+Shift+Space）：只切 `data-bc-fish` 属性 + CSS `opacity/visibility`，绝不销毁重建视频，零加载成本即时切换
- Saved theme 记 `videoPositionSec`（恢复播放位置）——主题保存的是"小型工作环境状态"
- 视觉权重分档：非任务态背景保持原亮度；进入任务后背景自动降权，前景 UI 重新成为主体（作者结论：背景太亮是灾难，"需要工作时退到背景"）

Apply Transaction（安全回滚）：

```
idle → snapshot → stage → commit disk → publish media → apply host → live verify → pass: finalize / fail: rollback
```

核心原则 "Disk success ≠ User-visible success"：写盘成功但页面没加载/解码失败/连接断了都算失败，live verify 检查舞台存在/媒体 ready/generation 一致/无 overflow/无 videoFailed。

媒体服务与校验：

- 本地媒体不直接 `file://` 引用，复制进数据目录后经 `127.0.0.1:random_port` 媒体服务器提供（只绑 127.0.0.1/localhost/::1，禁止 0.0.0.0/LAN）
- 校验：扩展名 + regular file + 大小 + magic bytes + MP4 `ftyp` 结构 + symlink/reparse point 检查（防 evil.exe 改名）
- 控制接口与媒体接口都加随机 token，检查 token/origin/文件身份/路径范围，只提供当前允许的一份媒体，不是任意文件服务器

Agent 接入：

- 插件向 DSH 注册斜杠命令（`/bg <path>`、`/bg-theme <name>`、`/bg-clear`）并暴露成 Tool——"把 D:\Videos\city.mp4 设置成背景"一句话完成，链路 Tool Call → 验证路径 → 导入媒体 → Apply Transaction → Host Apply → Live Verify

## 来源 3: 其他生态参考

- Wallpaper Engine 桥接插件（DSH 插件商店）: 把本机 Wallpaper Engine 壁纸变成 DSH 网页界面背景——Video 动态播放、Web 以 iframe 加载、Scene 提取主纹理作静态帧
- dsh-deep-whale 等皮肤包：安装方式 = 把项目地址和"安装一下这个皮肤包"一起发给 DSH（agent 自己装插件，依赖 DSH 万物皆插件架构）
- DeepSeek Harness 主仓库: https://github.com/deepseek-ai/deepseek-harness（Cordis 元框架，everything-is-a-plugin，Web UI + Host 服务 + 插件系统）
