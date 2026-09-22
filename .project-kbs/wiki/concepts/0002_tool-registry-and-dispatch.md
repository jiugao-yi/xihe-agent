---
type: concept
title: 工具注册表与调用链
slug: 0002_tool-registry-and-dispatch
aliases:
  - ToolRegistry
  - 工具系统
  - dispatch
tags:
  - architecture
  - tools
  - core
status: active
created: 2026-07-01
updated: 2026-09-14
related_pages:
  - wiki/entities/0001_xihe-agent.md
  - wiki/concepts/0005_mcp-dynamic-registration.md
sources:
  - path: raw/sources/tool-design.md
    date: 2026-07-01
---

# 工具注册表与调用链

## 摘要

xihe-agent 工具系统的核心模式：模块级 `ToolRegistry` 单例 + import 时自注册 + 统一 `dispatch` 调用链。每个 `tools/*.py` 在被 import 时调用 `registry.register(...)` 把自己登记进去，`core/agent/agent.py` 通过 `registry.dispatch(name, args, context=..., parent_agent=...)` 执行，所有 handler 返回 **JSON 字符串**。这是 CLI 与网关两种模式共享的同一套机制。

## 核心要点

- **ToolEntry 字段**: `name` / `toolset` / `schema`(OpenAI function，不含 name) / `handler` / `check_fn` / `requires_env` / `is_async` / `description` / `max_result_size_chars` + `read_only`（并行批分流）/ `description_modifier` / `subagent_blocked`（子代理递归/用户面工具剥离）/ `path_params`（dispatch 侧 cwd 重写）。
- **自注册**: `tools/__init__.py` 的 `load_all_tools()` 自动扫描 `tools/` 下所有非下划线 `.py`，import 触发注册。新增工具必须**同时**注册 **且** 在 `core/toolsets.py` 的某个 toolset 里列出，缺一不可见。
- **统一 handler 签名**: `(args: dict, **kw) -> str`。`context`（chat_id / platform / session_key / user_id）和 `parent_agent` 走 `**kw`，**不**塞进 `args`。用 `tool_result()` / `tool_error()` 替代手写 `json.dumps`。
- **Toolset 分组**: 18 个平铺组（2026-08-16 起 14 组，后增 `base` 读面地板 / `computer` 桌面 / `external_agents` / `meta` 工具自扩展，见 [[0033_specialist-toolset-overhaul]]）。`get_schemas(toolsets=...)` 既做 `check_fn` 过滤（不可用工具整组隐藏），也做子 agent 工具集限制，且是**双路匹配**：静态名单（`resolve_toolset`）∪ registry 注册时 `entry.toolset == ts`——后者是 `mcp-<server>` 免目录条目生效的机制（[[0032_specialist-agents]]）。结果按 (config stamp, roster, subagent) 缓存，register/deregister 即清。
- **dispatch 单门**: `core/registry.py` 的 dispatch 是审批管线唯一汇聚点（`evaluate() → deny / ask→request_approval / remember_rule`，见 [[0037_approval-permission-system]]）+ `path_params` 相对路径 cwd 重写。
- **AuxiliaryClient**: 工具内部需要 LLM 能力（vision / compression / title / tts / image_gen）时**不能**调 `agent.chat()`（会递归 + 污染 session），改走 `AuxiliaryClient`——无状态单次补全、per-task 可配模型、独立超时。通过 `set_auxiliary()` 注入到各 tool 模块。
- **子 Agent (delegate)**: `delegate_task` 创建**独立子 `XiheAgent`**（不调 parent.chat），独立 session、受限 toolset（无请求时 `DELEGATE_DEFAULT_TOOLSETS = [files, terminal, dev_tool, http, web, media]`；递归/用户面工具靠**逐工具 `subagent_blocked` 标签**剥（`get_schemas(subagent=True)`，文档化清单 `SUBAGENT_BLOCKED_TOOLS`），请求的 toolsets 独立于父名单照给）、任务聚焦 prompt、深度上限 `MAX_DEPTH=2`、批量≤3 用 ThreadPoolExecutor 并行。子 agent 共享 parent 的 `db`/`aux`/`compressor` 与 `_approval_shared`（审批任何深度可解）。
- **三层上下文溢出防御**: Layer1 各工具自行截断；Layer2 单结果超 `DEFAULT_RESULT_SIZE`(15k 字符) 落盘到临时文件只留预览（`maybe_persist_tool_result`，`read_file` 被 pin 不落盘防递归）；Layer3 单轮总字符超 `DEFAULT_TURN_BUDGET`(80k) 按大小降序落盘（`enforce_turn_budget`）。
- **防卡死三层**: `max_iterations`(默认30) + 每次 API 调用 120s 超时（`_call_with_retry` + `future.result(timeout)`）+ parent 中断传播。**不**用 `thread.join(timeout)`（线程仍跑）也不 `os.kill`（破坏共享状态）。
- **分层规则（2026-09-01 重构，见 [[0042_package-restructuring]]）**: registry 契约（`ToolRegistry` / `registry` / `tool_result` / `tool_error` / `_run_async`）住在 `core/registry.py` —— 引擎与 tool 模块两侧都要依赖的层；`tools/__init__.py` 只做 re-export + `load_all_tools()` 生命周期，tool 文件的 `from tools import registry, tool_result, tool_error` 短式不变。core **不得**在模块级 import tools（import tool 模块 = 注册副作用）；需要时用函数级延迟 import，由组合根（`core.context`）调 `load_all_tools()`。零依赖的 `core/support/` 是唯一例外，任何层都可模块级引用。

## 适用场景

- 新增工具时遵循「注册 + 入 toolset + 返回 JSON 字串 + handler 用 `**kw` 收 context」四件套。
- 排查「agent 没有某工具」：先看是否注册、是否在 toolset、`check_fn` 是否通过（如 Playwright/MCP server 是否连上）。
- 设计需要 LLM 的工具时，走 `AuxiliaryClient` 而非主循环。
- 理解 delegate / cron 为何能拿到 agent 引用：通过 `parent_agent` kw 注入。

## 相关页面

- [[0001_xihe-agent]] — 项目总览，工具系统是其核心架构之一
- [[0005_mcp-dynamic-registration]] — MCP 工具如何动态注册进同一个 registry
- 原文快照: [raw/sources/tool-design.md](../../raw/sources/tool-design.md)
