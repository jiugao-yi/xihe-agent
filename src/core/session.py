"""Session management — session key generation, reset policies, and storage.

Key concepts:
  - session_key: deterministic logical key identifying "which conversation"
  - session_id: physical instance identifying "which round" (changes on reset)
  - SessionSource: describes where a message comes from
  - ResetPolicy: configurable idle/daily/both reset strategies
  - SessionEntry: tracks current session metadata per session_key
"""

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from core.config import AGENT_HOME

logger = logging.getLogger(__name__)

SESSIONS_DIR = AGENT_HOME / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

_DB_PATH = SESSIONS_DIR / "sessions.db"


def _now() -> str:
    return datetime.now().isoformat()


def _now_dt() -> datetime:
    return datetime.now()


@dataclass
class SessionSource:
    """Describes where a message originated from.

    Used to:
    1. Generate a deterministic session key
    2. Route responses back to the right place
    3. Track origin for scheduled task delivery
    """
    platform: str
    chat_id: str
    chat_name: Optional[str] = None
    chat_type: str = "dm"      # "dm", "group", "channel", "thread"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    thread_id: Optional[str] = None  # Sub-topic (Telegram topics, Discord threads, etc.)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionSource":
        return cls(
            platform=data["platform"],
            chat_id=str(data["chat_id"]),
            chat_name=data.get("chat_name"),
            chat_type=data.get("chat_type", "dm"),
            user_id=data.get("user_id"),
            user_name=data.get("user_name"),
            thread_id=data.get("thread_id"),
        )


def build_session_key(
    source: SessionSource,
    group_sessions_per_user: bool = True,
    thread_sessions_per_user: bool = False,
) -> str:
    """Build a deterministic session key from a message source.

    DM rules:
      - DMs include chat_id when present, isolating each private conversation.
      - thread_id further differentiates threaded DMs within the same chat.
      - Without chat_id, thread_id is used as a best-effort fallback.
      - Without thread_id or chat_id, DMs share a single session.

    Group/channel rules:
      - chat_id identifies the parent group/channel.
      - user_id isolates participants within that parent chat when
        ``group_sessions_per_user`` is enabled.
      - thread_id differentiates threads within the parent chat.  When
        ``thread_sessions_per_user`` is False (default), threads are *shared*
        across all participants — user_id is NOT appended, so every user in
        the thread shares a single session.
      - Without identifiers, messages fall back to one session per platform/chat_type.
    """
    platform = source.platform
    if source.chat_type == "dm":
        if source.chat_id:
            if source.thread_id:
                return f"agent:main:{platform}:dm:{source.chat_id}:{source.thread_id}"
            return f"agent:main:{platform}:dm:{source.chat_id}"
        if source.thread_id:
            return f"agent:main:{platform}:dm:{source.thread_id}"
        return f"agent:main:{platform}:dm"

    participant_id = source.user_id
    key_parts = ["agent:main", platform, source.chat_type]

    if source.chat_id:
        key_parts.append(source.chat_id)
    if source.thread_id:
        key_parts.append(source.thread_id)

    # In threads, default to shared sessions (all participants see the same
    # conversation). Per-user isolation only applies when explicitly enabled
    # via thread_sessions_per_user, or when there is no thread (regular group).
    isolate_user = group_sessions_per_user
    if source.thread_id and not thread_sessions_per_user:
        isolate_user = False

    if isolate_user and participant_id:
        key_parts.append(str(participant_id))

    return ":".join(key_parts)


@dataclass
class ResetPolicy:
    """Determines when a session should auto-reset."""
    mode: str = "none"           # "idle", "daily", "both", "none"
    idle_minutes: int = 1440     # 24 hours
    daily_reset_hour: int = 4    # 4 AM local time

    def should_reset(self, updated_at: datetime,
                     now: Optional[datetime] = None) -> Optional[str]:
        """Check if a session should be reset.

        Returns the reset reason ("idle" or "daily") if reset is needed, or None.
        """
        if not now:
            now = _now_dt()

        if self.mode == "none":
            return None

        if self.mode in ("idle", "both"):
            idle_deadline = updated_at + timedelta(minutes=self.idle_minutes)
            if now > idle_deadline:
                return "idle"

        if self.mode in ("daily", "both"):
            today_reset = now.replace(
                hour=self.daily_reset_hour,
                minute=0, second=0, microsecond=0,
            )
            if now.hour < self.daily_reset_hour:
                today_reset -= timedelta(days=1)
            if updated_at < today_reset:
                return "daily"

        return None


