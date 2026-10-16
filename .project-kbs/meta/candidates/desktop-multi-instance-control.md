# 候选：桌面端接入多实例——config 路径选择 + 数据目录归位

> **状态**: open（待决定）
> **日期**: 2026-10-10
> **评估结论**: 方案完整但未落地。当前单用户默认路径场景无迫切需求，用户明确"后面再看是否需要支持"。核心缺口已确认：**桌面端完全不知道多实例**。
> **下一步**: 如需支持，先做"桌面端设置里加 config 路径 → spawn serve 带 --config"最小闭环，再评估数据目录归位。

## 摘要

xihe serve 支持多实例（`--config <path>` 选实例 → 由该文件 `agent_home` 决定数据根），但**桌面端（Electron 控制面）当前完全没接入这一层**。本次讨论梳理了桌面端如何"选一个 config 实例"启动 serve、以及桌面端自身数据（日志/设置/工作区）应随之归入对应 agent_home 的方案。

## 现状缺口（已确认的事实）

1. **桌面端拉起 serve 不带 `--config`**（`desktop/src/main/serve.ts` `spawnChild` 仅 `['serve','--host','--port']`，env 继承 process.env）。
2. **桌面端读写 config 硬编码默认路径**（`desktop/src/main/xiheConfig.ts:26` `join(homedir(),'.xihe-agent','config.yaml')`）。
3. **桌面端数据目录独立于 agent_home**（`dataDir.ts` → `~/.xihe-desktop`：workspaces.json / settings.json / run-history.json / serve.log），与 `~/.xihe-agent` 平级、各管各的。
4. **桌面端无法从 serve 接口拿 agent_home**：serve 由桌面端拉起、晚于桌面端启动，而桌面端在主进程早期（serve 起跑前）就要定 `desktopDataDir()` 读 UI 状态。→ "serve 报告 agent_home" 的路线不成立，桌面端必须**自己解析**。

## 服务端已具备的机制（复用，不在桌面端复刻）

`src/core/config.py` 已完整解析 `--config` 的实例文件 → `agent_home`（数据根）→ `XIHE_CONFIG_FILE` 导出 → `setup_logging` 落 `<agent_home>/agent-serve.log`。桌面端只需**指一条 config 路径**，其余全交 serve。
- 关键约束：默认 `~/.xihe-agent/config.yaml` **不含** `agent_home` 字段（config.py 的 chicken-and-egg：config.yaml 住在 agent_home 内，不能自定义自己的位置）；实例文件（`--config` 指向的 YAML）才可能设 `agent_home`。

## 方案（本次讨论产出，未落地）

**核心**：新增桌面端配置项 `xiheConfig`（config 路径），默认 `~/.xihe-agent/config.yaml`，用户可在设置里指向别的实例文件；spawn serve 时带 `--config`，serve 据此算对 agent_home。

1. **桌面端解析 agent_home 规则（先分场景）**：
   - 默认 `~/.xihe-agent/config.yaml` → 不解析，agent_home = `~/.xihe-agent`（已知常量）。
   - 自定义实例文件（含 `agent_home`）→ TS 读 YAML 拿 `agent_home`。TS 侧仅需**最小解析**（正则抓 `agent_home:` 或极简 YAML 读取；企查项目内可用库，倾向正则）。
2. **spawn serve 带 `--config`**（`serve.ts` args 加 `--config <path>`，始终带；默认传等价现状）。
3. **桌面端数据目录归位**：`dataDir.ts` 从 `~/.xihe-desktop` 改为 `<agent_home>/desktop/`：
   ```
   <agent_home>/
   ├── config.yaml / sessions.db / browser / cron / …   ← Python（serve）管
   ├── agent-serve.log                                   ← Python 管
   └── desktop/
       ├── serve.log  settings.json  workspaces.json     ← 桌面端管
   ```
   一个 agent_home = 一个实例；桌面端切 config 路径 = 完整切换（服务端 + 桌面端）数据。
4. **serve.log 职责边界**（日志合并讨论的延伸结论）：桌面端 `serve.log` 只记**前端自记状态**（spawn/restart/health/exit） + **启动失败 stderr 兜底**（python 起不来那一段）；不再镜像 serve 的 stdout（运行日志归 `agent-serve.log`，aiohttp 的 `aiohttp.server` traceback 走 logging、本就能进 `agent-serve.log`，已实测「`_WS_ARGS_LIMIT` traceback 在 agent.log 有、serve.log 有、拆分后 agent-serve.log 无」的归属路径）。

> 注：serve.log 日志收窄部分**已先行落地在 `serve.ts`**（本候选成文同日，改动含 bootReady 控制 + 前端自记 writeLine + 启动失败 stderr 兜底）；目录归位与 config 路径机制**未做**，待定。

## 待拍板点

1. `xiheConfig` 放桌面 `settings.json`，默认 `~/.xihe-agent/config.yaml` —— 是否认可。
2. agent_home 获取规则：默认直用 `~/.xihe-agent`、自定义实例文件才解析 —— 还是默认也走统一解析。
3. 端口：保持 `--port` CLI 参数、桌面端固定 7788；暂不做 config 覆盖 port（多实例并排时再议）。

## 评估：是否真实需求？

**倾向暂缓。** 当前是单用户桌面工具、默认路径场景，多实例（并排多个 serve / 切换完整实例）无迫切使用场景。服务端 `--config` 已支持多实例，缺的只是桌面控制面的接线。mkdir 到真被需要（要开第二个隔离实例、或一组实例协同）再做，成本集中在"TS 读 YAML 解析 agent_home"这一个技术点。

## 相关页面

- [[0023_multi-instance-config]] — 服务端 `--config` / agent_home 机制（本文的底座）
- [[0025_desktop-control-plane]] — 桌面端控制面（ServeSupervisor / spawn 生命周期）
- [[0024_desktop-serve-protocol]] — 桌面端 ↔ serve 协议
- [[multi-instance-a2a]] — 多实例服务端协同（A2A），与本文（桌面端选择实例）方向互补