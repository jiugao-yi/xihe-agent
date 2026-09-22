---
type: change
title: 会话数据模型重构——messages uuid 主键 + message_meta 拆分 + 附件气泡不变形
slug: 0054_messages-uuid-meta-refactor
change_type: refactor
risk_level: high
status: completed
created: 2026-09-20
updated: 2026-09-20
affected_modules:
  - src/core/session.py
  - src/core/agent/agent.py
  - src/core/agent/turn_runner.py
  - src/core/registry.py
  - src/gateway/serve/conversations.py
  - desktop/src/renderer/src/lib/serveClient.ts
  - desktop/src/renderer/src/appStore.ts
  - desktop/src/renderer/src/components/ChatPanel.tsx
  - desktop/src/renderer/src/components/TurnTrace.tsx
related_pages:
  - wiki/concepts/0048_sessions-messages-reset.md
  - wiki/concepts/0037_approval-permission-system.md
  - wiki/concepts/0024_desktop-serve-protocol.md
---

# 会话数据模型重构——messages uuid 主键 + message_meta 拆分 + 附件气泡不变形

## 摘要

2026-09-20 一天内的四个相关批次（pytest 594 绿 + tsc 0 错）：

1. **附件气泡不变形**（起点：用户报「上传文件后原本输入变了」）——附件提示块从「拼接进用户消息持久化」改为「attachments 列存元数据 + API 边界注入提示块」，content 永远是用户原文。
2. **危险操作审批记录持久化**（起点：用户报「刷新后看不到审批记录」）——审批决议内嵌 tool result JSON 随行落库，trace 端点提取，桌面渲染徽章。
3. **messages 表垃圾清理**——删 769 条 system 行 + 7,444 条孤儿行 + VACUUM，86.6→47.5 MB；system 行此后不再持久化（每轮重建）。
4. **messages/message_meta 拆分重构**（本批核心）——主键换 uuid4、新增 `ord` 会话序锚、应用附属四样（reasoning/usage/attachments/approval）拆到 message_meta 表；独立一次性迁移脚本搬历史数据。

## 批次 1：附件气泡不变形

**根因**：serve `_compose_attachment_text` 把 `[用户发送了文件…服务器路径…]` 拼进用户消息正文并持久化——桌面 refetch 后显示的就是拼接文本，「原本的输入变了」。

**方案**（讨论中三次收敛：meta 列 → 列只存附件 → 定稿 attachments 列 + API 边界注入）：
- `chat(attachments=...)` 新参数：user 行 content 存原文，`_attachments` 内部键随行携带元数据
- `_prepare_api_messages` 剥离下划线键后把 `_attachment_hint(atts)`（路径/大小/图片描述提示块）注入对应用户消息——复用 memory-context 的 API 边界注入模式，永不落库；历史每轮重新注入
- serve `_describe_attachments` 只产出元数据数组（图片补 `desc` 视觉描述），**executor 线程跑**（顺带修了原来在事件循环线程同步调视觉 LLM 卡 loop 的缺陷）
- 桌面：`fetchHistory` 映射 `attachments`；重发/编辑/重新生成携带原附件回传
- **顺带修复**：`_prepare_api_messages` 注入时的索引错位 bug（user 计数器 vs 全列表索引，system 前缀导致永远取不到 `_attachments`）——改 `zip(messages, _src)` 位置对齐

**踩坑**：全量测试没抓到索引错位——测试里消息列表第一条就是 user（无 system 前缀），恰好绕开错位路径。契约测试必须覆盖「system 行在前」的真实形状。

## 批次 2：审批记录持久化

**根因**：审批卡是纯内存 WS 事件（`pendingApproval` 挂在流式中的 assistant 气泡上），刷新即失；库里只有 blocked tool result 的错误文本，看不出「弹过审批」。

**方案**（三选一讨论后定 A「内嵌 tool result」）：dispatch 审批门把 `approval_record {decision, summary, reason, always}` 注入返回 JSON——批准路径补进成功 result，拒绝路径进 `tool_error`。serve `get_trace` 提取 → trace 事件带 `approval` → 桌面 `ApprovalBadge`（琥珀「已批准（不再询问）/已批准」/红「已拒绝」，hover 显示 summary+reason）。三模式统一生效（CLI/gateway 日志同样带记录）。

**后来在批次 4 中演进**：approval_record 从 content JSON 剥离进 message_meta.approval 列（见下），模型从此不再看到该字段。

## 批次 3：垃圾清理 + system 不再持久化

数据体检（86.6 MB / 26,052 行）：tool 结果 19.5 MB（spill 机制工作正常，>100KB 行为 0）、**system 提示 769 行 6.1 MB**（40KB 提示词每 round 重存一份，仅 213 个不同版本）、**孤儿行 7,572 条 8.4 MB**（session_key 列引入前的 reset 遗留，永远不可达）。

