---
type: change
title: 架构重构——plan mode/turn_layers/AgentTurnRunner 三层收敛 + office 工具集 + 技能/终端整备
slug: 0053_architecture-refactor
change_type: refactor
risk_level: medium
status: completed
created: 2026-09-17
updated: 2026-09-17
affected_modules:
  - src/core/agent/turn_layers.py
  - src/core/agent/turn_runner.py
  - src/core/agent/agent.py
  - src/core/toolsets.py
  - src/tools/office_tool.py
  - src/tools/image_render_tool.py
  - src/skills/visualization/
  - src/skills/code-review/
  - desktop/src/main/terminals.ts
  - desktop/src/renderer/src/components/ShellPanel.tsx
related_insights:
  - wiki/concepts/0052_three-mode-architecture.md
---

# 架构重构——plan mode/turn_layers/AgentTurnRunner 三层收敛 + office 工具集 + 技能/终端整备

## 摘要

多轮推进的架构整备批次（2026-09-15/17，六个提交）：harness 级 plan mode（roster 物理限制 + 结构化批准）、turn 级提示层统一机制（turn_layers）、三模式 turn 编排收敛（AgentTurnRunner）、chat() 巨型函数拆分、office 工具集（Excel/Word）、终端体系整备（agent 终端收敛 + ShellPanel 本地终端）、技能体系重整（visualization/code-review 落地，software-development 7→4 再并入）。pytest 584 绿 + tsc 0 错 + 桌面 build 绿。分层设计见 [[0052_three-mode-architecture]]。

## 批次内容（按提交序）

1. **office 工具集**：office 工具（excel/word 读写四动作，openpyxl/python-docx）+ send_file 网关投递（复用 drain 的 send_document 分支）。
2. **终端体系整备**：agent 终端自动打开机制全拆（纯手动）；ShellPanel 本地终端（多 tab 交互式，terminals.ts）；terminal 打印治乱（LineTap/StreamDecoder）。
3. **可视化体系**：visualization skill（借鉴 doubao-visualization）+ 桌面 html renderer 沙箱 + image_render 四源工具 + xfile 本地图片协议。
4. **plan mode**：roster 物理限制只读 + clarify 结构化批准（plan_approved 标志，零文本解析——旧 PLAN_APPROVED 文本标记协议废弃）+ 三入口批准后自动执行轮。
5. **turn_layers 统一**：轮级提示层机制（API 边界注入不落库），修 plan layer 借道 messages[0] 持久化的污染隐患；regenerate 遗忘语义（REGENERATE_HINT）。
6. **chat() 拆分**：25 行门面 + _chat_turn 轮体 + _dispatch_tools 调度；修门面 finally 引用内层局部变量的 NameError 隐患。
7. **TurnCallbacks**：8 回调参数收敛为 dataclass，三入口迁移。
8. **技能重组**：diagramming 删（气隙不可用）；software-development 7→4（planning/execution/code-review/testing）再并入 execution 于 Runner 后删除——最终 bundled = code-review/visualization/ssh-access/web-record-to-skill。
9. **AgentTurnRunner**（本批核心）：三入口 turn 编排收敛，见 [[0052_three-mode-architecture]]。
10. **CLI 构造门脸统一**：init_agent 移出 core（_build_cli_agent），三模式同走 ctx.create_agent 工厂，生命周期差异=调用次数。
11. **杂项**：handle_command 去 agent 化（cmd_ctx 携带 db/config/compressor）；glm-5 → 200k 目录修正；cli/cli.py 与 serve/conversations.py 重名消除（chat.py 清零）+ serve 启动冒烟测试。

## 关键决策（详见 0052）

- 构造即就绪：Runner __init__ 完成 agent 就位（传入复用/未传自建），无未就绪中间态。
- turn 层 API 边界注入（messages 是账本，API 形态是快照）。
- 结构化批准替代文本标记（用户点击 = 确定性输入）。
- CLI 保持长生命周期 agent（实例态 REPL 需要），构造门脸统一但调用次数不同。
- gateway late-steer 重排队 vs serve 丢弃：业务事实不同，不强制统一。

## 验证

pytest 584 passed / 20 skipped（含新增 test_serve_assembly 启动冒烟、test_plan_mode 9 项、test_regenerate_hint 2 项、test_image_render 10 项、test_office 5 项）+ tsc 0 错 + 桌面 build 绿。

## 回滚

六个提交各自独立可 revert（9048ff2 → 7769349 → 6823044 → …→ d96932e/e2ba 顺序见 git log）。

## 已知边界

- serve 组装路径（add_routes）原为 pytest 盲区——已补真实启动冒烟，但更细的逐端点行为仍靠 E2E。
- Runner.plan_outcome 依赖 chat 返回文本即计划正文；模型若在批准后追加非计划文本会一并落盘（低危，文案约束兜底）。
