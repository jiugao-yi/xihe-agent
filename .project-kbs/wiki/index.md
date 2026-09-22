# 项目 Wiki 索引

> 按内容分类的页面与来源目录。新增 / 更新页面时在此登记。

本知识库遵循 [PROTOCOL.md](../PROTOCOL.md) 的工作流与模板。

## 状态

- 初始化日期: 2026-07-01
- 健康度: [meta/lint-status.json](../meta/lint-status.json)
- 页面数: 55 · 原始快照: 10

## Concepts (概念 / 规范)

- [0002 工具注册表与调用链](concepts/0002_tool-registry-and-dispatch.md) — ToolRegistry 自注册 + dispatch + toolset + AuxiliaryClient + 子 agent + 三层上下文防御
- [0003 浏览器工具集](concepts/0003_browser-tools.md) — Playwright、a11y tree + ref ID、**CDP 托管默认** + persistent 兜底、browser_logout
- [0004 上下文压缩机制](concepts/0004_context-compression.md) — 4 步算法、tail 保护、增量摘要
- [0005 MCP 动态工具注册](concepts/0005_mcp-dynamic-registration.md) — 不用中间代理、常驻连接、后台 event loop
- [0006 会话两层 ID 设计](concepts/0006_session-design.md) — session_key/session_id、source-only API、重置策略
- [0007 Skills 系统](concepts/0007_skills-system.md) — progressive disclosure 三层、SKILL.md 格式
- [0009 定时任务（cron）设计](concepts/0009_cron-jobs.md) — 60s tick 无状态基线 + job 三形态（prompt/纯脚本/脚本喂prompt）+ wake gate + context_from
- [0011 Gateway 架构](concepts/0011_gateway-architecture.md) — SharedContext + 每消息新建薄 agent + SQLite 续对话；并发模型已修复（to_thread join + per-session 锁 + 带外停止 + steer，2026-09-14 订正旧「阻塞 join」叙述）
- [0013 Toolset Scope 与按需展开](concepts/0013_toolset-scope-and-dynamic-expansion.md) — 名单裁剪 + request_tools 元工具按需展开 + 设计决策（为什么不用向量检索/LLM 分类）。⚠️ 2026-09-14 再订正：现行 **18 平铺组**（base/computer/external_agents/meta 后增）；`DEFAULT_TOOLSETS` 已不存在（主名单 = config 顶层键，不写 = base 地板）；`session.toolset_scope` 从未实现
- [0014 项目上下文加载规则](concepts/0014_project-context-loading.md) — .xihe.md/AGENTS.md 始终加载 + CLAUDE.md/.cursorrules 可配置 + 全部合并(不是 first-match-wins)
- [0016 中断/停止/Steer 控制通道设计](concepts/0016_interrupt-stop-steer.md) — 停止走带外控制通道(绕过队列) + per-agent contextvar 中断 + interruptible_iter(循环)/子进程注册表(子进程) + steer(自然结束转新轮/停止丢弃) + 7 个演进坑
- [0017 角色化子 agent（Roles）](concepts/0017_role-based-subagents.md) — ⚠️ **已废弃**（角色化回退）预定义角色 + delegate_task(role=...) + skill索引精简 + MCP分组
- [0018 工具/技能/角色分层架构](concepts/0018_tool-skill-workflow-role-layering.md) — ⚠️ **角色层已废弃**（回退），tool/skill 两层 + workflow是skill编排用法 + 关系图
- [0022 测试策略——分层模型](concepts/0022_testing-strategy.md) — L0-L4 分层 + 注入假模型 client(`FakeChatClient`)测循环不变量 + 测试隔离约定 + 行业对照；`tests/` 44 文件 / 542 passed（2026-09-14）
- [0023 多实例配置——`--config` 启动时选实例](concepts/0023_multi-instance-config.md) — 一个 YAML 描述一个实例(`agent_home`→数据根隔离) + peek argv 设计(0 下游重构)。⚠️ 优先级分层/`.env` 浮顶陷阱部分已过时（2026-08-13 起配置单源：无 `.env`/env 覆盖/`${VAR}` 展开），以页首订正注记与 [[0001_xihe-agent]] 为准
- [0024 桌面端通信协议与 xihe serve 模式](concepts/0024_desktop-serve-protocol.md) — 第三运行模式 `xihe serve`(aiohttp HTTP+WS,复刻 gateway 内核但 `run_in_executor` 解放循环) + REST/WS 契约（2026-09-14 订正：补 attach/steer/approve/cron_result 帧 + truncate/title/toolargs 端点；掉线改 60s 宽限窗非必中断）+ Emitter 跨线程桥(stdlib queue + delta 合并) + 会话映射 platform=serve + 能力描述符
- [0025 桌面端控制面设计](concepts/0025_desktop-control-plane.md) — 独立仓 xihe desktop(Electron,不内嵌引擎,经协议驱动 serve) + 三层模型 Provider/Agent/Session + 两 provider 形态(process/connector) + 能力驱动 UI + LIVE_SLOT 升级 + Roadmap P0-P4。⚠️ **Agent 层（persona / 3 种子 agent / serve 显式暴露）已被 [[0026]] 订正**：改为内置 xihe + 可添加 claude；P1 persona 废弃。⚠️ **2026-09-14 终态（[[0047]]）**：多 agent 骨架彻底清除（store 扁平化 + `/agents` 删 + claude connector 路线放弃，外部引擎走 `external_agent` 工具）
- [0028 桌面端 claude 接入架构](concepts/0028_desktop-claude-transport-architecture.md) — ⚠️ **已过时（2026-09-14，ClaudeRunner 已删除）**：claude = 第二种 agent 引擎，**一会话一长驻子进程 + stdin 跨轮喂 NDJSON**；stdout NDJSON → 同形 ServeEvent → 复用 renderer 归约；冷/热双路径（进程死后 `--resume` 续同会话）实测通过；interrupt/dispose 语义分离；凭据 spawn 时注入（env+`--model`，api_key 永不外泄）。claude 现经 [[0040]] 的 `external_agent` 工具访问，本页留作历史架构参考
- [0029 桌面端双引擎架构总览](concepts/0029_desktop-dual-engine-architecture.md) — ⚠️ **已过时（2026-09-14，双引擎已不存在）**：xihe serve（WS）+ claude（STDIO）两引擎并列，统一 `ServeEvent` → renderer 引擎无关归约；双引擎总览图 + 两路传输对照表 + main 总编排（ServeSupervisor adopt-or-spawn + ClaudeRunner 长驻 + IPC 桥）。桌面现单引擎（serve WS），`by` 归因与 ServeEvent 归约仍有效；见 [[0047]]
- [0031 工作空间 cwd 绑定与入口差异](concepts/0031_workspace-cwd-binding.md) — cwd 绑定在会话上（桌面私有 convWorkspace map）、**随每轮 sendTurn 传 serve、不落库**；入口无关（外层列表=空间内）；CLI 完全独立（会话不可见+进程 cwd）；失效边界 3 条；「历史消息 ≠ 运行时 cwd」
- [0032 专家 Agent](concepts/0032_specialist-agents.md) — 配置声明的常驻专家：`agents/<slug>.yaml` 一文件一专家 → 派生 `run_<slug>_agent` 工具 + 花名册路由；完整分层 prompt（vs delegate wholesale 覆盖）；连接键留空继承；skills 白名单 **None=全量索引 / 空集=不注入**（falsy 翻转陷阱）；`mcp-<server>` 按需授权（get_schemas 双路匹配）；api_key 永不回显（api_key_set 布尔 + PUT 三态）；与已废弃角色化 [[0017]] 的差异对照。⚠️ toolsets 缺省语义 + `specialists.enabled` 总闸已被 [[0034]] 订正
- [0034 三层 Agent 名单模型](concepts/0034_three-layer-agent-roster.md) — **主/专家/delegate 名单收口**：主 agent = config.yaml 顶层 `toolsets`/`skills`（与专家共用 `resolve_roster`，无 main 专属逻辑）；三态（不写/`[]`=**base 读面地板**（2026-09-14 订正，非「不加载」）、`["*"]`=None 全量、名单=白名单且 `mcp-<server>` 永远保留；agent 表面 = (base ∪ roster) − blocked）；**`[]` vs `None` 不变式**（truthiness 反转陷阱）；`specialists.enabled` 总闸默认关（只管用户专家，bundled 不受控）；delegate 运行时三态独立于父 + `subagent_blocked`（external_agent 不在内）；load_config 白名单两循环陷阱；serve `_capabilities` 按主名单收敛
- [0036 系统提示词装载与三层 Agent prompt 差异](concepts/0036_system-prompt-assembly.md) — **prompt 侧对偶 [[0034]]**：声明式 `LAYERS` 表（PromptCtx + `_tool_guard`/`_passthrough` 工厂，表序=节序，`expand_agent_vars` 收尾）+ 四层组 18 层注入条件表 + **三层三条装载路径**（主=完整层组含 platform/kbs preamble/roster/记忆快照；专家=同一条装配代码换 persona 入参、无 platform/preamble/roster 层但指导层按自身工具面照常裁剪；delegate=`system_prompt_override` 三处短路→纯任务卡无任何指导层）+ 不变式（条件层 key off ctx.tools 且与 chat loop 同过滤器、CODING_TOOLS 按写/执行面判定、记忆快照不进 prompt 文本、.md 即时生效 vs .py 需重启）
- [0037 危险操作审批与权限系统](concepts/0037_approval-permission-system.md) — **三值决策管线（借鉴 Claude Code）**：`evaluate() → allow/ask/deny`（优先级 mode auto > deny 规则 > allow 规则 > 审批记忆 > ask 规则 > 危险判定 > LLM 语义判定）；四层架构（判定 `core/support/approvals.py` 纯函数 / 协调 `_approval_shared` 阻塞等待 / 拦截 dispatch 单门 / 通道三模式+cron 后台审批卡）；规则语法 `"tool(glob)"`（terminal/ssh_exec 匹配命令原文，write_file/patch 取路径，**无 action 工具只能整名覆盖**）；**ask 规则**（config 圈定任意工具需确认，置于 allow 后可 carve-out）；**审批记忆落盘 30 天 TTL** + 三维度桶（会话 / `cron_job:任务名` / `ws:目录`，`chat(approval_key=)` 换桶）；五结局状态机保守失败；**启发式非安全边界**（变量间接可绕过）；**审批决议记录持久化**（2026-09-20：`approval_record` 注入 result JSON → 持久化时剥离进 message_meta.approval，trace 徽章刷新可见）
- [0040 外部 agent 适配器协议（claude + codex 双引擎驱动）](concepts/0040_external-agent-adapter-protocol.md) — xihe 内核侧把本地 claude/codex CLI 作可委派外部引擎（**`external_agent` 工具**，2026-09-14 订正工具名）：协议同构（NDJSON + 会话 id 冷 resume + cwd + env 凭据 + 杀进程中断）→ 一个适配层两策略（**ClaudeDriver WARM 长驻 stdin 跨轮 / CodexDriver ONE-SHOT 每轮新进程 + `exec resume`**）；`(engine, session_key)` 双键 resume 表；**stdin 语义两引擎方向相反**（claude 首行 boot / codex EOF 才跑）；45s 就绪门/10min 空闲回收为 driver 内建（桌面侧 runner 已删）；协议对照表 + codex 事件映射 + 平台硬坑（`--disable multi_agent` 必带；Windows sandbox 用 unelevated）；已决 IPC 不走 MCP。双引擎均实测通过；claude/codex 的**唯一**现行接入通道（桌面并列引擎已删）
- [0046 共享会话终端](concepts/0046_shared-session-terminal.md) — agent 终端面板（2026-09-15 [[0050]] 订正：三源→双源，本地 shell 移独立面板、快速连接删）的双源通道架构：**一个通道一个环一个真相源**，agent 工具与桌面用户都是生产者/观察者——agent 本地命令（serve 的 conv/proc 通道注册表，Ring 单调 offset 寻址，活进程永不逐出 + atexit 树杀；一行头 `—— $ cmd` cwd 变化才显 + LineTap 行纪律 + StreamDecoder GBK 回退）/ SSH 会话（共享注册表 + tap，桌面是第二键盘，连接只由 agent 发起）；帧自带结束 offset 客户端 cursor 只被服务端推进、同 key 复活删旧 cursor、**viewer 永不创建频道**、tab 可见性 = 当前对话 ∨ 活跃运行、conv tab 存在才播种；**自动打开状态链**（ssh_connect/process start 立即、terminal 5s 延时且审批挂起、静音粘性、selOwnerRef 不抢 tab）；含"agent 命令没显示在面板"排查顺序
- [0048 会话数据模型](concepts/0048_sessions-messages-reset.md) — sessions/messages/message_meta 表结构与三个 ID 分工（session_key 确定性键 / session_id 轮次 / chat_id 平台侧）、消息逐迭代持久化（rewrite_messages 增量前缀，meta 同事务写一次）、**content 纯净通道 + uuid 主键 + ord 会话序锚 + FTS 内容表模式**（2026-09-20 重构，见 [[0054_messages-uuid-meta-refactor]]）、附件提示块 API 边界注入、中断部分文本带标记落库、ResetPolicy 重置（换 session_id 不删旧消息；默认 none）、_reshape_history 折叠、桌面端完整数据流。收录时误编号 0030 撞号，2026-09-14 重编号并补登记
- [0049 能力商店](concepts/0049_capability-store.md) — `core/services/store.py` 的**薄目录 + 安装器 + 挂载台账**三层：skill 装 `skills/<id>/`（zip-slip/布局白名单防线，装完立即可见）、MCP 只写 `store/installed.json` 台账经 `_load_mcp_config` 合并（config 手写同名优先；仅远程 streamable-http；secrets 只写不读）；「给谁用」= 台账挂载（token：mcp-<id> / skill frontmatter name，替换语义），**main 重启生效 / specialist 热生效**；绝不程序改写 config.yaml/agents yaml；/store* REST + 桌面 StorePage（手动配置/源已下架/手动放置/可升级徽章体系）；信任模型=内网无签名校验
- [0052 三模式调用架构](concepts/0052_three-mode-architecture.md) — 入口/Runner/核心三层：**SharedContext**（应用装配——重状态持有 + `create_agent` 唯一构造实现 + bootstrap_process 组合根）/ **XiheAgent**（一轮执行机器——chat 门面 + _chat_turn 轮体 + _dispatch_tools 调度；messages=账本落库，API 形态=快照含 turn 层）/ **AgentTurnRunner**（轮装配调度——Params+Callbacks 进、TurnResult+PlanOutcome 出；构造即就绪传 agent 复用未传自建）+ 入口只剩协议/串行化/投递；plan mode（roster 物理只读+clarify 结构化批准）、regenerate 遗忘语义、CLI 长生命周期 vs 其他 per-message 的理由