- 一次性清理：删 system 行 + `session_key IS NULL` 孤儿 + FTS rebuild + VACUUM → **86.6→47.5 MB**
- system 行不再持久化：`rewrite_messages`/`append_message` 开头过滤；`_chat_turn` 无条件 `insert(0, system_msg)`（原「有则替换无则插入」两分支简化）——prompt 每轮重建，持久化纯属每 round 存 40KB 近重复
- `_reshape_history` 的 system 兼容分支删除

## 批次 4：messages uuid 主键 + message_meta 拆分（核心）

**分类标准**（与用户对齐的定义）：**没有它 LLM 上下文是否缺失**——user/assistant/tool 三种行是模型面向对话事实；reasoning（思考留影，模型上下文零贡献）/usage（成本）/attachments（附件元数据）/approval（审批判决）全是应用附属。

**schema**：

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,        -- uuid4 hex（meta 外键的稳定锚；自增 id 会被
                                -- rewrite 的 DELETE+re-INSERT 撕断外键引用）
    session_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_calls TEXT, tool_call_id TEXT,
    ord INTEGER NOT NULL,       -- 见下「ord 三踩」
    created_at TEXT, session_key TEXT
);
CREATE TABLE message_meta (
    message_id TEXT PRIMARY KEY,  -- FK → messages.id
    reasoning TEXT, usage TEXT, attachments TEXT, approval TEXT,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);
```

**关键决策**：
- **不按业务分三张附属表**：三样数据生命周期/读写模式完全相同（1:1 从属、跟主行同事务写、无独立检索场景），分表是假分——每张表的 JOIN 成本与一致性维护是每天付的，未来某样长出独立查询需求再单拆（JSON 列迁移是一天的活）
- **审批持久化时剥离**：dispatch 照旧注入 `approval_record`（当场给桌面 WS 用），rewrite 写 tool 行时摘出进 meta.approval——content 落库纯净，模型不再看到该字段；**被拒调用的错误文本本身留 content**（那是模型面向的执行结果「没批准+别绕路」，不是审计数据）
- **CASCADE 不可靠**：库未开 foreign_keys，删主行必须显式删 meta（rewrite 尾删 / truncate / delete_session 三处）
- **表结构变更/迁移不进项目代码**：代码只含新 schema 的 CREATE；旧库保护与迁移全部走独立脚本

**ord 三踩**（uuid 无序后行序锚的三个连环坑）：
1. FTS external-content 模式 `content_rowid='id'` 要求 id 是 rowid 别名，TEXT 主键插入直接 `datatype mismatch` → FTS 改内容表模式（`fts5(message_id UNINDEXED, content)` + 触发器显式维护，search 的 snippet 列号 -1→1）
2. `ord` 初版按 session_id（轮）内单调——但跨轮 transcript `ORDER BY ord` 在轮间并列乱序、跨轮 truncate 定位错 → 改为**按 conversation（session_key）单调**，rewrite 接续 conv 内 max
3. `max(ord) or -1` 把合法的 0 吞成 -1 → 全部 ord=0、排序崩坏 → `row[0] is None` 显式判空

**其他**：
- `truncate_messages_from(from_id)` int→uuid，`ord >= (SELECT ord WHERE id=?)` 定位；`set_last_assistant_usage` 写 meta.usage（按 ord DESC 定位）；search 的 LIKE 回退 `ORDER BY created_at`（近似，跨会话无全局序）
- 桌面 id 全部 string 化（serveId/traceAnchor/getTrace/truncate 参数）——id 只当不透明令牌，逻辑零变化

## 迁移脚本 `scripts/migrate_messages_v2.py`

一次性运维脚本（不属于包、代码零引用）：`ALTER TABLE RENAME` → 建新表 → 按 rowid 序搬运（新 uuid、ord=搬运序号按会话分组递增）→ 扫描 tool 行 content 剥 `approval_record` → FTS 重建 → 行数对账。**幂等/可恢复**：检测「新表已建但空 + `_messages_old` 在」→ 续搬（第一次跑因索引名冲突中断后，重跑完成）。实例库执行结果：17,843 行对账一致、1,008 meta、1 条审批剥离、ord 单调校验 0 坏组；备份表已删 + VACUUM 78.4 MB。

## 验证

pytest 594 passed / 20 skipped（新增 test_message_meta 4 项契约：meta round-trip / approval 剥离 / truncate 级联 / ord 跨轮递增；test_serve_upload / test_approvals / test_session_incremental / test_truncate / test_session_rounds / test_session_usage 断言适配）+ tsc 0 错。

## 回滚

四批次各自独立；批次 4 涉及 schema 不可就地回滚（恢复旧代码需配套恢复旧库——迁移脚本的 `_messages_old` 备份路径是唯一回滚资产，本实例已删）。
