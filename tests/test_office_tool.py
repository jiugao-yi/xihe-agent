"""office tool (tools/office_tool.py): xlsx/docx write→read roundtrips with
the real openpyxl/python-docx, plus validation errors."""

import json

import pytest

from tools import office_tool

openpyxl = pytest.importorskip("openpyxl", reason="excel actions need openpyxl")
pytest.importorskip("docx", reason="word actions need python-docx")


def _office(**args):
    return json.loads(office_tool._office(args))


def test_excel_write_read_roundtrip(tmp_path):
    w = _office(action="excel_write", path=str(tmp_path / "r.xlsx"),
                sheets=[{"name": "data", "header": True,
                         "rows": [["服务", "QPS"], ["api", 1200], ["web", 800]]}])
    assert w.get("success") is True and w["sheets"] == 1
    r = _office(action="excel_read", path=str(tmp_path / "r.xlsx"))
    assert r["rows"] == [["服务", "QPS"], ["api", 1200], ["web", 800]]
    assert r["total_rows"] == 3 and r["truncated"] is False


def test_excel_read_sheet_select_and_cap(tmp_path):
    _office(action="excel_write", path=str(tmp_path / "m.xlsx"),
            sheets=[{"name": "a", "rows": [["x"]]},
                    {"name": "b", "rows": [[i] for i in range(10)]}])
    r = _office(action="excel_read", path=str(tmp_path / "m.xlsx"), sheet="b", max_rows=4)
    assert r["total_rows"] == 10 and r["returned_rows"] == 4 and r["truncated"] is True


def test_word_write_read_roundtrip(tmp_path):
    w = _office(action="word_write", path=str(tmp_path / "d.docx"), blocks=[
        {"type": "heading", "level": 1, "text": "周报"},
        {"type": "paragraph", "text": "本周完成三件事。"},
        {"type": "bullet", "text": "终端面板收敛"},
        {"type": "table", "rows": [["项", "值"], ["通过", 568]]},
    ])
    assert w.get("success") is True
    r = _office(action="word_read", path=str(tmp_path / "d.docx"))
    assert "# 周报" in r["text"]
    assert "- 终端面板收敛" in r["text"]
    assert "| 项 | 值 |" in r["text"]


def test_word_read_caps_output(tmp_path):
    _office(action="word_write", path=str(tmp_path / "big.docx"),
            blocks=[{"type": "paragraph", "text": "x" * 5000}] * 10)
    r = _office(action="word_read", path=str(tmp_path / "big.docx"), max_chars=600)
    assert r["truncated"] is True and len(r["text"]) <= 600


def test_validation_errors(tmp_path):
    r = _office(action="hexbin")
    assert "action must be one of" in r["error"]
    r = _office(action="excel_write", sheets=[{"rows": [["x"]]}])
    assert "path is required" in r["error"]
    r = _office(action="excel_write", path=str(tmp_path / "a.xlsx"), sheets=[])
    assert "non-empty" in r["error"]
    r = _office(action="excel_write", path=str(tmp_path / "a.xlsx"),
                sheets=[{"rows": "not-a-list"}])
    assert "array" in r["error"]
    r = _office(action="excel_read", path=str(tmp_path / "nope.xlsx"))
    assert "not found" in r["error"].lower()
    r = _office(action="word_write", path=str(tmp_path / "b.docx"), blocks=[])
    assert "non-empty" in r["error"]
    r = _office(action="word_write", path=str(tmp_path / "b.docx"),
                blocks=[{"type": "table", "rows": []}])
    assert "requires rows" in r["error"]