## Entities (实体)

- [0001 xihe-agent 项目总览](entities/0001_xihe-agent.md) — 单进程 OpenAI 兼容工具调用 agent，CLI / 网关 / serve 三模式（桌面端 desktop/ 子目录经 serve 协议接入）

## Stories (需求 / 故事)

_暂无_

## Changes (代码变更)

- [0010 浏览器 CDP 默认 + cron job 多形态 + 提示词约定](changes/0010_cdp-default-and-cron-job-forms.md) — 三组已验证落地改动（2026-07-03）
- [0012 新增 http/maven/node 工具 + web 门控 + gateway 增强](changes/0012_new-tools-and-gateway-enhancements.md) — 新工具 + web check_fn 门控 + gateway/file/mcp/cron 增强（2026-07-09）
- [0019 KBS 子系统——可插拔业务知识库协议](changes/0019_kbs-feature.md) — **功能点**:总开关 kbs.enabled 门控前导+工具(check_fn)、关掉零足迹;kbs_init/status/search + 复用 core 文件工具(2026-07-30)
- [0020 会话管理命令](changes/0020_session-management-commands.md) — **功能点**:`/sessions`(隐藏内部+按用户过滤)/`/history [N]`/`/resume`(会话内切换)/`xihe chat -r` + `list_sessions` 后端(2026-07-30)
- [0021 CLI hybrid TUI](changes/0021_cli-hybrid-tui.md) — **功能点**:hybrid REPL(空闲 prompt-toolkit + 处理中 msvcrt steer)、cwd 注入、fresh-by-default、来晚 steer 自动续跑、CODING_GUIDANCE 7 点、日志 file_level、新依赖 prompt_toolkit+textual(2026-07-31)
- [0027 xihe-desktop ClaudeRunner 长驻 stream-json 重写](changes/0027_desktop-claude-longlived-rewrite.md) — **重构**:每轮新进程 → 一会话一长驻进程(stdin 跨轮喂 NDJSON)，省 ~5s 启动 + 复用 prompt-cache；热路径(stdin 多轮) + 冷路径(进程死后 `--resume` 续同会话)双路径实测通过；新增 dispose 补 deleteConversation 缺口(2026-08-12)
- [0033 专家 Agent 落地 + 工具集目录重构](changes/0033_specialist-toolset-overhaul.md) — **功能点**:agents section → 每专家一文件 + serve CRUD + 桌面编辑器(chips/待重启徽标)；skills「不选=不配置」空集语义修复；工具集 14 平铺组(删组合预设/includes/browser_scripts、core 四拆、agent 拆 skills)；itsm.yaml 迁移；e2e 28 项 + pytest 12 + 桌面 build 全绿(2026-08-16)
- [0035 三层名单统一 + specialists.enabled 闸门](changes/0035_three-layer-roster-unification.md) — **重构**:主 agent 从 config.yaml 顶层键实例化(删全部 main 专属 resolver)+ agent.py `is not None` 修复(`[]`曾被翻成全量) + config 白名单两循环扩展 + `_capabilities` 按主名单收敛 + delegate schema blocked 清单修正(5→12) + 桌面专家委派 Toggle/横幅；破坏性:不写 toolsets=无工具、不写 specialists.enabled=run_*_agent 消失(2026-08-17)
- [0038 危险操作审批落地](changes/0038_dangerous-operation-approvals.md) — **功能点**:三值权限管线(evaluate allow/ask/deny + deny/allow 规则 + 会话记忆) + dispatch 单汇聚点门 + XiheAgent 阻塞等待(五结局保守失败) + 子代理共享 `_approval_shared` + CLI/gateway/serve 三模式审批交互(桌面审批卡含「批准，不再询问」第三按钮) + `_DANGEROUS_PATTERNS` 30 条正则从 terminal.py 移入 `_approvals.py` + 高危参数表 7 类；terminal 死分支删除(2026-08-20)
- [0039 ask 规则、审批记忆落盘与维度、cron 审批卡闭环](changes/0039_ask-rules-approval-dimensions.md) — **功能点**:`approvals.ask` 通用圈定(任意工具加行即拦,allow 可 carve-out) + 记忆落盘 `agent_home/approvals/`(按桶分文件/30 天 TTL/原子写/滑动续期,`memory_days` 非法回落 30) + 记忆三维度(`approval_key` 换桶:cron 按任务名/serve 工作空间按目录) + cron 审批卡闭环(卡片投 deliver 聊天→后台路由表折 y/n/a→"a" 落任务名桶以后静默放行;发卡失败即拒;删/暂停任务 interrupt 解除等待;无通道维持无人值守即拒);飞书 steer 缺口与桌面 serve cron 卡二期为已知边界(2026-08-25)
- [0042 包结构重构](changes/0042_package-restructuring.md) — **重构**:按三原则(高内聚低耦合/单向 DAG/按变化原因分组)四步——serve 按业务分模块(app/chat/admin/browser/terminal/knowledge 各持 add_routes) + 启动器独立 `src/app/main.py` + platforms→`gateway/platforms/` + cli 回归纯 CLI + registry 契约下沉 `core/registry.py`(tools/__init__ 只留 re-export+load_all_tools,tool 模块 import 零改动) + core 子包化 `agent/`(引擎)/`services/`(特性域)/`support/`(零依赖 DAG 底) + 基础设施对流(_approvals/_paths/tool_result_storage→core/support,tools 40→35);git mv 保历史/包 __init__ re-export;pytest 466 绿 + serve 冒烟 200;gateway/serve 需重启(2026-09-01)
- [0043 桌面端开发工作台 Phase 1](changes/0043_desktop-workbench-phase1.md) — **功能点**:Monaco 引入(气隙无 CDN loader,ESM+?worker+React.lazy 拆 chunk) + chat/workbench 双模式布局(设置持久化) + 编辑器 tab/模型复用/外部变更协调(fsVersion+dirty 横幅不覆盖草稿) + diff 可视化(serve write_file 结果内嵌 unified diff + DiffBlock 高亮 + 审批卡 args 透传 Monaco 对比) + 文件树 git 装饰(porcelain -z 事件驱动) + IDEA 紧凑树(单链目录合并,50 层防 symlink 环) + 运行面板(每 workdir 一槽+历史) + 共享终端三源(本地/agent 本地命令/SSH 共写一通道);serve 只加字段不改协议(2026-09-02)
- [0044 桌面端头部交互重构](changes/0044_desktop-header-workspace-ux.md) — **改进**:按钮与绑定关联规则(操作对象是工作空间内容的跟绑定走:布局隐藏/文件树运行置灰,全局能力不关联) + 工作空间 chip 删除(绑定统一走侧栏,store bindWorkspaceToConv 删) + `inWorkbench` 生效布局推导(未绑定回落 chat 防退不出工作台死角) + 树开关双状态合并(showTree/rightTreeCollapsed 重叠错位) + 品牌接入(banner→`docs/_make_icons.py` 再生成 logo/icon 管线 + 展示点);踩坑:Fast Refresh 停旧模块致 UI 假回归(2026-09-02)
- [0045 编辑器 git 行标记](changes/0045_desktop-git-gutter-marks.md) — **功能点**:IDEA 式 gutter 色条(绿增/蓝改/灰删) + minimap 投影,编辑 400ms 防抖实时刷新;`git:headFile` IPC 三分语义(untracked=空基线全绿/非 repo=无标记/1MiB 上限) + lineDiff 前后缀裁剪+LCS(超 4M cell 降级整块 modified,'' 按 0 行) + MonacoPane headText 装饰(overviewRuler canvas 必须硬编码 hex);边界:commit 后已开 tab 不自动消(2026-09-02)
- [0047 桌面端单 agent 收敛——多 agent 骨架清除](changes/0047_desktop-single-agent-collapse.md) — **重构**:store 扁平化(`agents[]`/`selectedAgentId`/`Agent` 描述符/`serveBacked` 全删 → `conversations`+`activeConvId`+`serveModel`,action 全部去 `agentId` 首参) + serve 删 `GET /agents`(model/capabilities 从 `/health` 取) + 删死文件 common.tsx + cron_result/complete 列表刷新有意放宽 + README/package.json 文案;[[0026]] 终态落地,0025/0028/0029 同步订正;pytest 542 绿 + tsc 0 错 + build 绿(2026-09-14)
- [0050 终端体系整备](changes/0050_terminal-panel-overhaul.md) — **改进+重构**:agent 终端自动打开收敛(仅 ssh_connect/process start 立即弹,terminal 5s 延时+审批挂起,静音改会话粘性,selOwnerRef 不抢 tab) + conv 通道打印治乱(一行头 cwd 变化才显 + LineTap `\n`/`\r` 行纪律 + StreamDecoder GBK 回退——`locale.getencoding()` 陷阱) + 快速连接/本地 tab 整链删除(连接统一 agent;origin 全拔) + tab 可见性=当前对话∨活跃运行 + viewer 永不建频道(修"切对话孵化空 Agent tab") + **Run 面板重做为 ShellPanel 通用本地终端**(多 tab 交互式、不绑 workspace、窗口级 dock、`main/terminals.ts` 多会话+游标回放,删 run.ts 历史);pytest 558 绿 + tsc 0 错 + build 绿(2026-09-15)
- [0051 可视化体系](changes/0051_visualization-image-render.md) — **功能**:visualization skill(借鉴 doubao-visualization 的白名单/真实性/输出结构,通道按 xihe 改写:mermaid 原生 / html renderer 沙箱块 / 三模式路由硬规则) + 桌面 `html type="renderer"` 沙箱 iframe 就地渲染(allow-scripts+高度上报 postMessage) + **image_render 统一工具**(data/mermaid/html/markdown 四源;matplotlib+独立无头 Playwright;vendor mermaid.min.js;落点 AGENT_HOME/charts;render_chart+render_image 按"少而精"合并) + **xfile:// 本地图片渲染链**(特权协议仅图片;占位 host+整段编码防盘符被当 host;附件条正文单源——live 与刷新一致);坑:markdown 反斜杠转义吃路径禁模型嵌图、mermaid 无横向条形;pytest 568 绿 + tsc 0 错 + build 绿(2026-09-16)
- [0053 架构重构批次](changes/0053_architecture-refactor.md) — **重构**:plan mode(roster 物理只读+clarify 结构化批准零文本解析+批准后自动执行轮,三模式入口) + turn_layers 轮级提示层(API 边界注入不落库,修 messages[0] 持久化污染) + **AgentTurnRunner 三模式编排收敛**(Params+Callbacks 进 TurnResult+PlanOutcome 出,构造即就绪传 agent 复用未传自建;CLI 保持长生命周期) + chat() 巨型函数拆分(门面/_chat_turn/_dispatch_tools) + TurnCallbacks dataclass(8 回调收敛) + office 工具集(excel/word 四动作+send_file) + handle_command 去 agent 化 + serve 启动冒烟测试(补组装盲区);六提交独立可回滚;pytest 584 绿 + tsc 0 错 + build 绿(2026-09-17)
- [0054 会话数据模型重构](changes/0054_messages-uuid-meta-refactor.md) — **重构**:四批次——附件气泡不变形(attachments 列元数据+API 边界注入提示块,content 恒为用户原文;顺带修注入索引错位与 describe_image 卡 loop) + 审批记录持久化(approval_record 注入 result JSON→trace 徽章) + 垃圾清理(system 行不再持久化+孤儿清扫,86.6→47.5MB) + **messages uuid 主键+message_meta 拆分**(分类标准=没有它 LLM 上下文是否缺失;ord 会话序锚三踩坑:FTS external-content rowid 限制/轮内序改会话序/max+or 吞 0;审批剥离进 meta;CASCADE 不可靠显式删;迁移独立脚本不进项目代码);pytest 594 绿 + tsc 0 错(2026-09-20)
- [0055 企微中继卡体系](changes/0055_wecom-relay-cards.md) — **功能**:审批按钮卡(批准/拒绝/总是允许,点击即裁决+5 秒窗卡面终态+desc 回填 summary) + clarify 选择卡(vote 官方 schema,单选/多选,选项原文随卡登记 oN 折回作答) + 通用路由表 approvals.py register_card/card_click/card_meta(按 (kind,id) 精确命中,双击/迟到塌缩) + 健康探测持久化(事件回流落盘,2 张 0 事件自动停用回落文本) + cron 后台审批卡 + clarify tool schema multi_select;协议毒字段/事件结构实测见 [[0056]];测试 +17(2026-09-21)

