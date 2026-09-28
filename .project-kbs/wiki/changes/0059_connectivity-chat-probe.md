---
type: change
title: 连通性探针改最小对话——check_connectivity 弃 GET /models 改 POST /chat/completions
slug: 0059_connectivity-chat-probe
change_type: fix
risk_level: low
status: completed
created: 2026-09-23
updated: 2026-09-23
affected_modules:
  - src/core/services/diagnostics.py
  - src/gateway/serve/system.py
  - src/cli/doctor.py
  - desktop/src/renderer/src/lib/serveClient.ts
  - desktop/src/renderer/src/components/SettingsPanel.tsx
related_pages:
  - wiki/changes/0057_context-compression-v32.md
---

# 连通性探针改最小对话

## 摘要

用户两轮实测指认：「新配置连接失败：端点可达，但不提供 GET /models（不影响使用）」——这个提示自相矛盾（显示为连接失败，文案又说可用）。根因：`check_connectivity` 用 `GET {base_url}/models` 当探针，而**模型清单接口不是模型可用性接口**——很多内网网关不实现 /models 但 /chat/completions 完全正常；且 404 分支返回 `ok: True` + error 文案，桌面端按 `ok` 分支渲染时新旧版本行为不一致时出现"失败+可用"混合提示。修复：探针改为发**一个最小对话**（POST /chat/completions，"ping"，max_tokens=8，带配置的模型名）——测的就是用户需要的能力。pytest（含 3 个新测试）+ tsc + build 绿。

## 变更内容

### diagnostics.py `check_connectivity(base_url, api_key, timeout, model)`

- `GET /models` → `POST {base_url}/chat/completions`，payload `{messages:[{user,"ping"}], max_tokens:8, model}`——与真实对话同一条请求路径
- 判定语义收敛：`ok` 是唯一裁决字段（<400 即可达+认证过）；401/403 → `error`（key 无效）；其余 ≥400 → `error` 带网关错误原文（解析 error.message/message/error 三种返回形态）；`note` 字段承载解释性信息，**永不进 error**（消费方按失败渲染的字段只有 error）
- 返回值不再带 models 数组——清单对本探针无意义（config 已点名要测的模型）

### 消费方同步

- serve `/test-connection`：透传 body 的 model override（桌面测试未保存的 model 表单值也真实生效），响应带 `model_configured`
- `xihe doctor`：传 `config.model`，输出改为「模型 {model} 对话可用」
- 桌面 `serveClient.ts`：`TestConnectionResult` 删 `models`、加 `note`
- 桌面 `SettingsPanel.testConn`：成功文案「连接正常，模型 xxx 可用（note）」，不再依赖 models 计数

## 测试（test_diagnostics.py 新增 3 项）

- 本地 HTTPServer 桩验证：探针命中 `/v1/chat/completions`、带 `Authorization: Bearer`、payload 含模型名与 "ping"；同服务的 GET /models 404 **不影响判定**（新旧接口语义分离的核心断言）
- 401 响应 → `ok: False` + error 提 api_key
- 连接拒绝（原测试保留）

## 踩坑

- **用 A 接口探 B 能力**：探针必须打在"用户要用的那条路径"上；/models 可达 ⇏ /chat/completions 可用，反之亦然（内网 litellm 网关常见 /models 404）
- **`ok: True` 却带 error 文案**是多消费方事故源：裁决字段与解释字段必须分离（本批引入 note），否则任何消费方都可能把解释渲染成失败

## 相关页面

- [[0057_context-compression-v32]] - 同批提交（桌面设置面板同文件改动）
