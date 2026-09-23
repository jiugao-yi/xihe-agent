"""Image render tool — deterministic content/data → PNG, offline. Two input
forms, one capability: ``source=data`` renders charts via matplotlib;
``source=mermaid/html/markdown`` snapshots content through a throwaway
headless Playwright browser (system Chrome/Edge channel, same preference as
browser_tool but a SEPARATE instance — never touches the agent's interactive
browser)."""

import importlib.util
import logging
import threading
import time
from html import escape as _escape
from pathlib import Path

from tools import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_SOURCES = ("data", "mermaid", "html", "markdown")
_CHART_TYPES = ("bar", "hbar", "line", "pie", "area", "scatter")
# pyplot is not thread-safe — serialize renders across worker threads.
_plot_lock = threading.Lock()

# CJK-capable system fonts, first match wins (per-OS fallbacks included).
_FONTS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
          "PingFang SC", "WenQuanYi Micro Hei", "sans-serif"]

_MERMAID_JS = Path(__file__).parent / "assets" / "mermaid.min.js"

_PAGE_CSS = """<style>
  body { margin: 0; padding: 24px 28px; background: #ffffff;
         font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                      "Segoe UI", sans-serif; color: #1A1B1C;
         font-size: 14px; line-height: 1.65; }
  table { border-collapse: collapse; }
  th, td { border: 1px solid #d9d9d9; padding: 6px 14px; text-align: left; }
  th { background: #f4f5f7; }
  code { background: #f4f5f7; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
  pre { background: #f7f8fa; border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  blockquote { border-left: 3px solid #c9cdd4; margin: 8px 0; padding: 2px 14px; color: #6B7280; }
  img { max-width: 100%; }
</style>"""


def _check() -> bool:
    return (importlib.util.find_spec("matplotlib") is not None
            or importlib.util.find_spec("playwright") is not None)


def _output_dir(kw: dict) -> Path:
    # Runtime artifact, not workspace content — always under AGENT_HOME
    # (never the process cwd / a bound workspace).
    from core.config import AGENT_HOME
    d = Path(AGENT_HOME) / "charts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mode_hint(path: str, kw: dict) -> str:
    platform = (kw.get("context") or {}).get("platform") or ""
    if platform in ("wecom", "feishu", "telegram", "discord"):
        return f"Deliver it with send_image(image_path='{path}')"
    if platform == "serve":
        return ("The desktop displays this image under your reply automatically. "
                f"You MUST mention {path} in the reply text (plain text, no "
                "backticks needed) so it survives reloads — do NOT write "
                "markdown image syntax like ![](...)")
    return "Terminal cannot display images — also give the data as a markdown table"


def _resolve_output(args: dict, kw: dict, prefix: str) -> Path:
    raw = args.get("output")
    if raw:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = _output_dir(kw) / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return _output_dir(kw) / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.png"


def _nums(vals) -> list[float] | None:
    try:
        return [float(v) for v in vals]
    except (TypeError, ValueError):
        return None


# ---- source=data: matplotlib charts ----