## Insights (决策 / 踩坑)

- [0009 Agent 安全——主人身份与私密信息](insights/0009_agent-security-master-identity.md) — prompt injection vs 代码层硬控制、CaMeL/Claude Code/商用方案、xihe 推荐方案（路径黑名单+脱敏+chat_id 绑定+来源标注）
- [0026 桌面端 Agent 模型定调——内置 xihe + 可添加 claude](insights/0026_desktop-agent-model-built-in-xihe.md) — Agent=类型(内置 xihe 由 main 托管 serve 生命周期 + 可添加 claude connector 占位)，非多实例/非 persona；推翻 [[0025]] Agent 层建模，驱动桌面 F1。2026-09-14 演进：claude/codex 改走 `external_agent` 工具（[[0040]]），connector 路线收敛，骨架清除见 [[0047]]
- [0030 xihe+desktop 打包发行策略](insights/0030_packaging-distribution-strategy.md) — **设计参考（未实现）**：Windows 可行（Electron + 冻结 `xihe serve` 子进程，electron-builder 出 NSIS） / macOS 同框架但需 mac 构建机 + 签名公证（气隙做不了完整 notarization）+ ⚠️ paddle mac arm64 待验证 / **iOS（手机）不能同款**（Electron/Python 不上 iOS）→ 瘦客户端连远程 serve/gateway。**手机用 xihe 的现成路径 = gateway 模式**（企微/飞书跟 bot 说话）。打包本质=决定 Python 大脑跑哪（本地 bundle vs 远程），serve 协议是 enabler。2026-09-15 目录归类订正：concepts → insights（调研/方案对比/设计参考）
- [0041 Server Tool（服务端内置工具）支持](insights/0041_server-tool-support.md) — **设计参考（未实施）**：非 function tools 条目由模型服务端执行（Anthropic/OpenAI Responses/智谱三形态 + inline vs 显式块两风格）；内部网关实测——`glm-5.2-zp` 组（litellm Anthropic 通道）上 `web_search_20250305` 形状**真执行**且产出信息性 tool_call `web_search_prime`（回合成 result 二轮协议已验证），现配 `-openai` 组声明层认识但执行层**静默空转**；方案=config `server_tools` 透传（非 ToolRegistry 成员）+ 序列化/判空两处改 + 命中名单的 tool_call 不 dispatch 直合成 result（dangling-repair/compressor 不变式全保留）+ 流式零协议改动；两车道方向判断（对话增强=server tool 终局 / agent 作业=结构性客户端）。2026-09-15 目录归类订正：concepts → insights（调研/方案对比/设计参考）
- [0056 企微智能机器人长连接卡片协议实测](insights/0056_wecom-aibot-card-protocol.md) — 七轮 spike + 官方文档对照（2026-09-21）：**毒字段静默丢弃**（task_id 冒号/source 字段/错 schema 的 vote 卡——"卡型白名单"假设的真相是 schema 错误，二分变体实验是唯一定位手段）+ 卡型可用性（button/vote/multiple 官方 schema 全通；vote 选项在 checkbox 子对象+submit_button，multiple 在 select_list 三层嵌套）+ 事件结构（template_card_event 嵌套层，vote/multiple 选中项在 selected_items XML 风格数组）+ 更新机制（respond_update_msg 透传事件 req_id，5 秒窗，task_id 必须原样回传否则假成功）+ 连接（单 bot 单连接互顶/chat_type 不填按群聊解析/ack 可能缺失）。AI 生成协议代码"长得对"与"跑得通"的教训

## Candidates (候选 / 待验证)

> 见 [meta/candidates/index.md](../meta/candidates/index.md) — 暂无开放候选。

## 原始材料快照 (raw/sources)

- [browser-tools.md](../raw/sources/browser-tools.md)
- [context-compression.md](../raw/sources/context-compression.md)
- [mcp-dynamic-registration.md](../raw/sources/mcp-dynamic-registration.md)
- [session-design.md](../raw/sources/session-design.md)
- [skills.md](../raw/sources/skills.md)
- [tool-design.md](../raw/sources/tool-design.md)