def load_reset_policy(config: dict, platform: Optional[str] = None,
                      chat_type: Optional[str] = None) -> ResetPolicy:
    """Load reset policy from config, with per-platform overrides.

    Config structure (in config.yaml):
        session:
          default_reset: none
          idle_minutes: 1440
          daily_reset_hour: 4
          platforms:
            wecom:
              idle_minutes: 720
    """
    session_cfg = config.get("session", {}) or {}

    if platform:
        platform_cfg = session_cfg.get("platforms", {}).get(platform, {}) or {}
        if platform_cfg:
            return ResetPolicy(
                mode=platform_cfg.get("reset", session_cfg.get("default_reset", "none")),
                idle_minutes=platform_cfg.get("idle_minutes", session_cfg.get("idle_minutes", 1440)),
                daily_reset_hour=platform_cfg.get("daily_reset_hour", session_cfg.get("daily_reset_hour", 4)),
            )

    return ResetPolicy(
        mode=session_cfg.get("default_reset", "none"),
        idle_minutes=session_cfg.get("idle_minutes", 1440),
        daily_reset_hour=session_cfg.get("daily_reset_hour", 4),
    )


@dataclass
class SessionEntry:
    """Entry in the session store — maps session_key to current session_id."""
    session_key: str
    session_id: str
    created_at: str        # ISO format
    updated_at: str        # ISO format
    origin: Optional[SessionSource] = None
    chat_type: str = "dm"
    was_auto_reset: bool = False
    auto_reset_reason: Optional[str] = None   # "idle" or "daily"

    def to_dict(self) -> dict:
        result = {
            "session_key": self.session_key,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chat_type": self.chat_type,
            "was_auto_reset": self.was_auto_reset,
            "auto_reset_reason": self.auto_reset_reason,
        }
        if self.origin:
            result["origin"] = self.origin.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SessionEntry":
        origin = None
        if "origin" in data and data["origin"]:
            origin = SessionSource.from_dict(data["origin"])
        return cls(
            session_key=data["session_key"],
            session_id=data["session_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            origin=origin,
            chat_type=data.get("chat_type", "dm"),
            was_auto_reset=data.get("was_auto_reset", False),
            auto_reset_reason=data.get("auto_reset_reason"),
        )


# Cap on the per-session persisted-row cache (see rewrite_messages). Bounded so
# a long-lived gateway/serve process doesn't pin unbounded per-session state.
_PERSIST_CACHE_LIMIT = 64


def _persist_row_key(msg: dict) -> int:
    """Hash of exactly the model-facing columns rewrite_messages persists for
    one message.

    Mirrors the INSERT column list — any field not in this tuple changing
    wouldn't be persisted anyway, any field in it changing must force a
    rewrite of that row (agent.py mutates the last tool-result dict in place
    for budget nudges, so identity checks are not enough). Meta payloads
    (``_reasoning``/``_usage``/``_attachments``/``_approval``) are excluded:
    they live in message_meta, written once at row creation, never diffed.
    """
    content = msg.get("content")
    tc = msg.get("tool_calls")
    return hash((
        msg.get("role"),
        # str/None bind to sqlite directly; anything else would fail the INSERT
        # — repr keeps the key hashable so that failure still surfaces there.
        content if isinstance(content, (str, bytes, type(None))) else repr(content),
        json.dumps(tc, ensure_ascii=False) if tc else None,
        msg.get("tool_call_id"),
    ))


def _strip_approval(content: str) -> tuple[str, dict | None]:
    """从 tool 结果 JSON 中剥离审批痕迹，返回 (净 content, 决议 record)。

    两条来源统一成 record：dispatch 的 ``approval_record``（完整判决）与
    legacy ``approval`` 摘要字符串（早期拒绝路径，补 decision=rejected）。
    无审批痕迹时原样返回 (content, None)。必须在 INSERT 之前调用——先洗
    再插一次写对；INSERT 触发器已把 content 写进 FTS，落库后再 UPDATE 洗净
    会让 FTS 留脏值且行数对账发现不了（索引与数据不一致）。
    """
    if "approval_record" not in content and '"approval"' not in content:
        return content, None
    try:
        data = json.loads(content)
    except ValueError:
        return content, None
    if not isinstance(data, dict):
        return content, None
    rec = data.get("approval_record")
    if not isinstance(rec, dict):
        summary = data.get("approval")
        rec = {"decision": "rejected", "summary": summary} \
            if isinstance(summary, str) else None
    data.pop("approval_record", None)
    data.pop("approval", None)
    return json.dumps(data, ensure_ascii=False), rec


class SessionDB:
    """SQLite session store — metadata + message transcripts."""

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._db_lock = threading.RLock()
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._execute("PRAGMA journal_mode=WAL")
        # NORMAL is the recommended pairing with WAL (only loses the last
        # checkpoint on power loss, far cheaper fsync than FULL); busy_timeout
        # waits out a sibling process's write lock (desktop serve + a CLI
        # sharing one sessions.db) instead of raising SQLITE_BUSY at once.
        self._execute("PRAGMA synchronous=NORMAL")
        self._execute("PRAGMA busy_timeout=5000")
        # Both CREATEs carry the full current schema — the ALTER TABLE
        # migrations that used to run here were folded into them. A db from
        # before the fold works only if some migration-carrying version
        # opened it at least once (all long-running instances have).
        self._execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                platform TEXT,
                chat_id TEXT,
                user_id TEXT,
                chat_type TEXT DEFAULT 'dm',
                title TEXT,
                origin TEXT,
                was_auto_reset INTEGER DEFAULT 0,
                auto_reset_reason TEXT,
                model TEXT,             -- per-session model override (/model switching)
                created_at TEXT,
                updated_at TEXT
            )
        """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,    -- uuid4 hex (stable anchor for message_meta)
                session_id TEXT NOT NULL,  -- round tag: which context generation wrote
                                           -- this row (NOT a FK — past rounds have no
                                           -- sessions row; agent context loads by it)
                role TEXT NOT NULL,     -- user | assistant | tool (model-facing only —
                                        -- system is rebuilt per turn, never persisted)
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                ord INTEGER NOT NULL,   -- row order WITHIN a conversation (monotonic
                                        -- across reset rounds); uuid ids are unordered
                                        -- and created_at ties within one rewrite, so
                                        -- this is the only sort anchor
                created_at TEXT,
                session_key TEXT        -- owning conversation (the real relationship;
                                        -- transcript/delete/truncate/search scope by it)
            )
        """)
        # Application-side annotations, 1:1 with a message row (the split line:
        # a field belongs here iff the model's context loses nothing without it).
        self._execute("""
            CREATE TABLE IF NOT EXISTS message_meta (
                message_id TEXT PRIMARY KEY,  -- FK → messages.id (uuid, rewrite-stable)
                reasoning TEXT,         -- model thinking snapshot (assistant), display-only
                usage TEXT,             -- per-turn token usage (JSON), turn's final assistant row
                attachments TEXT,       -- user-message attachment metadata (JSON array);
                                        -- `content` stays the user's literal input — the
                                        -- attachment hint block is API-boundary-injected
                                        -- (agent._prepare_api_messages), never persisted
                approval TEXT,          -- approval verdict JSON (tool rows that passed the
                                        -- gate); stripped from content at persist time
                FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
            )
        """)
        self._execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id)
        """)
        self._execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_order
            ON messages(session_key, ord)
        """)
        self._execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conv
            ON messages(session_key)
        """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS reset_marks (
                session_id TEXT PRIMARY KEY,   -- the round a reset CREATED
                session_key TEXT NOT NULL,
                reason TEXT,                   -- 'manual' | 'idle' | 'daily'
                created_at TEXT
            )
        """)
        self._execute("""
            CREATE INDEX IF NOT EXISTS idx_reset_marks_key
            ON reset_marks(session_key)
        """)

        self._init_fts()

        self._commit()

        # In-memory entry cache (mirrors sessions table for fast lookups)
        self._entries: dict[str, SessionEntry] = {}
        # session_id → per-row _persist_row_key list of what rewrite_messages
        # last wrote (see rewrite_messages). Invalidated by every non-rewrite
        # row mutation (delete/truncate).
        self._persisted: dict[str, list[int]] = {}
        self._load_entries()

    def _execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        """Thread-safe execute — serializes all DB ops with a lock.
        Required because delegate_task spawns parallel subagents that share
        the same SessionDB/connection. Without this, concurrent execute()
        on the same sqlite3.Connection raises InterfaceError."""
        with self._db_lock:
            return self._conn.execute(sql, params)

    def _commit(self) -> None:
        with self._db_lock:
            self._conn.commit()

    def _init_fts(self) -> None:
        """Create FTS5 virtual table and triggers for auto-sync.

        Content-table flavor (message_id UNINDEXED + content column), NOT the
        external-content mode the pre-uuid schema used: external content keys
        the index by rowid, and a TEXT uuid primary key is not a rowid alias —
        inserting a uuid into messages_fts(rowid, ...) fails with a datatype
        mismatch. Triggers copy the content in/out explicitly instead.
        """
        try:
            self._execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    message_id UNINDEXED,
                    content
                )
            """)
        except sqlite3.OperationalError:
            # FTS5 not available (rare), fall back to LIKE
            logger.warning("FTS5 not available, full-text search will use LIKE fallback")
            return

        # Triggers to keep FTS in sync with messages table
        try:
            self._execute("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages
                BEGIN
                    INSERT INTO messages_fts(message_id, content)
                    VALUES (new.id, new.content);
                END
            """)
            self._execute("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages
                BEGIN
                    DELETE FROM messages_fts WHERE message_id = old.id;
                END
            """)
        except sqlite3.OperationalError:
            pass

        # Rebuild only when the index is out of sync with the table: a full
        # re-tokenize of every message (~7s at 23K rows) would run on every
        # process start otherwise, while the count check costs ~0.1s. The
        # insert/delete triggers keep the counts honest through normal
        # operation (they are transactional with the rows), so a mismatch
        # means drift that only a rebuild fixes.
        try:
            msg_n = self._execute("SELECT count(*) FROM messages").fetchone()[0]
            fts_n = self._execute("SELECT count(*) FROM messages_fts").fetchone()[0]
            if msg_n != fts_n:
                self._execute("DELETE FROM messages_fts")
                self._execute(
                    "INSERT INTO messages_fts(message_id, content) "
                    "SELECT id, content FROM messages")
        except Exception:
            # a broken FTS index silently disables session search
            logger.warning("FTS rebuild failed — session search degraded",
                           exc_info=True)

    def _load_entries(self) -> None:
        """Load all session entries from SQLite into memory cache."""
        rows = self._execute(
            "SELECT session_key, session_id, platform, chat_id, user_id, "
            "chat_type, origin, was_auto_reset, auto_reset_reason, "
            "created_at, updated_at FROM sessions"
        ).fetchall()
        for row in rows:
            key, sid, platform, chat_id, user_id, chat_type, origin_json, \
                was_reset, reset_reason, created, updated = row
            origin = None
            if origin_json:
                try:
                    origin = SessionSource.from_dict(json.loads(origin_json))
                except (json.JSONDecodeError, KeyError):
                    origin = SessionSource(platform=platform or "", chat_id=chat_id or "",
                                           user_id=user_id, chat_type=chat_type or "dm")
            self._entries[key] = SessionEntry(
                session_key=key,
                session_id=sid,
                created_at=created or "",
                updated_at=updated or "",
                origin=origin,
                chat_type=chat_type or "dm",
                was_auto_reset=bool(was_reset),
                auto_reset_reason=reset_reason,
            )

    def _save_entry(self, entry: SessionEntry) -> None:
        """Persist a SessionEntry to SQLite.

        On conflict (reset creating a new round under the same key) only the
        round-scoped columns change — title/model belong to the conversation,
        not the round, and INSERT OR REPLACE here used to wipe them on every
        reset.
        """
        origin_json = json.dumps(entry.origin.to_dict(), ensure_ascii=False) if entry.origin else None
        self._execute(
            "INSERT INTO sessions "
            "(session_key, session_id, platform, chat_id, user_id, chat_type, "
            "origin, was_auto_reset, auto_reset_reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_key) DO UPDATE SET "
            "session_id=excluded.session_id, updated_at=excluded.updated_at, "
            "was_auto_reset=excluded.was_auto_reset, "
            "auto_reset_reason=excluded.auto_reset_reason",
            (
                entry.session_key, entry.session_id,
                entry.origin.platform if entry.origin else None,
                entry.origin.chat_id if entry.origin else None,
                entry.origin.user_id if entry.origin else None,
                entry.chat_type,
                origin_json,
                int(entry.was_auto_reset),
                entry.auto_reset_reason,
                entry.created_at, entry.updated_at,
            ),
        )
        self._commit()

    def _key_for_sid(self, session_id: str) -> Optional[str]:
        """Reverse-lookup the owning session_key of a live session_id (message
        writes stamp the key so the transcript survives round switches)."""
        for key, entry in self._entries.items():
            if entry.session_id == session_id:
                return key
        return None

    def build_key(self, source: SessionSource) -> str:
        """Build a deterministic session key from a SessionSource."""
        session_cfg = self._config.get("session", {}) or {}
        return build_session_key(
            source,
            group_sessions_per_user=session_cfg.get("group_sessions_per_user", True),
            thread_sessions_per_user=session_cfg.get("thread_sessions_per_user", False),
        )

    def get_or_create_session(self, source: SessionSource) -> str:
        """Return session_id for source, creating a new one if expired or missing.

        The session_key is generated deterministically from source via build_session_key().
        """
        session_key = self.build_key(source)
        now = _now()
        now_dt = _now_dt()

        entry = self._entries.get(session_key)
        if entry:
            policy = load_reset_policy(
                self._config,
                platform=source.platform,
                chat_type=source.chat_type,
            )
            reset_reason = policy.should_reset(
                datetime.fromisoformat(entry.updated_at) if entry.updated_at else now_dt,
                now_dt,
            )
            if reset_reason:
                return self._create_session(
                    session_key, source,
                    was_auto_reset=True, auto_reset_reason=reset_reason,
                )

            entry.updated_at = now
            self._execute(
                "UPDATE sessions SET updated_at = ? WHERE session_key = ?",
                (now, session_key),
            )
            self._commit()
            return entry.session_id

        return self._create_session(session_key, source)

    def _create_session(
        self,
        session_key: str,
        source: SessionSource,
        was_auto_reset: bool = False,
        auto_reset_reason: Optional[str] = None,
    ) -> str:
        session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = _now()
        prior_round = self._entries.get(session_key)

        entry = SessionEntry(
            session_key=session_key,
            session_id=session_id,
            created_at=now,
            updated_at=now,
            origin=source,
            chat_type=source.chat_type,
            was_auto_reset=was_auto_reset,
            auto_reset_reason=auto_reset_reason,
        )
        self._entries[session_key] = entry
        self._save_entry(entry)
        if prior_round is not None:
            # Round switch (auto or manual reset): persist why, so the
            # transcript can label this boundary.
            self._execute(
                "INSERT OR REPLACE INTO reset_marks "
                "(session_id, session_key, reason, created_at) VALUES (?, ?, ?, ?)",
                (session_id, session_key, auto_reset_reason or "manual", now),
            )
            self._commit()
        return session_id

    def reset_marks(self, session_key: str) -> dict[str, str]:
        """{session_id: reason} for every reset that created a round of this
        conversation — the per-boundary labels for transcript dividers."""
        rows = self._execute(
            "SELECT session_id, reason FROM reset_marks WHERE session_key = ?",
            (session_key,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def pending_auto_reset(self, source: SessionSource) -> Optional[str]:
        """The reset reason the NEXT get_or_create_session would apply, or
        None. Lets a caller flag an incoming round switch just before it
        happens (serve turn_start) using the same policy check instead of a
        copy of it."""
        entry = self._entries.get(self.build_key(source))
        if not entry:
            return None
        policy = load_reset_policy(
            self._config, platform=source.platform, chat_type=source.chat_type)
        updated = (datetime.fromisoformat(entry.updated_at)
                   if entry.updated_at else _now_dt())
        return policy.should_reset(updated, _now_dt())

    def reset_session(self, session_key: str) -> Optional[str]:
        """Force-create a new session for this key."""
        entry = self._entries.get(session_key)
        if not entry or not entry.origin:
            return None
        return self._create_session(session_key, entry.origin)

    def delete_session(self, session_key: str) -> bool:
        """Delete a conversation: the ``sessions`` row and the messages of ALL
        its rounds (keyed rows across resets). The FTS triggers on ``messages``
        keep the full-text index in sync on DELETE; message_meta rows are
        swept explicitly (foreign_keys is off, CASCADE would not fire).
        """
        entry = self._entries.get(session_key)
        if not entry:
            return False
        session_id = entry.session_id
        dead = self._execute(
            "SELECT id FROM messages WHERE session_key = ? OR session_id = ?",
            (session_key, session_id)).fetchall()
        self._execute(
            "DELETE FROM messages WHERE session_key = ? OR session_id = ?",
            (session_key, session_id))
        if dead:
            ph = ",".join("?" * len(dead))
            self._execute(
                f"DELETE FROM message_meta WHERE message_id IN ({ph})",
                [d[0] for d in dead])
        self._execute("DELETE FROM reset_marks WHERE session_key = ?", (session_key,))
        self._execute("DELETE FROM sessions WHERE session_key = ?", (session_key,))
        self._commit()
        self._entries.pop(session_key, None)
        self._persisted.pop(session_id, None)
        return True

    def get_entry(self, session_key: str) -> Optional[SessionEntry]:
        """Return the SessionEntry for a key, or None."""
        return self._entries.get(session_key)

    def get_session_title(self, session_id: str) -> Optional[str]:
        row = self._execute(
            "SELECT title FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def set_session_title(self, session_id: str, title: str) -> None:
        self._execute(
            "UPDATE sessions SET title = ? WHERE session_id = ?",
            (title, session_id),
        )
        self._commit()

    def get_session_model(self, session_key: str) -> Optional[str]:
        """Return the per-session model override (keyed by session_key), or None."""
        row = self._execute(
            "SELECT model FROM sessions WHERE session_key = ?",
            (session_key,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def set_session_model(self, session_key: str, model: str) -> None:
        """Persist a per-session model override so /model switching survives
        gateway per-message agent recreation."""
        self._execute(
            "UPDATE sessions SET model = ? WHERE session_key = ?",
            (model, session_key),
        )
        self._commit()

    def append_message(self, session_id: str, role: str,
                       content: Optional[str] = None,
                       tool_calls: Optional[str] = None,
                       tool_call_id: Optional[str] = None) -> None:
        if role == "system":  # rebuilt per turn, never persisted (rewrite_messages)
            return
        now = _now()
        self._execute(
            "INSERT INTO messages (id, session_id, role, content, tool_calls, tool_call_id, ord, created_at, session_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, session_id, role, content, tool_calls, tool_call_id,
             self._next_ord(session_id), now, self._key_for_sid(session_id)),
        )
        self._execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        self._commit()

    def _next_ord(self, session_id: str) -> int:
        """The next in-conversation row order value (max + 1; 0 when empty).
        Keyed by conversation, not round — reset switches session_id but the
        transcript order must keep increasing."""
        key = self._key_for_sid(session_id)
        if key is None:
            return 0
        row = self._execute(
            "SELECT max(ord) FROM messages WHERE session_key = ?",
            (key,),
        ).fetchone()
        return 0 if row is None or row[0] is None else row[0] + 1

    def truncate_messages_from(self, session_key: str, session_id: str,
                               from_id: str) -> int:
        """Delete the message row ``from_id`` (uuid) and everything after it —
        conversation-wide ord order, across all reset rounds (the target user
        row may sit in an earlier round than the current one). Meta rows go
        with them (foreign_keys is off, CASCADE inert). Returns the number of
        deleted rows; 0 (with a warning logged) when the target id doesn't
        exist."""
        dead = self._execute(
            "SELECT id FROM messages WHERE id IN ("
            "SELECT id FROM messages WHERE session_key = ? OR session_id = ?) "
            "AND ord >= (SELECT ord FROM messages WHERE id = ?)",
            (session_key, session_id, from_id),
        ).fetchall()
        if not dead:
            # ord >= NULL → NULL → 静默删 0 行；打日志防"成功但没删"的
            # 客户端（旧 int id / 已删 id / 跨会话 id）排查盲区
            logger.warning("truncate_messages_from: target id %r not found "
                           "(session_key=%s) — deleted nothing", from_id, session_key)
            return 0
        dead_ids = [d[0] for d in dead]
        ph = ",".join("?" * len(dead_ids))
        cur = self._execute(
            f"DELETE FROM messages WHERE id IN ({ph})", dead_ids)
        self._execute(
            f"DELETE FROM message_meta WHERE message_id IN ({ph})", dead_ids)
        self._commit()
        # Row positions no longer match the rewrite cache — force its next
        # call onto the full-rewrite path.
        self._persisted.pop(session_id, None)
        return cur.rowcount

    def load_messages(self, session_id: str) -> list[dict]:
        """Load all messages for a session in order.

        Meta annotations (``reasoning``/``usage``/``attachments``) come back as
        underscore-prefixed internal keys so the next turn's rewrite_messages
        can re-persist them — without the reload, every _persist_messages call
        (DELETE + re-INSERT) would wipe them for completed turns.
        ``_prepare_api_messages`` strips underscore keys (after re-injecting the
        attachment hint block), so the API request shape is unaffected.
        ``approval`` is NOT returned — it is human-facing audit data; the model
        never sees it (content stays pure tool output).
        """
        rows = self._execute(
            "SELECT m.role, m.content, m.tool_calls, m.tool_call_id, "
            "mt.reasoning, mt.usage, mt.attachments FROM messages m "
            "LEFT JOIN message_meta mt ON mt.message_id = m.id "
            "WHERE m.session_id = ? ORDER BY m.ord",
            (session_id,),
        ).fetchall()
        messages = []
        for role, content, tool_calls_json, tool_call_id, reasoning, usage_json, att_json in rows:
            msg = {"role": role}
            if content is not None:
                msg["content"] = content
            if tool_calls_json:
                try:
                    msg["tool_calls"] = json.loads(tool_calls_json)
                except json.JSONDecodeError:
                    pass
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id
            if reasoning:
                msg["_reasoning"] = reasoning
            if usage_json:
                try:
                    msg["_usage"] = json.loads(usage_json)
                except json.JSONDecodeError:
                    pass
            if att_json:
                try:
                    msg["_attachments"] = json.loads(att_json)
                except json.JSONDecodeError:
                    pass
            if role == "assistant" and not content and not tool_calls_json:
                continue
            messages.append(msg)
        return messages

    @staticmethod
    def _rows_to_display(rows: Sequence) -> list[dict]:
        out = []
        for (mid, role, content, tool_calls_json, tool_call_id, reasoning,
             usage_json, created_at, sid, att_json, approval_json) in rows:
            msg = {"id": mid, "role": role, "session_id": sid}
            if created_at:
                msg["created_at"] = created_at
            if content is not None:
                msg["content"] = content
            if tool_calls_json:
                try:
                    msg["tool_calls"] = json.loads(tool_calls_json)
                except json.JSONDecodeError:
                    pass
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id
            if reasoning:
                msg["reasoning"] = reasoning
            if usage_json:
                try:
                    msg["usage"] = json.loads(usage_json)
                except json.JSONDecodeError:
                    pass
            if att_json:
                try:
                    msg["attachments"] = json.loads(att_json)
                except json.JSONDecodeError:
                    pass
            if approval_json:
                try:
                    msg["approval"] = json.loads(approval_json)
                except json.JSONDecodeError:
                    pass
            if role == "assistant" and not content and not tool_calls_json:
                continue
            out.append(msg)
        return out

    def load_messages_with_id(self, session_id: str) -> list[dict]:
        """Like ``load_messages``, but includes each row's stable uuid ``id``.
        Used only by display paths (serve history/trace) that need a stable
        per-message key to lazy-fetch tool-call detail.

        ``load_messages`` itself MUST stay field-compatible with the OpenAI
        message shape — ``agent.py`` rebuilds the model's conversation history
        from it, so it cannot gain an ``id`` field (that would leak into the
        API request). This sibling method exists so display concerns don't
        contaminate the model-facing shape.
        """
        rows = self._execute(
            "SELECT m.id, m.role, m.content, m.tool_calls, m.tool_call_id, "
            "mt.reasoning, mt.usage, m.created_at, m.session_id, mt.attachments, mt.approval "
            "FROM messages m LEFT JOIN message_meta mt ON mt.message_id = m.id "
            "WHERE m.session_id = ? ORDER BY m.ord",
            (session_id,),
        ).fetchall()
        return self._rows_to_display(rows)

    def load_transcript(self, session_key: str) -> list[dict]:
        """A conversation's display rows across ALL reset rounds, in row order
        (``ord``). The transcript view is per-conversation; the agent context
        stays per-round (load_messages on the current session_id)."""
        rows = self._execute(
            "SELECT m.id, m.role, m.content, m.tool_calls, m.tool_call_id, "
            "mt.reasoning, mt.usage, m.created_at, m.session_id, mt.attachments, mt.approval "
            "FROM messages m LEFT JOIN message_meta mt ON mt.message_id = m.id "
            "WHERE m.session_key = ? ORDER BY m.ord",
            (session_key,),
        ).fetchall()
        return self._rows_to_display(rows)

    def set_last_assistant_usage(self, session_id: str, usage: dict) -> None:
        """Write per-turn token usage to the turn's final assistant row's meta.

        ``_log_turn_usage`` runs after the turn's last assistant message is
        persisted, so "newest assistant row in this session" is exactly the
        turn the usage belongs to. A turn with zero model calls never writes.
        """
        row = self._execute(
            "SELECT id FROM messages WHERE session_id = ? AND role = 'assistant' "
            "ORDER BY ord DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        self._execute(
            "INSERT INTO message_meta (message_id, usage) VALUES (?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET usage = excluded.usage",
            (row[0], json.dumps(usage, ensure_ascii=False)),
        )
        self._commit()

    def rewrite_messages(self, session_id: str, messages: list[dict]) -> None:
        """Make the session's rows equal ``messages`` — incrementally.

        The agent loop calls this after EVERY iteration with the full list,
        where all previous rows are unchanged and only the tail grew. The old
        unconditional DELETE-all + re-INSERT made a turn cost O(turn²) SQLite
        writes (FTS triggers firing per row per iteration) — the long-turn
        stall under the DB lock. Instead, match the longest unchanged prefix
        against what we last wrote (per-row content hashes, NOT dict identity:
        the loop mutates the last tool-result dict in place for budget
        nudges), delete only rows past it, insert only the tail. A mid-list
        change (context compression replaces the list) naturally falls back
        to a full rewrite at the first divergent row.

        system rows are NOT persisted: the prompt is rebuilt every turn from
        code/config/skills and re-inserted at the API-boundary copy's head
        (agent._chat_turn). Meta annotations (``_reasoning``/``_usage``/
        ``_attachments``) go to message_meta, written once at row creation.
        Tool rows carrying an ``approval_record`` in their result JSON have it
        stripped here into message_meta.approval — content stays pure tool
        output (the model-facing rejection error TEXT remains in content; only
        the human-facing verdict moves to meta).
        """
        messages = [m for m in messages if m.get("role") != "system"]
        with self._db_lock:
            cached = self._persisted.get(session_id)
            prefix = 0
            if cached:
                for i in range(min(len(cached), len(messages))):
                    if _persist_row_key(messages[i]) == cached[i]:
                        prefix = i + 1
                    else:
                        break
            if cached is not None and prefix == len(cached) == len(messages):
                return  # nothing changed since the last write

            # Drop rows at/after the first divergent position (by ord — uuids
            # are unordered). The OFFSET subquery is empty (NULL) when
            # prefix == row count → deletes nothing; surplus cached rows (list
            # shrank) are covered because the delete is positioned, not
            # counted. Meta rows for deleted messages go with them (explicit —
            # foreign_keys is off, CASCADE would not fire).
            dead = self._execute(
                "SELECT id FROM messages WHERE session_id = ? AND ord >= ("
                "SELECT ord FROM messages WHERE session_id = ? "
                "ORDER BY ord LIMIT 1 OFFSET ?)",
                (session_id, session_id, prefix),
            ).fetchall()
            if dead:
                dead_ids = [d[0] for d in dead]
                ph = ",".join("?" * len(dead_ids))
                self._execute(f"DELETE FROM messages WHERE id IN ({ph})", dead_ids)
                self._execute(
                    f"DELETE FROM message_meta WHERE message_id IN ({ph})", dead_ids)
            conv_key = self._key_for_sid(session_id)
            # ord continues the conversation-wide sequence (across reset
            # rounds), not the in-list position — a new round's prefix starts
            # at 0 but its rows must sort after everything already stored.
            next_ord = self._next_ord(session_id)
            now = _now()
            for i, msg in enumerate(messages[prefix:]):
                role = msg.get("role", "unknown")
                content = msg.get("content")
                tool_calls = json.dumps(msg["tool_calls"], ensure_ascii=False) if msg.get("tool_calls") else None
                tool_call_id = msg.get("tool_call_id")
                reasoning = msg.get("_reasoning")  # internal-only key; never sent to the API
                usage = json.dumps(msg["_usage"], ensure_ascii=False) if msg.get("_usage") else None
                atts = json.dumps(msg["_attachments"], ensure_ascii=False) if msg.get("_attachments") is not None else None
                # 审批剥离在 INSERT 之前：先洗再插一次写对（FTS 触发器随后
                # 写入的就是净 content，无 drift）。
                approval = None
                if role == "tool" and isinstance(content, str):
                    content, rec = _strip_approval(content)
                    if rec is not None:
                        approval = json.dumps(rec, ensure_ascii=False)
                mid = uuid.uuid4().hex
                self._execute(
                    "INSERT INTO messages (id, session_id, role, content, tool_calls, tool_call_id, ord, created_at, session_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (mid, session_id, role, content, tool_calls, tool_call_id,
                     next_ord + i, now, conv_key),
                )
                # Meta annotations ride their row's creation (rare: most rows
                # have none).
                if reasoning is not None or usage is not None or atts is not None \
                        or approval is not None:
                    self._execute(
                        "INSERT INTO message_meta (message_id, reasoning, usage, attachments, approval) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mid, reasoning, usage, atts, approval),
                    )
            self._commit()

            if cached is None and len(self._persisted) >= _PERSIST_CACHE_LIMIT:
                self._persisted.pop(next(iter(self._persisted)), None)
            # Prefix keys were already compared equal — reuse instead of
            # re-hashing the whole transcript.
            self._persisted[session_id] = (
                (cached[:prefix] if cached else [])
                + [_persist_row_key(m) for m in messages[prefix:]]
            )

    def list_sessions(self, limit: int = 50, platform: Optional[str] = None,
                      user_id: Optional[str] = None,
                      include_internal: bool = False) -> list[dict]:
        """List sessions, most-recently-updated first.

        Returns dicts: session_key, session_id, platform, chat_id, title,
        updated_at, msg_count. Optional *platform* and *user_id* filters
        (None = no filter; user_id=None avoids the SQL NULL-trap on purpose).

        By default hides internal agent-run sessions (cron jobs, delegate
        subagents) — they're transcripts, not user conversations to browse or
        resume. Pass ``include_internal=True`` to include them.
        """
        clauses: list[str] = []
        params: list[object] = []
        if platform:
            clauses.append("s.platform = ?")
            params.append(platform)
        if user_id:
            clauses.append("s.user_id = ?")
            params.append(user_id)
        if not include_internal:
            clauses.append("s.platform NOT IN ('cron', 'delegate')")
        where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
        params.append(limit)
        sql = (
            "SELECT s.session_key, s.session_id, s.platform, s.chat_id, s.title, "
            "s.updated_at, "
            "(SELECT COUNT(*) FROM messages m "
            " WHERE m.session_key = s.session_key OR m.session_id = s.session_id) AS msg_count "
            "FROM sessions s " + where + "ORDER BY s.updated_at DESC LIMIT ?"
        )
        rows = self._execute(sql, tuple(params)).fetchall()
        cols = ["session_key", "session_id", "platform", "chat_id",
                "title", "updated_at", "msg_count"]
        return [dict(zip(cols, r)) for r in rows]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Full-text search across all session messages.

        Uses FTS5 for fast, ranked search when available.
        Falls back to LIKE for databases created before FTS5 was added.
        """
        # Try FTS5 first. The sessions join keys on the conversation
        # (session_key) so pre-reset rounds are searchable too; the
        # session_id disjunct covers rows written before that column
        # existed (NULL key).
        try:
            rows = self._execute(
                "SELECT m.session_id, m.role, snippet(messages_fts, 1, '>>>', '<<<', '...', 20) AS snippet, "
                "s.platform "
                "FROM messages_fts fts "
                "JOIN messages m ON m.id = fts.message_id "
                "JOIN sessions s ON m.session_key = s.session_key "
                "                OR m.session_id = s.session_id "
                "WHERE messages_fts MATCH ? "
                "ORDER BY fts.rank "
                "LIMIT ?",
                (query, limit),
            ).fetchall()
            return [
                {"session_id": r[0], "role": r[1], "content": r[2][:500] if r[2] else "", "platform": r[3]}
                for r in rows
            ]
        except (sqlite3.OperationalError, Exception) as e:
            logger.debug("FTS5 search failed, falling back to LIKE: %s", e)

        # LIKE fallback
        try:
            rows = self._execute(
                "SELECT m.session_id, m.role, m.content, s.platform "
                "FROM messages m JOIN sessions s ON m.session_key = s.session_key "
                "                OR m.session_id = s.session_id "
                "WHERE m.content LIKE ? ORDER BY m.created_at DESC, m.ord DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [
                {"session_id": r[0], "role": r[1], "content": r[2][:200], "platform": r[3]}
                for r in rows
            ]
        except Exception as e:
            logger.debug("Search failed: %s", e)
            return []