def _render_data_chart(args: dict, kw: dict) -> str:
    chart_type = str(args.get("chart_type") or "").lower()
    if chart_type not in _CHART_TYPES:
        return tool_error(f"chart_type must be one of: {', '.join(_CHART_TYPES)}")

    labels = args.get("labels")
    series = args.get("series")
    if not isinstance(labels, list) or not labels:
        return tool_error("labels must be a non-empty array")
    if not isinstance(series, list) or not series:
        return tool_error("series must be a non-empty array")
    if chart_type == "pie" and len(series) != 1:
        return tool_error("pie takes exactly one series")

    clean = []
    for s in series:
        data = _nums((s or {}).get("data") or [])
        if data is None:
            return tool_error("series.data must be numeric")
        if len(data) != len(labels):
            return tool_error(f"series '{(s or {}).get('name', '')}' has {len(data)} values, labels has {len(labels)} — must match")
        clean.append((str((s or {}).get("name") or ""), data))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return tool_error("matplotlib is not installed — data charts unavailable in this environment")

    title = str(args.get("title") or "")
    x_label = str(args.get("x_label") or "")
    y_label = str(args.get("y_label") or "")
    rotate = max((len(str(l)) for l in labels), default=0) > 3 or len(labels) > 8
    out_path = _resolve_output(args, kw, "chart")

    with _plot_lock:
        plt.rcParams["font.sans-serif"] = _FONTS
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
        fig.patch.set_facecolor("white")

        try:
            if chart_type == "pie":
                _, _, autotexts = ax.pie(
                    clean[0][1], labels=labels, autopct="%1.1f%%",
                    startangle=90, counterclock=False,
                    colors=plt.cm.tab20.colors[:len(labels)])
                for t in autotexts:
                    t.set_color("white")
                    t.set_fontsize(9)
            else:
                n = max(1, len(clean))
                if chart_type == "bar":
                    width = 0.8 / n
                    for i, (name, data) in enumerate(clean):
                        pos = [j + i * width for j in range(len(labels))]
                        bars = ax.bar(pos, data, width=width, label=name or None)
                        ax.bar_label(bars, fmt=lambda v: f"{v:g}", fontsize=8, padding=2)
                    ax.set_xticks([j + 0.4 - width / 2 for j in range(len(labels))])
                    ax.set_xticklabels(labels)
                elif chart_type == "hbar":
                    height = 0.8 / n
                    for i, (name, data) in enumerate(clean):
                        pos = [j + i * height for j in range(len(labels))]
                        bars = ax.barh(pos, data, height=height, label=name or None)
                        ax.bar_label(bars, fmt=lambda v: f"{v:g}", fontsize=8, padding=2)
                    ax.set_yticks(range(len(labels)))
                    ax.set_yticklabels(labels)
                    ax.invert_yaxis()
                elif chart_type in ("line", "area"):
                    for name, data in clean:
                        if chart_type == "area":
                            ax.fill_between(range(len(labels)), data, alpha=0.35, label=name or None)
                            ax.plot(range(len(labels)), data, linewidth=1.6)
                        else:
                            ax.plot(range(len(labels)), data, marker="o", markersize=4,
                                    linewidth=1.8, label=name or None)
                    ax.set_xticks(range(len(labels)))
                    ax.set_xticklabels(labels)
                elif chart_type == "scatter":
                    for name, data in clean:
                        ax.scatter(range(len(labels)), data, s=42, label=name or None)
                    ax.set_xticks(range(len(labels)))
                    ax.set_xticklabels(labels)

                ax.grid(axis="x" if chart_type == "hbar" else "y",
                        alpha=0.25, linewidth=0.6)
                ax.set_axisbelow(True)
                for side in ("top", "right"):
                    ax.spines[side].set_visible(False)
                if rotate and chart_type != "hbar":
                    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
                if len(clean) > 1:
                    ax.legend(frameon=False, fontsize=9)
                if x_label:
                    ax.set_xlabel(x_label, fontsize=10)
                if y_label:
                    ax.set_ylabel(y_label, fontsize=10)

            if title:
                ax.set_title(title, fontsize=13)
            fig.tight_layout()
            fig.savefig(out_path)
        finally:
            plt.close(fig)

    logger.info("image_render [data/%s] -> %s", chart_type, out_path)
    return tool_result(success=True, source="data", chart_type=chart_type,
                       path=str(out_path), hint=_mode_hint(str(out_path), kw))


# ---- source=mermaid/html/markdown: headless-browser snapshots ----

def _build_html(kind: str, content: str) -> str:
    if kind == "markdown":
        import markdown as _md
        body = _md.markdown(content, extensions=["tables", "fenced_code"])
        return ("<!doctype html><html><head><meta charset=\"utf-8\">" + _PAGE_CSS +
                "</head><body>" + body + "</body></html>")
    if kind == "html":
        return ("<!doctype html><html><head><meta charset=\"utf-8\">" + _PAGE_CSS +
                "</head><body>" + content + "</body></html>")
    # mermaid
    if not _MERMAID_JS.exists():
        raise FileNotFoundError("mermaid.min.js asset missing")
    lib = _MERMAID_JS.read_text(encoding="utf-8")
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:24px;background:#ffffff">'
        '<div id="g"><pre class="mermaid">' + _escape(content) + "</pre></div>"
        "<script>" + lib + "</script>"
        "<script>mermaid.initialize({startOnLoad:false, theme:'default', "
        "securityLevel:'loose'});"
        "window.__done=false;"
        "mermaid.run({querySelector:'.mermaid'}).then(function(){window.__done=true})"
        ".catch(function(e){document.body.innerText='MERMAID_ERROR: '+e;"
        "window.__done=true;});</script></body></html>"
    )


