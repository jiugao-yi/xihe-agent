---
type: concept
title: 会话数据模型：sessions / messages 表与 reset 机制
slug: 0048_sessions-messages-reset
aliases:
  - 会话数据模型
  - sessions messages 表
tags:
  - architecture
  - session
  - storage
status: active
created: 2026-09-10
updated: 2026-09-20
related_pages:
  - wiki/concepts/0006_session-design.md
  - wiki/concepts/0024_desktop-serve-protocol.md
  - wiki/insights/0026_desktop-agent-model-built-in-xihe.md
  - wiki/changes/0054_messages-uuid-meta-refactor.md
---

# 会话数据模型：sessions / messages 表与 reset 机制

> 核心文件：`src/core/session.py`（SessionDB / ResetPolicy / SessionSource）
> 消费方：`src/core/agent/agent.py`（持久化）、`src/gateway/serve/chat.py`（REST/WS 读写）、桌面端（只消费 serve API）

## 1. 表结构

### sessions 表（1 行 = 1 个对话）

```sql
CREATE TABLE sessions (
    session_key TEXT PRIMARY KEY,  -- 确定性查找键：agent:main:{platform}:{chat_type}:{chat_id}
    session_id  TEXT NOT NULL,     -- 内部唯一 ID（messages 表外键），格式 {时间戳}_{uuid8}
    platform    TEXT,              -- serve / wecom / feishu / cron / delegate
    chat_id     TEXT,              -- 外部会话标识（桌面的 conv_id / 企微 chat_id）
    user_id     TEXT,
    chat_type   TEXT DEFAULT 'dm',
    title       TEXT,
    origin      TEXT,              -- SessionSource 序列化（重建会话用）
    was_auto_reset INTEGER DEFAULT 0,
    auto_reset_reason TEXT,        -- "idle" / "daily"
    model       TEXT,              -- per-session model override（/model 切换）
    created_at  TEXT,
    updated_at  TEXT
)
```

### messages 表（N 行 = 1 个对话的消息流）

> **2026-09-20 重构**（[[0054_messages-uuid-meta-refactor]]）：主键换 uuid4、新增 ord 序锚、
> 应用附属四样拆到 message_meta。分类标准 = **没有它 LLM 上下文是否缺失**：
> messages 只存模型面向的对话事实（user/assistant/tool 三种行），附属数据全在 meta 表。

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,           -- uuid4 hex（message_meta 外键的稳定锚——自增 id 会被
                                   -- rewrite_messages 的 DELETE+re-INSERT 撕断引用）
    session_id TEXT NOT NULL,      -- 轮次标签（换轮后旧行的 id 不再出现在 sessions 表）
    role TEXT NOT NULL,            -- user / assistant / tool（system 不再持久化——prompt
                                   -- 每轮从代码+config+skills 重建，_chat_turn 无条件插头部）
    content TEXT,                  -- 用户原文 / 助手文本 / 工具输出（附件提示块、审批判决
                                   -- 等应用数据一律不混入）
    tool_calls TEXT,               -- JSON 数组，仅 assistant 行
    tool_call_id TEXT,             -- 仅 tool 行
    ord INTEGER NOT NULL,          -- 会话内行序锚（跨 reset 轮单调递增）：uuid 无序、
                                   -- created_at 同一 rewrite 内多行全等，这是唯一排序依据；
                                   -- load/转录/truncate 全按它
    created_at TEXT,
    session_key TEXT,              -- 所属会话键——跨轮寻址的唯一依据
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
)
CREATE INDEX idx_messages_session ON messages(session_id)
CREATE INDEX idx_messages_order ON messages(session_key, ord)
CREATE INDEX idx_messages_conv ON messages(session_key)

