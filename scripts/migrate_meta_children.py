# -*- coding: utf-8 -*-
"""一次性补列：message_meta 加 children + created_at（append-only 架构批次）。

幂等：列已存在则跳过。历史 meta 行的 children 为 NULL（无委派指针——旧数据
本就没有锚），created_at 补该行对应消息行的 created_at（meta 与主行同批创建）。
"""
import sqlite3
import sys

DB = r"C:\Users\zzmao\.xihe-agent\sessions\sessions.db"


def main() -> int:
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute("PRAGMA table_info(message_meta)")]
    changed = False
    if "children" not in cols:
        c.execute("ALTER TABLE message_meta ADD COLUMN children TEXT")
        print("added column: children")
        changed = True
    if "created_at" not in cols:
        c.execute("ALTER TABLE message_meta ADD COLUMN created_at TEXT")
        # backfill from the owning message row (same write batch)
        c.execute(
            "UPDATE message_meta SET created_at = ("
            "  SELECT m.created_at FROM messages m"
            "  WHERE m.id = message_meta.message_id)"
            " WHERE created_at IS NULL")
        print("added column: created_at (+backfill from messages)")
        changed = True
    if changed:
        c.commit()
        print("committed")
    else:
        print("schema already up to date")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