def _render_content(args: dict, kw: dict) -> str:
    kind = args["source"]
    content = args.get("content")
    file = args.get("file")
    if bool(content) == bool(file):
        return tool_error("provide exactly one of content or file")
    if file:
        from core.support.paths import resolve_path
        p = Path(str(resolve_path(str(file), kw.get("parent_agent"))))
        if not p.exists():
            return tool_error(f"File not found: {p}")
        content = p.read_text(encoding="utf-8")

    try:
        width = int(args.get("width") or 900)
    except (TypeError, ValueError):
        width = 900
    width = max(320, min(width, 2000))

    try:
        html_doc = _build_html(kind, str(content))
    except FileNotFoundError as e:
        return tool_error(str(e))
    except Exception as e:
        return tool_error(f"content conversion failed: {e}")

    out_path = _resolve_output(args, kw, "snap")

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return tool_error("playwright is not installed — content snapshots unavailable in this environment")

    with sync_playwright() as pw:
        browser = None
        for ch in ("chrome", "msedge", None):
            try:
                kwargs = {"headless": True}
                if ch:
                    kwargs["channel"] = ch
                browser = pw.chromium.launch(**kwargs)
                break
            except Exception:
                continue
        if browser is None:
            return tool_error("no usable chromium (tried chrome/msedge/bundled)")

        try:
            page = browser.new_page(viewport={"width": width, "height": 600},
                                    device_scale_factor=2)
            page.set_content(html_doc, wait_until="load")
            if kind == "mermaid":
                page.wait_for_function("window.__done === true", timeout=20_000)
                err = page.locator("body").inner_text()
                if err.startswith("MERMAID_ERROR"):
                    return tool_error("mermaid failed: " + err[14:200])
            else:
                page.wait_for_timeout(150)
            page.locator("body").screenshot(path=str(out_path))
        finally:
            browser.close()

    if not out_path.exists() or out_path.stat().st_size < 200:
        return tool_error("render produced no image")
    logger.info("image_render [%s] -> %s", kind, out_path)
    return tool_result(success=True, source=kind, path=str(out_path),
                       hint=_mode_hint(str(out_path), kw))


def _image_render(args: dict, **kw) -> str:
    source = str(args.get("source") or "").lower()
    if source not in _SOURCES:
        return tool_error(f"source must be one of: {', '.join(_SOURCES)}")
    if source == "data":
        return _render_data_chart(args, kw)
    return _render_content(args, kw)


registry.register(
    name="image_render",
    schema={
        "type": "function",
        "function": {
            "name": "image_render",
            "description": (
                "Render an image file deterministically (no AI generation), "
                "fully offline — the compensation channel for chats WITHOUT "
                "native rendering. Four sources: 'data' = charts from "
                "structured numbers; 'mermaid' = diagrams "
                "(flowchart/sequence/pie/gantt…); 'html' = self-contained "
                "rich visuals/pages; 'markdown' = documents/tables/code as "
                "styled snapshots. Gateway chats (wecom/feishu) deliver the "
                "result with send_image(image_path=...). Desktop and CLI have "
                "native rendering (mermaid / html renderer / markdown "
                "tables) — prefer those in replies there; call this tool on "
                "desktop only when mermaid cannot express the chart (e.g. "
                "horizontal bars, precise multi-series) or the user wants an "
                "image FILE (the desktop shows the result under your reply "
                "automatically — never write markdown image syntax for it). "
                "To screenshot a LIVE web page or the interactive browser's "
                "current state, use browser_screenshot instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": list(_SOURCES),
                        "description": "Input form: data=structured chart, "
                                       "mermaid/html/markdown=content to snapshot",
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": list(_CHART_TYPES),
                        "description": "source=data: bar=对比/排名, hbar=长标签排名, "
                                       "line=趋势, area=趋势强调体量, pie=占比(≤6类, "
                                       "单系列), scatter=分布/相关",
                    },
                    "labels": {
                        "type": "array", "items": {"type": "string"},
                        "description": "source=data: category labels",
                    },
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "data": {"type": "array", "items": {"type": "number"}},
                            },
                            "required": ["data"],
                        },
                        "description": "source=data: one or more data series",
                    },
                    "content": {
                        "type": "string",
                        "description": "mermaid/html/markdown source text "
                                       "(content modes: exactly one of content/file)",
                    },
                    "file": {
                        "type": "string",
                        "description": "Path to a .mmd/.html/.md file (content modes)",
                    },
                    "title": {"type": "string", "description": "Title (chart heading)"},
                    "x_label": {"type": "string", "description": "source=data"},
                    "y_label": {"type": "string", "description": "source=data"},
                    "width": {"type": "integer",
                              "description": "Viewport px for content sources (default 900)"},
                    "output": {"type": "string",
                               "description": "Output PNG path (optional; default charts/ under the agent workspace)"},
                },
                "required": ["source"],
            },
        },
    },
    handler=lambda args, **kw: _image_render(args, **kw),
    check_fn=_check,
    toolset="media",
)