CREATE TABLE message_meta (        -- 应用附属（1:1 从属；写一次跟主行同事务）
    message_id TEXT PRIMARY KEY,   -- FK → messages.id
    reasoning TEXT,                -- 模型思考留影（assistant 行，仅显示）
    usage TEXT,                    -- 该轮 token 用量 JSON（turn 末 assistant 行）
    attachments TEXT,              -- 用户附件元数据 JSON（user 行）；content 保持原文，
                                   -- 模型面向的提示块由 _prepare_api_messages 在 API 边界注入
    approval TEXT,                 -- 审批决议 JSON（过审批门的 tool 行；持久化时从 result
                                   -- JSON 剥离，content 保持纯工具输出；模型不再看到）
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
)
-- CASCADE 不生效（库未开 foreign_keys）：删主行必须显式删 meta——rewrite 尾删 /
-- truncate_messages_from / delete_session 三处都做
-- 另有 messages_fts（FTS5 内容表模式：fts5(message_id UNINDEXED, content)——不能用
-- external-content 模式，content_rowid 要求 id 是 rowid 别名，TEXT 主键会 datatype mismatch）
-- 历史库迁移：独立脚本 scripts/migrate_messages_v2.py（不在项目代码里）；
-- 本实例已迁移完成
```

### 三个 ID 的分工

| 字段 | 谁认识 | 用途 |
|---|---|---|
| `session_key` | 内部代码 | `get_or_create_session()` 的唯一索引，从 SessionSource 确定性推导 |
| `session_id` | 仅 DB 内部 | `messages.session_id` 外键；`load_messages()` / `rewrite_messages()` 按 it 加载 |
| `chat_id` | 桌面/企微/飞书 | 外部世界对"这个对话"的标识；serve 收到 WS 帧的 conv_id 就是 it |

## 2. 会话生命周期

### 创建/获取（`get_or_create_session`）

```
入口：agent.chat() 或 serve 的 _handle_send
  │
  ├─ source = SessionSource(platform, chat_id, user_id, chat_type)
  ├─ session_key = build_key(source) → "agent:main:serve:dm:{chat_id}"
  │
  ├─ 缓存里有 entry？
  │   ├─ 是 → 检查 ResetPolicy.should_reset()
  │   │   ├─ 需要重置 → _create_session(was_auto_reset=True)
  │   │   └─ 不需要 → 更新 updated_at，返回既有 session_id
  │   └─ 否 → 查 DB；有 → 加载缓存；没有 → _create_session()
  │
  └─ _create_session() → INSERT sessions 行，生成新 session_id
```

### 消息持久化（agent loop）

```
agent.chat() 内部，每次迭代结束调 _persist_messages(session_id, messages)
  → rewrite_messages()：增量前缀匹配（对比上次写入的行哈希——只哈希模型面向列，
    meta 不参与 diff；meta 只随主行创建写一次），
    按 ord 定位分歧行，只 DELETE 分歧行（显式连删 meta）+ INSERT 尾部新行
    （新行 uuid 主键、ord 接续会话内 max+1）→ O(增量) 而非 O(全量)
```

一轮（turn）的消息序列（DB 中的行；id 为 uuid 示意缩写）：

```
ord=0 id=ab12 role=user      "帮我查一下..."（meta.attachments 可带附件元数据）
ord=1 id=c3d4 role=assistant tool_calls=[search_files(...)]
ord=2 id=e5f6 role=tool      (搜索结果 JSON；过审批门时 meta.approval 带判决)
ord=7 id=90a1 role=assistant tool_calls=[read_file(...)]
ord=8 id=b2c3 role=tool      (文件内容)
ord=9 id=d4e5 role=assistant "查到了，结果是..."（无 tool_calls = settled；meta.usage 带用量）
```

### 附件提示块的注入路径（2026-09-20）

用户消息带附件时，落库的 content 是**原文**；模型看到的提示块
（`[用户发送了文件 …服务器路径… 需要内容时用你可用的工具读取]`）由
`_prepare_api_messages` 在 API 边界从 `_attachments` 元数据派生注入，永不落库——
历史每轮重新注入，桌面转录永远显示原文 + 附件 chips（不变形）。
serve 上传后先经 `_describe_attachments`（executor 线程）给图片补视觉描述 desc。

### 中断时的部分文本持久化

```
agent loop 检查点 2（API 返回后、messages.append 前）被中断：
  content = 模型已流式输出的部分文本
  → messages.append({"role":"assistant", "content": content + "\n\n[被中断，以上为部分输出]"})
  → _persist_messages()  ← 部分文本带标记落库，刷新不丢
