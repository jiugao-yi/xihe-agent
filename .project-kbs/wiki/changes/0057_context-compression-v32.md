---
type: change
title: 上下文压缩 v3.2——append-only 持久化 + compactions 视图 + usage 校准触发 + 通用摘要
slug: 0057_context-compression-v32
change_type: refactor
risk_level: high
status: completed
created: 2026-09-23
updated: 2026-09-23
affected_modules:
  - src/core/session.py
  - src/core/agent/compressor.py
  - src/core/agent/agent.py
  - src/core/context.py
  - src/core/config.py
  - src/core/support/tool_result_storage.py
  - src/gateway/commands.py
  - src/gateway/serve/conversations.py
  - src/cli/doctor.py
  - desktop/src/renderer/src/components/SettingsPanel.tsx
related_pages:
  - wiki/concepts/0048_sessions-messages-reset.md
  - wiki/changes/0054_messages-uuid-meta-refactor.md
---

# 上下文压缩 v3.2——append-only 持久化 + compactions 视图 + usage 校准触发 + 通用摘要

## 摘要

压缩链路的架构级重构（pytest 全量绿 + tsc 0 错 + build 绿 + 冒烟脚本 11 步断言过）。核心：**DB 是唯一事实源，压缩只是视图函数**——旧方案压缩产物经 `rewrite_messages` 物理覆盖 DB，模型上下文和桌面历史绑死，被摘要的中间回合对人也消失；新方案 messages 表 append-only（行 id 永不变），压缩写一条 `compactions` 记录（head/tail 锚点 + 摘要），模型看 `load_context_messages` 合成的视图，桌面端照常查 raw 表全量历史。配 ChangesPanel 折叠卡片的 serve 数据先行（`GET /convs/{id}/messages` 附带 `compactions` 数组，桌面 UI 后续批次）。设计决策链与已否决项见下方「关键决策」。

## 变更内容

### 持久化层 append-only（session.py）

- `rewrite_messages` 语义改为**只增不改**：`load_messages` 现在给每行带稳定 `_id`（下划线内部键，`_prepare_api_messages` 剥离不进 API）；重 append 时带 `_id` 的行跳过、`_compaction` 标记的合成摘要行跳过，只 INSERT 真正的新尾部行
- **hash 前缀缓存整体删除**（`_persisted`/`_persist_row_key`/`_PERSIST_CACHE_LIMIT`）——append 语义下 `_id` 即天然幂等键；budget nudge 原地改尾行发生在 persist 之前，永远在未持久化窗口内，无需行级 UPDATE
- 新表 `compactions(id, session_id, head_end_id, tail_start_id, summary, summary_role, created_at)` + CRUD（add/latest/for_session/delete）；`sessions` 表加 `last_prompt_tokens` 列
- **`load_context_messages(session_id)`**：raw 行 + 最新 compaction → head 原文 + 合成摘要行（带 `_compaction` 标记，仅存在于内存视图）+ tail 原文；锚点失效（truncate 删了锚点行）→ 删该记录退回全量，下轮重新触发（安全降级）
- `truncate_messages_from`：删除行命中 compaction 锚点 → 清扫该记录；`delete_session`：**先**快照全部 round 的 session_id **再**删 messages，然后清扫 compactions（顺序曾写反被测试抓出）
- `load_messages` docstring 中 rewrite 语义表述同步更新

### 压缩主流程（compressor.py）

- `compress(messages, session_key, session_id)` 产出 compactions 记录（锚点取消息行 `_id`）；返回的压缩列表 = head + 摘要 + tail，后续迭代照常 append
- **`_summaries` 内存 dict 删除**（FIFO 32、进程易失、跨会话串扰史）——渐进摘要的 PREVIOUS SUMMARY 直接从 compactions 表读
- **`_find_tail_start` 改 CJK-aware 口径**（`_text_tokens` + tool_calls arguments）——修平坦 `len//4` 估算：中文密集 tail 实际可达预算 2-3 倍 → 压缩后仍超阈值 → 下轮再压的震荡
- **`_align_head`**：head 保护从硬编码 3 行改为"system + 首个语义完整回合"（首条 user → assistant → 配对 tool 收拢），hard_cap=8，异常态回退旧行为
- **闭环校验**：压缩产物重估仍 ≥ 阈值 → warning 不重试（side-store + 统一估算之后，残余超限只可能是 system prompt 超限或配置错误）
- **摘要 prompt 固定七段**：Goal/Intent、User Messages、Progress、Key Decisions、Key Facts & State、Pending Tasks、Next Steps——场景知识以 Key Facts 事实枚举指令进入（文件路径/命令/URL/浏览器登录态/外部系统状态/定时任务/错误），**零元数据推导**（否决了按 enabled_toolsets 推导场景段和按实际调用推导两个中间方案：能力清单说"agent 有什么本事"不是"对话干了什么"，分组映射对未登记工具/heredoc 写文件天然漏）
- **`_serialize_turns` 尺寸驱动规则**（无工具名知识）：assistant arguments JSON 解析后大字符串字段（>1200）截头 300 保"可命名性"、小字段（path 等）全保；tool 结果头 2500+尾 1000（错误/退出码在尾部）；`<persisted-output>` 存根整块透传（预览即内容）；`_head_tail` 助手

