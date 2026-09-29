# -*- coding: utf-8 -*-
"""todos.json / memories.json / external-agent-pids 的跨进程读-改-写安全。

serve 与 gateway 共享一个 AGENT_HOME 并行运行时，todo/memory 的
load-modify-save 与 pids 的 add/remove 都会互相覆盖。修复模式与
test_cron_jobs_file_lock.py 相同：进程内 threading.Lock + 跨进程
CrossProcessLock 包住整个事务；文件写走 tmp + os.replace 原子替换，
读路径无锁。
"""
import json
import threading

import pytest

import tools.todo_tool as tt
import tools.memory_tool as mt
import core.services.external_agent as ea
from core.support.process_lock import CrossProcessLock


@pytest.fixture
def todo_home(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "_TODO_FILE", tmp_path / "todos.json")
    monkeypatch.setattr(tt, "_TODO_CROSS_LOCK", tmp_path / "todos.lock")
    return tmp_path


@pytest.fixture
def mem_home(tmp_path, monkeypatch):
    monkeypatch.setattr(mt, "_MEMORIES_FILE", tmp_path / "memories.json")
    monkeypatch.setattr(mt, "_MEMORY_CROSS_LOCK", tmp_path / "memories.lock")
    return tmp_path


@pytest.fixture
def pids_home(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "_pids_path", lambda: tmp_path / "external-agent-pids.json")
    monkeypatch.setattr(ea, "_pids_cross_lock", tmp_path / "external-agent-pids.lock")
    return tmp_path


class TestTodoCrossProcess:
    def test_interleaved_add_and_update_both_survive(self, todo_home):
        """模拟另一进程持锁的交错事务：A 进程 add、B 进程（独立锁实例）
        update——旧代码下 B 用 add 前的快照整文件覆盖会丢掉 A 的写入；
        事务下两个变更都必须落盘。"""
        r = tt._todo({"action": "add", "title": "first"})
        assert '"success": true' in r

        # "另一进程"持锁窗口内，主线程的事务自等到超时 → 受控 tool_error
        with CrossProcessLock(todo_home / "todos.lock"):
            r = tt._todo({"action": "add", "title": "during-lock"})
            assert '"error"' in r, r
        # 释放后重试成功
        assert '"success": true' in tt._todo({"action": "add", "title": "second"})

        # 交错事务：add 与 update 各自完整走 load-modify-save
        data = json.loads((todo_home / "todos.json").read_text(encoding="utf-8"))
        item_id = data["default"][0]["id"]
        r = tt._todo({"action": "update", "id": item_id, "status": "completed"})
        assert '"success": true' in r

        data = json.loads((todo_home / "todos.json").read_text(encoding="utf-8"))
        titles = {i["title"] for i in data["default"]}
        assert titles == {"first", "second"}
        assert data["default"][0]["status"] == "completed"

    def test_concurrent_adds_all_survive(self, todo_home):
        """N 线程并发 add（每线程独立 CrossProcessLock 语义）：最终文件
        必须包含全部条目，无覆盖丢失。"""
        def worker(w):
            for i in range(5):
                assert '"success": true' in tt._todo(
                    {"action": "add", "title": f"w{w}_{i}"})

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = json.loads((todo_home / "todos.json").read_text(encoding="utf-8"))
        assert len(data["default"]) == 30

    def test_save_is_atomic_no_partial_reads(self, todo_home):
        """写走 tmp + os.replace：读者永远见完整 JSON，不见半截文件。"""
        assert not (todo_home / "todos.json").exists() or \
            json.loads((todo_home / "todos.json").read_text(encoding="utf-8")) is not None
        tt._todo({"action": "add", "title": "x"})
        # 原子替换后不留 tmp 残骸
        assert not (todo_home / "todos.json.tmp").exists()

    def test_hydrate_goes_through_lock(self, todo_home):
        """hydrate_from_history（gateway 每消息触发）也被跨进程锁包住：
        持锁窗口内不写、不炸，静默降级。"""
        tt._todo({"action": "add", "title": "existing"})
        msgs = [{"role": "tool", "content": json.dumps(
            {"list": "default", "items": [{"id": "todo_1", "title": "h"}]})}]
        with CrossProcessLock(todo_home / "todos.lock"):
            tt.hydrate_from_history(msgs)  # 不抛即通过
        data = json.loads((todo_home / "todos.json").read_text(encoding="utf-8"))
        assert data["default"][0]["title"] == "existing"


class TestMemoryCrossProcess:
    def test_interleaved_saves_both_keys_survive(self, mem_home):
        """两个"进程"各自 save 不同 key：后写者不得用旧快照抹掉先写者。"""
        def save_a():
            with CrossProcessLock(mem_home / "memories.lock"):
                memories = mt._load_memories()
                memories["a"] = "va"
                mt._save_memories(memories)

        r = mt._memory_manage({"action": "save", "key": "b", "value": "vb"})
        assert '"success": true' in r
        save_a()

        data = json.loads((mem_home / "memories.json").read_text(encoding="utf-8"))
        assert data == {"a": "va", "b": "vb"}

    def test_save_contended_returns_tool_error(self, mem_home):
        with CrossProcessLock(mem_home / "memories.lock"):
            r = mt._memory_manage({"action": "save", "key": "k", "value": "v"})
            assert '"error"' in r
        assert '"success": true' in mt._memory_manage(
            {"action": "save", "key": "k", "value": "v"})

    def test_delete_contended_returns_tool_error(self, mem_home):
        mt._memory_manage({"action": "save", "key": "k", "value": "v"})
        with CrossProcessLock(mem_home / "memories.lock"):
            r = mt._memory_manage({"action": "delete", "key": "k"})
            assert '"error"' in r

    def test_read_path_needs_no_lock(self, mem_home):
        """读不进锁：持锁写期间 get/list/search 仍可用（原子写保证完整性）。"""
        mt._memory_manage({"action": "save", "key": "k", "value": "v"})
        with CrossProcessLock(mem_home / "memories.lock"):
            r = mt._memory_read({"action": "get", "key": "k"})
            assert '"v"' in r


class TestPidsCrossProcess:
    def test_add_remove_roundtrip(self, pids_home):
        ea._add_pid({"pid": 111, "engine": "claude"})
        ea._add_pid({"pid": 222, "engine": "codex"})
        assert {e["pid"] for e in ea._read_pids()} == {111, 222}
        ea._remove_pid(111)
        assert [e["pid"] for e in ea._read_pids()] == [222]

    def test_concurrent_adds_all_survive(self, pids_home):
        def worker(w):
            for i in range(5):
                ea._add_pid({"pid": w * 100 + i, "engine": "claude"})

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(ea._read_pids()) == 20