```

桌面端 `_reshape_history` 折叠时：
- 识别 `[被中断` 标记 → 不设 settled → `incomplete=True` → 显示"未完成"徽标 + "继续"按钮
- 显示层从 content 中剥掉标记（桌面看到干净的文本）
- 模型下一轮看到标记，知道该从断点续写

## 3. Reset 机制

### ResetPolicy（`session.py:136-165`）

```python
mode: "idle" | "daily" | "both" | "none"   # 默认 "none"（2026-09-14 起改为 none，此前为 idle）
idle_minutes: 1440                           # 默认 24 小时
daily_reset_hour: 4                          # 默认凌晨 4 点
```

配置（config.yaml `session` 段；`platforms.<平台名>` 可按平台覆盖，子键为 `reset` / `idle_minutes` / `daily_reset_hour`）：

```yaml
session:
  default_reset: none      # none（默认）= 同一会话永久延续，历史上下文完整
  idle_minutes: 1440
  daily_reset_hour: 4
```

### 触发时机

`get_or_create_session()` 每次被调用时检查：

- **idle 模式**：`updated_at + idle_minutes < now` → 触发
- **daily 模式**：`updated_at < 今天{daily_reset_hour}点` → 触发
- **both**：任一条件满足即触发

### 重置做了什么

```
_reset（自动 or 手动，2026-09-15 修订）:
  messages 行不动——新行都带 session_key（会话键），跨轮可达
  sessions 表同一 session_key 换成新 session_id（ON CONFLICT DO UPDATE：
  只动 round 级列——title / per-session model 等会话级列保留；
  旧实现 INSERT OR REPLACE 曾把标题和 model 一并清空）
  was_auto_reset=1, auto_reset_reason="idle"/"daily"
  reset_marks 表插一行（session_id=新轮, reason='manual'|'idle'|'daily'）——每次重置的原因持久化
  → 模型上下文从空白开始（load_messages 只读当前 session_id），
    但转录视图（load_transcript 按 session_key）跨轮可见重置前历史
```

三层都适用：桌面 `GET /convs/{id}/messages`、gateway/CLI `/history`（共用 `handle_command`）、`session_search`（FTS/LIKE 的 JOIN 改按 session_key，旧轮可搜）都读跨轮转录；`/sessions` 的 msg_count 也是会话级计数。

### 重置分隔条（round divider，2026-09-15）

- **派生 + 打标**：`_reshape_history(rows, marks)` 在行序里看 session_id 变化，把每轮首条折叠消息打上 `round_start: true`（+ `reset_reason`，来自 reset_marks；更早的边界无原因只打标）；呈现归客户端。
- **桌面**：ChatPanel 在 `roundStart` 消息前渲染 `⟲ 上下文已重置（手动/闲置超时/每日重置）` 分隔条（优先于时间分隔条）；**尾部即时反馈**——响应带 `pending_reset`（重置已发生但新轮尚无消息）时追加一条 `dividerOnly` 合成消息，点完重置立刻看到分隔线；**自动重置的即时反馈**——自动换轮发生在发消息时（`get_or_create_session` 内部，晚于桌面乐观追加），`turn_start` 帧带 `auto_reset`（`SessionDB.pending_auto_reset` 用同一 ResetPolicy 预判），桌面就地给刚追加的用户消息打 `roundStart`，不必等重拉。
- **重置入口**：ChatPanel 输入框左侧 RotateCcw 按钮（`resetConversation` → POST reset → 重拉转录；标题与历史保留；未连接/回合进行中禁用）。
- **gateway/CLI `/history`**：轮切换处插一行 `──── 上下文已重置（原因）────`。

### 手动 reset vs 删除

| 操作 | API | 效果 |
|---|---|---|
| 手动 reset | `POST /convs/{id}/reset` | 同一 session_key 换新 session_id；标题/model 保留；转录跨轮可见 |
| 删除 | `DELETE /convs/{id}` | sessions 行 + **所有轮次**的 messages 全删（session_key ∪ session_id 作用域）；FTS 索引同步 |
| 回滚 | `POST /convs/{id}/truncate` | 跨轮作用域：删指定行及其后**全对话**的行（重发目标可能在旧轮） |

### reset 旧消息的生命周期

- 2026-09-15 起 messages 表带 `session_key` 列：跨轮转录/搜索/删除/回滚都按会话键作用域。
- **2026-09-20**：孤儿行（session_key 为 NULL 的 reset 遗留）已一次性清理（7,444 条）+ system 行清零（769 条，且此后不再持久化）+ VACUUM，86.6→47.5 MB；uuid 重构后新 schema 无迁移逻辑在项目代码里，历史库走 `scripts/migrate_messages_v2.py` 一次性搬运。
- serve 端点补充（2026-09-14）：除页面表中列的外还有 `POST /convs/{id}/truncate`（重发回滚）与 `POST /convs/{id}/title`（改名）。truncate 的 `from_msg_id` 现为 uuid 字符串（2026-09-20 起）。

## 4. 数据流总图

```
桌面端                    serve                        SessionDB
──────                    ─────                        ─────────
会话列表 ←── GET /sessions ── list_sessions() ── SELECT sessions

选会话 → activeConvId
  │
发消息 → WS {"type":"send","conv_id":"..."}
  │         │
  │         ├─ _source(conv_id) → SessionSource
  │         ├─ build_key() → session_key
  │         ├─ get_or_create_session() ←── ResetPolicy 检查在这里
  │         ├─ create_agent() → agent.chat(source, text)
  │         │     │
  │         │     ├─ load_messages(session_id)  ← 加载历史
  │         │     ├─ 修复悬空 tool_calls + 注入恢复提示
  │         │     ├─ agent loop（迭代）
  │         │     │   ├─ API 调用 → assistant_msg append
  │         │     │   ├─ 工具执行 → tool_msg append
  │         │     │   └─ _persist_messages() ←── 每次迭代落库
  │         │     └─ return 最终文本
  │         │
  │         └─ complete 事件（WS 推送）
  │              content = 最终回复文本（唯一权威源）
  │
查看历史 ←── GET /convs/{id}/messages ── _reshape_history()
                                               │ 折叠消息流为气泡（LEFT JOIN message_meta：
                                               │   user 气泡带 attachments，刷新不变形）
                                               │ user 行 → user 气泡
                                               │ assistant+tool 行 → assistant 气泡
                                               │    tools = tool 行计数
                                               │    has_reasoning = reasoning 非空
                                               │    incomplete = 无 settled 行
                                               │    ts = 首行 created_at
                                               └ 返回 [{role, content, id(uuid), tools, ...}]

展开 trace ←── GET /convs/{id}/trace/{msg_id} ── 从 anchor 行(uuid)走到下一条 user 行
                                                   收集 tool_calls + reasoning 事件；
                                                   审批徽章从 meta.approval 读
```

## 5. 关键不变量

1. **session_key 是确定性的**：同一个 (platform, chat_id, user_id, chat_type) 永远生成同一个 key
2. **messages 只增不删**（除非 reset/delete）——正常迭代中 `rewrite_messages` 是前缀追加
3. **agent loop 每次迭代后落库**——中断/崩溃不丢已完成的工作
4. **部分中断文本带标记落库**（2026-09-10 加）——刷新后保留 + fold 识别为未完成
5. **reset 换 session_id 不删旧消息**——旧消息跨轮可达（session_key 键控）
6. **`_reshape_history` 的 settled 判据**：无 tool_calls 的 assistant 行 = 最终回复；带 `[被中断` 标记的除外
7. **content 是模型面向的纯净通道**（2026-09-20 起）：用户原文不拼附件块、工具输出不带审批判决、system 不落库——应用附属数据全在 message_meta，模型历史和展示各取所需（2026-09-10 版的「reset 后不可达」表述随孤儿清理与 key 键控失效）
8. **meta 写一次跟主行**：`_persist_row_key` 不哈希 meta——meta 随主行创建落一次库，后续 rewrite 前缀匹配不会重写它；load_messages 用下划线内部键（`_reasoning`/`_usage`/`_attachments`）带回，`_prepare_api_messages` 剥离（approval 例外：永不回模型）
9. **uuid 行序靠 ord**：所有「按行序」的 SQL（load/转录/truncate 定位/usage 定位）必须 ORDER BY / 比较 `ord`，绝不能 ORDER BY id（uuid 随机序）；跨会话无全局序（search 的 LIKE 回退退化为 created_at 近似排序）
