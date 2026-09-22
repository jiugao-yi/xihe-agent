"""image_render (tools/image_render_tool.py): the one deterministic
content/data → PNG tool. source=data renders real (tiny) matplotlib charts;
source=mermaid/html/markdown snapshot through a real headless chromium
(Playwright + system Chrome/Edge are the deployment's browser baseline)."""

import json

import pytest

from tools import image_render_tool


def _render(tmp_path, **overrides):
    args = {"output": str(tmp_path / "out.png")}
    args.update(overrides)
    return json.loads(image_render_tool._image_render(args))


# ---- source=data (matplotlib) ----

matplotlib = pytest.importorskip("matplotlib", reason="data charts need matplotlib")


def _data_args(tmp_path, **overrides):
    args = {
        "source": "data", "chart_type": "bar",
        "labels": ["一月", "二月", "三月"],
        "series": [{"name": "收入", "data": [3, 5, 4]}],
        "title": "季度收入（示例）",
        "output": str(tmp_path / "chart.png"),
    }
    args.update(overrides)
    return args


def test_data_bar_renders_png(tmp_path):
    r = json.loads(image_render_tool._image_render(_data_args(tmp_path)))
    assert r.get("path", "").endswith(".png")
    assert (tmp_path / "chart.png").stat().st_size > 5_000
    assert r["source"] == "data"


def test_data_gateway_hint_routes_to_send_image(tmp_path):
    r = json.loads(image_render_tool._image_render(
        _data_args(tmp_path, output=str(tmp_path / "c2.png")),
        context={"platform": "wecom"}))
    assert "send_image" in r["hint"]


def test_data_line_multi_series(tmp_path):
    r = json.loads(image_render_tool._image_render(_data_args(
        tmp_path, chart_type="line",
        series=[{"name": "A", "data": [1, 4, 2]}, {"name": "B", "data": [3, 2, 5]}])))
    assert r.get("chart_type") == "line"


def test_data_pie_renders(tmp_path):
    r = json.loads(image_render_tool._image_render(_data_args(
        tmp_path, chart_type="pie", series=[{"name": "份额", "data": [45, 30, 25]}])))
    assert "path" in r


def test_data_validation_errors(tmp_path):
    r = _render(tmp_path, source="data", labels=["a"], series=[{"data": [1]}])
    assert "chart_type" in r["error"]
    r = _render(tmp_path, source="data", chart_type="pie", labels=["a", "b"],
                series=[{"data": [1]}, {"data": [2]}])
    assert "exactly one series" in r["error"]
    r = _render(tmp_path, source="data", chart_type="bar", labels=["a", "b"],
                series=[{"data": [1]}])
    assert "must match" in r["error"]
    r = _render(tmp_path, source="data", chart_type="bar", labels=["a"],
                series=[{"data": ["x"]}])
    assert "numeric" in r["error"]


# ---- source=mermaid/html/markdown (headless browser) ----

pytest.importorskip("playwright", reason="content snapshots need playwright")
pytest.importorskip("markdown", reason="markdown source needs the markdown lib")


def test_html_kind_renders_png(tmp_path):
    r = _render(tmp_path, source="html",
                content="<h2>部署结果</h2><p>成功 <b>3</b> 个，失败 <b>1</b> 个</p>")
    assert r.get("path", "").endswith(".png")
    assert (tmp_path / "out.png").stat().st_size > 2_000


def test_markdown_kind_renders_table(tmp_path):
    r = _render(tmp_path, source="markdown",
                content="| 服务 | 状态 |\n|---|---|\n| api | ✅ |\n| web | ❌ |")
    assert "path" in r


def test_mermaid_kind_renders_diagram(tmp_path):
    r = _render(tmp_path, source="mermaid",
                content='flowchart LR\n  A["客户端"] --> B["网关"]\n  B --> C["服务"]')
    assert "path" in r
    assert (tmp_path / "out.png").stat().st_size > 5_000  # a real diagram, not blank


def test_mermaid_syntax_error_reports(tmp_path):
    r = _render(tmp_path, source="mermaid", content="flowchart LR\n  A -->-> bad [")
    assert "error" in r


def test_content_validation_errors(tmp_path):
    r = _render(tmp_path, source="html")
    assert "exactly one of content or file" in r["error"]
    r = _render(tmp_path, source="pdf", content="x")
    assert "source must be one of" in r["error"]
    r = _render(tmp_path, source="markdown", file="no-such-file.md")
    assert "not found" in r["error"].lower()