### 触发与微压缩（agent.py / compressor.py）

- **usage 校准触发跨实例化**：`_should_compress` 的 usage 快捷路径（≥threshold 真值 / <0.8×threshold 估算跳过）已存在但 `_last_prompt_tokens` 是 agent 实例字段——gateway/serve 每消息新建 agent，从 0 起步整个失效；现在 `chat()` 开头从 `sessions.last_prompt_tokens` 读入（上轮 `_log_turn_usage` 写回）
- **`lightweight_prune` 微压缩**（L0）：`_prepare_api_messages` API 边界对副本应用——保护最后 20 条 tool 结果，更早 >200 字符 → 占位符；与压缩内占位共享 `_prune_slice` 实现（`<persisted-output>` 存根豁免——可寻址性不变量：压缩/占位永不破坏模型按路径 read_file 取回的能力）；`should_compress` 的估算回退入参同样过 prune（口径一致防虚高触发）
- 压缩内 prune 不再单独执行——append-only 下摘要素材永远从 raw 取原文，"先摘后清"调序问题**天然不存在**

### 周边同步

- `config.py` + `config.example.yaml`：`compression_threshold` 默认 0.50 → **0.75**（零新增配置项/零新增 section——v2 方案的 margin、micro、adaptive_sections、full_args_tools 四个开关全否决，回滚=git revert）
- side-store `_generate_preview`：头尾各半（500+500）——日志/命令输出的结论（错误、退出码）在尾部，原"只保头"恰好藏住最有用的行
- `/compress` 命令（CLI+gateway 同一 handler）走 `load_context_messages` + compress，文案注明完整历史保留
- serve `get_messages` 响应附带 `compactions` 数组（按全部 round 的 session_id 聚合）——桌面折叠卡片数据先行
- serve `/test-connection` 与 `xihe doctor` 传 model（见 0059）

## 关键决策

1. **append-only + compactions 视图**（推翻"摘要质量优先可接受物理覆盖"的 v2 判断）：物理覆盖让桌面历史也被摘要吞掉——用户实测质询"历史都被清理了桌面是不是看不到了"点破该盲区；meta 与消息行的关联（`message_meta` 1:1 挂 id）在旧 rewrite 的 DELETE+INSERT 下靠"续命"维持，append-only 下关联永稳，脆弱耦合直接消失
2. **compactions 用 round 级 session_id + message-id 锚点**（否决 session_key 级 + ord 序号）：ord 是会话级跨 reset 连续的，用它切片会把前 round 消息混入视图；id 永不变且天然 round 内
3. **reset 零额外逻辑**：compactions 按 session_id（round）查，reset 换 round 后查无记录 → 视图自动全量，"干净开局"语义免费
4. **usage 快捷路径已存在**（探索发现）：`_should_compress` 三态判定 2026-09 已实现，本批只修其 gateway 失效；**触发基数 = 最近一次 API 响应的真实 prompt_tokens（已含 system/tools schema/全部历史），估算只管响应后的增量（主要是 tool 结果）**——每次请求都是一次免费全量校准，误差不累积；比纯字符估算高一个数量级（全量估算误差随会话线性放大 ±20K+ tokens，tools schema 完全漏算）
5. **否决后台异步压缩**：与持久化所有权耦合，收益仅省一次 aux 阻塞
6. **否决单条消息上限（L0.5）**：side-store 已在结果组装出口（agent.py dispatch）封顶单条尺寸，源头已堵
7. **存量不兼容**：sessions 表加列、compactions 新表——开发阶段无迁移，旧库按约定废弃

## 测试

- `test_session_incremental.py` **重写**为 append 语义契约 10 项：追加幂等/前缀 id 稳定/压缩后行只增不减/未持久化行恰一次落库/修复行 ord 顺延/truncate 清 compaction 与 meta/delete_session 跨轮清扫/meta round-trip/视图合成/**锚点失效退全量**
- `test_compressor_sessions.py` 改 DB 摘要断言（FakeDB 桩 + 无 db 无渐进）
- 冒烟脚本 `scripts/compress_smoke.py`（无 LLM 依赖）：11 步断言 append 幂等/压缩只写 compactions/视图可重建/桌面 raw 零丢失/二次压缩渐进/truncate 清扫
- **修复存量损坏测试** `test_stream_attribution.py::test_bridge_labels_child_events`（master 上即失败——`FakeChild.chat` 还在用旧扁平回调签名 `stream_delta_callback`，2026-09-17 TurnCallbacks 收敛时漏改；适配 `callbacks.stream_delta` 等）

## 已知边界 / 后续

- 桌面端折叠卡片 UI 未做（serve 数据先行）；`/compact-focus` 未做；30K+ 历史大行清理未做（L0 占位已覆盖 resume 路径——近期 tool 行最大 15105 字符 = side-store 存根本身的大小，>20K 的 65 条全是 2026-08 前旧管道遗留）
- 阈值 0.75 的估算误差余量收窄（0.85 档）依赖 usage 校准精度，暂不动

## 相关页面

- [[0048_sessions-messages-reset]] - 会话数据模型（本批订正持久化语义与不变量）
- [[0054_messages-uuid-meta-refactor]] - 前一批 uuid 主键 + meta 拆分（append-only 在其上落地）
