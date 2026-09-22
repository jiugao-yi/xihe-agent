---
type: concept
title: 能力商店（catalog + 安装器 + 挂载台账）
slug: 0049_capability-store
aliases:
  - capability store
  - 能力商店
  - store 挂载
  - 商店台账
tags:
  - architecture
  - store
  - skills
  - mcp
  - desktop
status: active
created: 2026-09-15
updated: 2026-09-15
related_pages:
  - wiki/concepts/0007_skills-system.md
  - wiki/concepts/0005_mcp-dynamic-registration.md
  - wiki/concepts/0034_three-layer-agent-roster.md
  - wiki/concepts/0032_specialist-agents.md
  - wiki/concepts/0024_desktop-serve-protocol.md
---

# 能力商店（catalog + 安装器 + 挂载台账）

## 摘要

能力商店 = `core/services/store.py` 的**薄目录 + 安装器 + 挂载台账**三层，给桌面「商店」页（StorePage）供给。设计立场（模块 docstring）：它是**现有挂载点之上的薄层**——skill 装进共享的用户 skills 目录（磁盘扫描自然发现）、MCP 声明只活在自己的台账里经 `mcp_tool._load_mcp_config` 合并（config.yaml 手写同名优先）；「给谁用」用**台账挂载**表达，**绝不程序改写 config.yaml / agents/*.yaml**。安装是桌面专属操作（agent 侧无 store 工具），卸载/挂载全走台账。

## 目录与来源

- 配置：`store.sources` 列表（每项需非空 `url`，可带 `name`）；`url` 支持 http(s) 与本地路径（内网静态服务 / 共享盘 / `E:/xihe-store/index.json` 本地目录源）。不配则商店为空（UI 空态指向 config）。
- index.json：`{"items":[...]}`，条目必填 `id` + `type ∈ {skill, mcp}`（坏条目跳过告警）；可带 `title/version/description`、`source: {zip|path}`（相对引用按 index 位置解析）、mcp 条目的 `mcp` dict（无 `url` 的 stdio 条目标 `unsupported`——**只支持远程 streamable-http MCP**）与 `config_schema`（凭据字段声明）。
- 缓存与容错：进程内目录缓存 TTL 300s（`/store/refresh` 强制重取）；多源逐个 try/except，一个源挂不影响其余（响应 `sources[]` 带每源 ok/error）。下载 20s 超时、单文件 10MiB、包总量 50MiB。

## 安装语义与磁盘布局

台账：`AGENT_HOME/store/installed.json`，结构 `{"skill":{id:记录}, "mcp":{id:记录}, "mounts":{agent_key:[token]}}`；坏文件告警后重建空台账；写走 tmp + `os.replace` 原子；`_ledger_lock` 守读改写（读路径故意无锁——catalog_view 会调它，非重入锁会死锁）。

**skill 安装**（→ `AGENT_HOME/skills/<id>/`）：
- id 正则 `^[a-z0-9][a-z0-9._-]*$` ≤64；**撞名坐标系是 SKILL.md frontmatter `name`**（发现层按它去重）——目录级检查躲得过的嵌套手工安装（`skills/<group>/<name>/`）也会被抓；撞 bundled 名直接拒（bundled 赢扫描去重，影子安装会不可见）。
- 物化：本地 `path` copytree 或 `zip` 下载/读盘；包根必须有 SKILL.md 或恰好一层顶层目录包含它。
- **zip 防线**（zip-slip + 资源限）：拒绝绝对路径/盘符/反斜杠/`..` 条目、≤500 文件、单文件 ≤10MiB、总量 ≤50MiB、解压后逐条再验落点在目标内——**从不满 extractall**。
- 布局白名单：只允许 `SKILL.md / references / templates / scripts / assets`。
- 升级：旧目录改名 `<id>.store-old` → 新目录就位 → 删备份；失败回滚。装完清 skills prompt 缓存，`skills_list` **立即可见**。

**MCP 安装**（→ 只写台账，不落任何其它文件）：
- 仅远程 streamable-http；config.yaml 已有同名 `mcp_servers.<id>` 直接拒（手写优先不覆盖）。
- `config_schema` 凭据从请求 `config` 收集，必填缺失拒；重装/升级未提供的键**继承已存 secrets**；渲染成最终 MCP config（`{key}` 占位替换，可进 URL query）。
- 装后即调 `discover_mcp_tools()` 走后台连接，返回实时连接状态。

**卸载**：只卸台账拥有的；skill 拒删用户 skills 目录之外的路径（防台账/目录错位）；MCP best-effort `remove_mcp_server`；台账清理 + **从所有 agent 的挂载列表抹掉该 token**（空列表连键一起删）。

## 挂载机制（给谁用）

- token：MCP 条目 = `mcp-<item_id>`（即 roster 的按需服务器作用域）；skill = frontmatter `name`（`skills_allowed` 过滤所用的同一个键）。
- `set_mount(kind, id, targets)` 是**替换语义**：先从所有 agent 抹掉该 token，再加到 targets 列出的；需先安装。
- `merge_mounts(agent_key, toolsets, skills)` 把挂载并进解析后的名单；**`None`（全量）保持 `None`** 不动——`[]` vs `None` 不变式（[[0034]]）不被破坏。
- 生效时机（API 显式回报 `effect`）：
  - **main = 重启生效**：`SharedContext.__init__` 构造时算一次 `merge_mounts("main", ...)`——挂载到主 agent 后要**重启 serve**。
  - **specialist = 热生效**：`_build_agent_instance` 每次派发重读台账。
  - MCP 另有一条免挂载路径：主 agent 名单含 `mcp`（全服务器）时装完即可用；挂载的意义主要是**给 specialist 圈 `mcp-<id>`**。

## serve API 与桌面 UI

- `GET /store`（目录视图：installed/installed_version/upgradable/mounted/secrets_set——**secrets 永不出台账**，只回哪些键已填）、`POST /store/refresh`、`/store/install {type,id,config?}`、`/store/uninstall`、`/store/mount {type,id,targets[]}`；store 工作全在 executor 里跑（不占 WS 事件循环）。
- StorePage：源状态 chip（红绿点 + 错误 tooltip）+ 刷新；搜索 + 已装/类型过滤；卡片徽章体系——**手动配置**（config.yaml 手写 MCP / 手工放置 skill，只读展示、可看实时连接态）/ **源已下架**（orphan，仍可挂载卸载）/ **手动放置**（hand_installed：磁盘上已有与目录条目同名之物）/ **可升级** / **已安装**；安装对话框 MCP 凭据 password 输入（已存键显示「（已保存，留空保持）」，只写不读）+ 挂载勾选（装完立即 mount），结果提示区分「主 agent 需重启 serve 生效，specialist 下次调用即生效」。

## 边界与已知取舍

- **信任模型是内网**：目录源是用户自配 URL，商店**无签名/发布者校验**；安全面 = 解压/布局防线 + id/name 正则。skill 的 `scripts/` 是普通磁盘文件，无沙箱。
- **secrets 台账明文**（`installed.json`），但 API 只回 `secrets_set` 布尔、桌面只写不读——与 api_key 的处理口径一致（[[0032]]）。
- `upgradable` 是**字符串不等**（已装 version ≠ 目录 version），非 semver 比较；UI 同时显示两个版本号。
- 手工与商店的去重：手写 MCP 的 URL base（去 query 小写）与未装目录条目相同 → 该条目标 hand_installed 不重复成行；同理手工 skill 的 frontmatter name 命中目录条目。
- MCP 合并失败显式记日志——吞掉 = 商店装的 MCP 从名单静默消失（mcp_tool 有注释钉死这一点）。

## 陷阱

- 挂载到 main 后忘重启 serve → 「挂了没生效」的第一嫌疑。
- skill 撞名检查按 frontmatter name 而非目录名——排查「装不上/装了不可见」先比对 name。
- `_parse_frontmatter` 在 store.py 里**故意复制一份**而非 import skills 工具——core 不得模块级 import tools（注册副作用）；反向 store→mcp_tool 的 import 全在函数内（循环安全）。

## 相关页面

- [[0007_skills-system]] — skills 目录扫描与发现去重（安装落点）
- [[0005_mcp-dynamic-registration]] — MCP 发现与 `_load_mcp_config` 合并（手写优先）
- [[0034_three-layer-agent-roster]] — merge_mounts 并入的名单语义（None 不变式）
- [[0032_specialist-agents]] — specialist 挂载热生效的派发路径
- [[0024_desktop-serve-protocol]] — /store* REST 面
