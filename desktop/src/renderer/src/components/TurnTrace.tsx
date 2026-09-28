import { memo, useEffect, useMemo, useState, type MouseEvent } from 'react'
import { Brain, Check, ChevronRight, Copy, ExternalLink, Loader2, Navigation, ShieldAlert, SplitSquareHorizontal, Wrench, X } from 'lucide-react'
import type { TraceEvent } from '../appStore'
import { cn } from '../lib/cn'
import { getFullToolResult, getToolArgs } from '../lib/serveClient'
import { useStore } from '../appStore'
import { detectLanguage } from '../lib/lang'
import { DiffBlock } from './DiffBlock'

type ToolEv = Extract<TraceEvent, { kind: 'tool' }>
type ThoughtEv = Extract<TraceEvent, { kind: 'thought' }>
type SteerEv = Extract<TraceEvent, { kind: 'steer' }>

/** Collapsible trace of one assistant turn's reasoning + tool calls, rendered
 * ABOVE the answer bubble. Events with no source (``by``) belong to the main
 * agent; attributed events (subagent-N / specialist slug / claude / codex …)
 * group into their own collapsible sections — mixed multi-source turns read
 * per-actor instead of as one interleaved stream. Within a section the
 * original arrival order is kept (reasoning and tool calls alternate there
 * exactly as they did).
 *
 * Open state is fully user-controlled; initial state follows `pending` (a
 * running turn mounts open so live progress shows; a finished or historical
 * turn mounts collapsed). Sub-sources default to expanded while their tools
 * still run, collapsed once settled.
 *
 * Historical turns arrive with only a folded tool count, a serve row anchor,
 * and a `hasReasoning` flag — reasoning + tool calls are lazy-loaded via
 * `onLoadTrace` on expand. */
export function TurnTrace({
  trace,
  pending,
  toolsCount,
  hasReasoning,
  anchor,
  onLoadTrace,
}: {
  trace?: TraceEvent[]
  pending: boolean
  /** Historical turn: tool count shown in the header before the trace is
   *  lazy-loaded. Live turns carry the real list in `trace` instead. */
  toolsCount?: number
  /** Historical turn: serve reported persisted reasoning — shows a 思考 badge
   *  in the collapsed header before the trace is lazy-loaded. */
  hasReasoning?: boolean
  /** Serve row uuid used to lazy-load the trace (historical turns only). */
  anchor?: string
  /** Expand hook that fires the lazy load. */
  onLoadTrace?: (anchor: string) => void
}) {
  const [open, setOpen] = useState(pending)
  // Per-source fold state; keys are the `by` labels. A key absent = follow
  // the default (main always open; a source folds once it has no running
  // tools) — entries only appear when the user has explicitly toggled.
  const [foldedSources, setFoldedSources] = useState<Record<string, boolean>>({})
  const events = trace ?? []
  const tools = events.filter((t): t is ToolEv => t.kind === 'tool')
  const running = tools.filter((t) => t.status === 'running').length
  const interruptedTools = tools.filter((t) => t.status === 'interrupted').length
  const hasThought = events.some((t): t is ThoughtEv => t.kind === 'thought')
  const steerCount = events.filter((t): t is SteerEv => t.kind === 'steer').length
  // Header count: live trace has the real list; a historical turn shows its
  // folded count until the trace is lazy-loaded on expand.
  const toolCount = toolsCount ?? tools.length
  // Lazy-load: expanding a historical turn (trace not yet fetched) pulls it.
  useEffect(() => {
    if (open && !trace && anchor != null && onLoadTrace) onLoadTrace(anchor)
    // onLoadTrace is a per-render inline closure; intentionally omitted from
    // deps so a re-render mid-fetch doesn't fire duplicate loads. Live turns
    // have anchor=undefined, so this never fires for them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, trace, anchor])

  // Two-level display model over the raw event stream:
  //
  // 1. Chronological runs: consecutive same-`by` events form a run; thought
  //    deltas inside a run merge into one text (streaming fragments read as
  //    prose). Main-agent runs (by='') render bare, exactly where they
  //    happened — the delegation's before/after stays in true order.
  // 2. Parallel-interleave collapse: concurrently running subagents inter-
  //    leave their deltas, so one source can appear as many tiny runs
  //    ("subagent-1 0个工具, subagent-2 1个工具, subagent-1 2个工具…").
  //    When a non-main source occurs in MULTIPLE runs, its runs merge into
  //    ONE visual block (placed at the source's first appearance, internal
  //    order kept, counts summed) — the block reads as "this subagent's
  //    session", not as N timestamp slices. A source with a single
  //    contiguous run is untouched.
  type Block = { key: string; tools: number; running: number; items: TraceEvent[] }
  const blocks = useMemo(() => {
    // Pass 1 — chronological runs with in-run thought merging.
    const runs: Block[] = []
    for (const t of events) {
      const key = (t as ToolEv | ThoughtEv).by ?? ''
      const last = runs[runs.length - 1]
      const mergeThought = (
        last && last.key === key && t.kind === 'thought'
        && last.items.length > 0
        && last.items[last.items.length - 1].kind === 'thought')
      if (mergeThought) {
        const prev = last.items[last.items.length - 1] as ThoughtEv
        prev.text += t.text
        continue
      }
      if (!last || last.key !== key) {
        runs.push({ key, tools: 0, running: 0, items: [] })
      }
      const cur = runs[runs.length - 1]
      cur.items.push(t)
      if (t.kind === 'tool') {
        cur.tools += 1
        if (t.status === 'running') cur.running += 1
      }
    }
    // Pass 2 — collapse multi-run non-main sources into one block at the
    // first-run position; main ('') runs pass through in place.
    const counts = new Map<string, number>()
    for (const r of runs) counts.set(r.key, (counts.get(r.key) ?? 0) + 1)
    const out: Block[] = []
    const merged = new Map<string, Block>()
    for (const r of runs) {
      if (r.key === '' || (counts.get(r.key) ?? 0) === 1) {
        out.push(r)
        continue
      }
      let m = merged.get(r.key)
      if (!m) {
        m = { key: r.key, tools: 0, running: 0, items: [] }
        merged.set(r.key, m)
        out.push(m)   // at first-run position
      }
      m.items.push(...r.items)
      m.tools += r.tools
      m.running += r.running
    }
    return out
  }, [events])
  const hasSources = blocks.some((s) => s.key !== '')

  if (!toolCount && !hasThought && !steerCount && !hasReasoning) return null

  // Click anywhere on the panel (not just the header) toggles — a long 思考
  // run scrolls the header out of reach otherwise. Clicks on interactive
  // children (tool rows, result blocks, links) and clicks that end a text
  // selection pass through untouched.
  const onPanelClick = (e: MouseEvent) => {
    const t = e.target as HTMLElement
    if (t.closest('button, a, input, textarea, pre')) return
    if (window.getSelection()?.toString()) return
    setOpen((v) => !v)
  }

  const renderEvent = (t: TraceEvent, i: number, child = false) =>
    t.kind === 'tool' ? <ToolItem key={i} ev={t} child={child} /> :
    t.kind === 'thought' ? <ThoughtItem key={i} ev={t} child={child} /> :
    <SteerItem key={i} ev={t} />

  /** One block's items with ALL its thought events coalesced into a single
   *  prose block at the top (one 🧠, one paragraph) and the tool rows below
   *  — the main agent's thinking renders as one continuous block, so a
   *  subagent's must too. Thought text merges with delta-boundary newlines
   *  normalized (\r\n → \n, 3+ blank lines → 2) — the fragments were one
   *  stream before the store split them. ``child`` styles the rows as
   *  subagent content (smaller/lighter) so they read subordinate to the
   *  main agent's own rows. */
  const renderBlockItems = (items: TraceEvent[], child = false) => {
    const thoughtText = items
      .filter((t): t is ThoughtEv => t.kind === 'thought')
      .map((t) => t.text.replace(/\r\n/g, '\n'))
      .join('')
      .replace(/\n{3,}/g, '\n\n')
    const rest = items.filter((t) => t.kind !== 'thought')
    return (
      <>
        {thoughtText && <ThoughtItem key="thought" child={child} ev={{ kind: 'thought', text: thoughtText, ts: rest[0] && (rest[0] as ToolEv).ts } as ThoughtEv} />}
        {rest.map((t, i) => renderEvent(t, i, child))}
      </>
    )
  }

  return (
    <div
      onClick={onPanelClick}
      className="w-full cursor-pointer rounded-xl border border-line bg-panel/60 text-xs text-ink-3"
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:text-ink"
      >
        <ChevronRight className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-90')} />
        {toolCount > 0 && (
          <span className="inline-flex items-center gap-1">
            <Wrench className="h-3 w-3" />
            {toolCount} 个工具
            {running > 0 ? ` · ${running} 运行中` : ''}
            {interruptedTools > 0 ? ` · ${interruptedTools} 已中断` : ''}
          </span>
        )}
        {(hasThought || hasReasoning) && (
          <span className="inline-flex items-center gap-1">
            <Brain className="h-3 w-3" />
            思考
          </span>
        )}
        {steerCount > 0 && (
          <span className="inline-flex items-center gap-1">
            <Navigation className="h-3 w-3" />
            {steerCount} 改向
          </span>
        )}
        {hasSources && (
          <span className="ml-auto font-mono text-[10px] text-ink-4">
            {Array.from(new Set(blocks.map((s) => s.key).filter(Boolean))).join(' · ')}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-line/70 px-2.5 py-2">
          {trace ? (
            hasSources ? (
              <SubagentDashboard blocks={blocks} renderBlockItems={renderBlockItems} foldedSources={foldedSources} setFoldedSources={setFoldedSources} />
            ) : (
              events.map((t, i) => renderEvent(t, i))
            )
          ) : (
            <div className="px-1 py-0.5 text-ink-4">加载工具调用…</div>
          )}
        </div>
      )}
    </div>
  )
}

const ARGS_PREVIEW = 60

/** Per-source accent colors: label tail number (subagent-1/2/3, explore-2…)
 *  cycles this low-saturation palette. Colors the status-row name and the
 *  expand area's left bar — one color per actor, readable at a glance. */
const SOURCE_COLORS = [
  { text: 'text-sky-400', bar: 'border-l-2 border-sky-500/70' },
  { text: 'text-emerald-400', bar: 'border-l-2 border-emerald-500/70' },
  { text: 'text-amber-400', bar: 'border-l-2 border-amber-500/70' },
  { text: 'text-violet-400', bar: 'border-l-2 border-violet-500/70' },
]

function _taskSeqNo(key: string): number {
  const tail = key.split('-').pop() ?? ''
  const n = Number(tail)
  return Number.isFinite(n) && n > 0 ? n : 1
}

/** Activity dashboard for delegated subagent activity (the Manus-pattern
 *  "global status + single-focus detail"): every source renders as ONE
 *  status row — state icon (✓ done / ⟳ running / ✗ interrupted), name,
 *  tool count, and the LATEST tool call as a one-line "what it's doing"
 *  cue. Clicking a row expands that subagent's full process inline (one
 *  focus at a time — concurrent streams are never live-tiled).
 *
 *  Position keeps the main agent's narrative true: subagent blocks cluster
 *  into a dashboard card rendered at the DELEGATION point — a dashboard
 *  flushes right BEFORE the next main-agent block, so "what the main agent
 *  did after dispatching" still reads in order. */
function SubagentDashboard({ blocks, renderBlockItems, foldedSources, setFoldedSources }: {
  blocks: { key: string; tools: number; running: number; items: TraceEvent[] }[]
  renderBlockItems: (items: TraceEvent[], child?: boolean) => React.ReactNode
  foldedSources: Record<string, boolean>
  setFoldedSources: React.Dispatch<React.SetStateAction<Record<string, boolean>>>
}) {
  type Row = { key: string; tools: number; running: number; items: TraceEvent[] }
  // Walk blocks in order; subagent runs merge per-source into a pending
  // dashboard that flushes when the next MAIN block (or the end) arrives.
  const parts: Array<{ kind: 'main'; items: TraceEvent[] } | { kind: 'dash'; rows: Row[] }> = []
  let pending: Map<string, Row> | null = null
  const flush = () => {
    if (pending && pending.size > 0) {
      parts.push({ kind: 'dash', rows: [...pending.values()] })
    }
    pending = null
  }
  for (const b of blocks) {
    if (b.key === '') {
      flush()
      parts.push({ kind: 'main', items: b.items })
      continue
    }
    if (!pending) pending = new Map()
    let r = pending.get(b.key)
    if (!r) {
      r = { key: b.key, tools: 0, running: 0, items: [] }
      pending.set(b.key, r)
    }
    r.items.push(...b.items)
    r.tools += b.tools
    r.running += b.running
  }
  flush()

  return (
    <>
      {parts.map((p, pi) => p.kind === 'main' ? (
        <div key={pi} className="space-y-1.5">{renderBlockItems(p.items)}</div>
      ) : (
        <div key={pi} className="rounded-lg border border-line/60 divide-y divide-line/40">
          {p.rows.map((r) => {
            const done = r.running === 0
            const interrupted = r.items.some(
              (t) => t.kind === 'tool' && t.status === 'interrupted')
            // Latest tool call = the one-line "what it's doing" cue.
            const lastTool = [...r.items].reverse().find((t) => t.kind === 'tool') as ToolEv | undefined
            const isOpen = r.key in foldedSources ? !foldedSources[r.key] : false
            const shortArgs = lastTool ? lastTool.args.replace(/\s+/g, ' ').slice(0, 48) : ''
            // Per-owner color: label tail number cycles a low-sat palette;
            // colors the status-row NAME and the expand area's left bar so
            // color alone answers "whose output is this".
            const accent = SOURCE_COLORS[
              ((_taskSeqNo(r.key) - 1) % SOURCE_COLORS.length + SOURCE_COLORS.length) % SOURCE_COLORS.length]
            return (
              <div key={r.key}>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setFoldedSources((m) => ({ ...m, [r.key]: isOpen }))
                  }}
                  className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-elevated/40"
                >
                  <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center">
                    {interrupted ? (
                      <X className="h-3 w-3 text-warning" />
                    ) : done ? (
                      <Check className="h-3 w-3 text-success/80" />
                    ) : (
                      <Loader2 className="h-3 w-3 animate-spin text-ink-4" />
                    )}
                  </span>
                  <span className={cn('shrink-0 font-mono text-[10px]', accent.text)}>{r.key}</span>
                  <span className="shrink-0 text-[10px] text-ink-4">{r.tools} 工具</span>
                  {lastTool && (
                    <span className="min-w-0 flex-1 truncate text-[10px] text-ink-4">
                      <span className="font-mono text-ink-3">{lastTool.name}</span>
                      {shortArgs && <span className="ml-1">{shortArgs}</span>}
                    </span>
                  )}
                  <ChevronRight className={cn('h-3 w-3 shrink-0 text-ink-5 transition-transform', isOpen && 'rotate-90')} />
                </button>
                {isOpen && (
                  <div className={cn('space-y-1.5 border-t border-line/40 bg-app/40 py-1.5 pl-4', accent.bar)}>
                    {renderBlockItems(r.items, true)}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ))}
    </>
  )
}

/** Parse a tool result/args JSON string; null when not a JSON object (live
 *  args are a 120-char slice and usually don't parse). */
function parseToolJson(s: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(s)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** 历史审批徽章：该工具调用曾被审批门拦下，决议随 result JSON 持久化、
 *  trace 端点提取而来。title 带完整 summary + 决议原因。 */
function ApprovalBadge({ approval }: {
  approval: NonNullable<ToolEv['approval']>
}) {
  const approved = approval.decision === 'approved'
  const label = approved
    ? (approval.always ? '已批准（不再询问）' : '已批准')
    : '已拒绝'
  return (
    <span
      title={`${approval.summary}${approval.reason ? ` — ${approval.reason}` : ''}`}
      className={cn(
        'inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px]',
        approved ? 'bg-warning/10 text-warning/90' : 'bg-danger/10 text-danger'
      )}
    >
      <ShieldAlert className="h-2.5 w-2.5" />
      {label}
    </span>
  )
}

const ToolItem = memo(function ToolItem({ ev, child }: { ev: ToolEv; child?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  // Live tool_call events carry a 500-char slice + an id — fetch the full
  // args on first expand (history trace events already carry full args, no id).
  const [fullArgs, setFullArgs] = useState<string | null>(null)
  const argsText = fullArgs ?? ev.args
  const tooLong = argsText.length > ARGS_PREVIEW
  const ts = fmtTs(ev.ts)
  const openFileTab = useStore((s) => s.openFileTab)
  const openDiffTab = useStore((s) => s.openDiffTab)
  const layoutMode = useStore((s) => s.layoutMode)
  const setLayoutMode = useStore((s) => s.setLayoutMode)

  // write_file/patch results embed the unified diff server-side. Live AND
  // historical turns both carry it (it rides in the tool_result payload).
  const parsedResult =
    ev.status === 'done' && ev.result != null ? parseToolJson(ev.result) : null
  const diffInfo =
    parsedResult && (ev.name === 'write_file' || ev.name === 'patch') && parsedResult.success === true && typeof parsedResult.diff === 'string' && parsedResult.diff
      ? { path: String(parsedResult.path ?? ''), unified: parsedResult.diff }
      : null

  // 打开对比 needs the FULL args (patch old/new) — only historical traces have
  // them (live args are the 120-char slice, which fails JSON.parse above).
  const parsedArgs = ev.name === 'patch' ? parseToolJson(ev.args) : null
  const diffable =
    !!diffInfo &&
    !!parsedArgs &&
    typeof parsedArgs.old === 'string' &&
    typeof parsedArgs.new === 'string'

  const gotoWorkbench = () => {
    if (layoutMode === 'chat') void setLayoutMode('workbench')
  }
  return (
    <div className="flex items-start gap-1.5">
      <span className="mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        {ev.status === 'running' ? (
          <Loader2 className="h-3 w-3 animate-spin text-ink-4" />
        ) : ev.status === 'interrupted' ? (
          <X className="h-3 w-3 text-warning" />
        ) : (
          <Check className="h-3 w-3 text-success/80" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={cn(
            'rounded px-1 py-0.5 font-mono',
            child ? 'bg-transparent text-[10px] text-ink-4' : 'bg-elevated text-[11px] text-ink-2'
          )}>
            {ev.name}
          </span>
          {ev.approval ? <ApprovalBadge approval={ev.approval} /> : null}
          {ev.status === 'interrupted' ? (
            <span className="text-[10px] text-warning">已中断</span>
          ) : typeof ev.elapsed === 'number' ? (
            <span className="text-[10px] text-ink-4">{ev.elapsed}s</span>
          ) : null}
          {ts && <span className="ml-auto text-[10px] tabular-nums text-ink-4">{ts}</span>}
        </div>
        {ev.args && (
          <button
            type="button"
            onClick={() => {
              if (!tooLong) return
              setExpanded((v) => !v)
              if (
                !expanded && fullArgs === null && ev.id != null &&
                ev.args.length >= 500
              ) {
                void getToolArgs(ev.id).then((a) => a && setFullArgs(a))
              }
            }}
            className={cn(
              'mt-0.5 block w-full select-text text-left font-mono text-ink-4 hover:text-ink-3',
              expanded ? 'whitespace-pre-wrap break-all' : 'truncate'
            )}
            title={argsText}
          >
            {expanded ? argsText : argsText.slice(0, ARGS_PREVIEW)}
            {tooLong && !expanded && '…'}
          </button>
        )}
        {diffInfo ? (
          <DiffBlock
            path={diffInfo.path}
            unified={diffInfo.unified}
            actions={
              <>
                {diffInfo.path && (
                  <button
                    type="button"
                    onClick={() => {
                      gotoWorkbench()
                      openFileTab(diffInfo.path)
                    }}
                    className="inline-flex items-center gap-1 hover:text-ink-2"
                    title="在编辑器中打开此文件"
                  >
                    <ExternalLink className="h-3 w-3" />
                    打开文件
                  </button>
                )}
                {diffable && parsedArgs && diffInfo.path && (
                  <button
                    type="button"
                    onClick={() => {
                      gotoWorkbench()
                      openDiffTab({
                        title: 'patch 对比',
                        oldText: String(parsedArgs.old),
                        newText: String(parsedArgs.new),
                        oldHeader: '替换前（片段）',
                        newHeader: '替换后（片段）',
                        language: detectLanguage(diffInfo.path),
                      })
                    }}
                    className="inline-flex items-center gap-1 hover:text-ink-2"
                    title="在对比视图中查看 patch 的替换片段"
                  >
                    <SplitSquareHorizontal className="h-3 w-3" />
                    打开对比
                  </button>
                )}
              </>
            }
          />
        ) : ev.status === 'done' && ev.result != null ? (
          <ResultBlock result={ev.result} truncated={ev.truncated} />
        ) : null}
        {diffInfo && ev.result != null && (
          <ResultBlock result={ev.result} truncated={ev.truncated} forceCollapse label="原始结果" />
        )}
      </div>
    </div>
  )
})

const THOUGHT_PREVIEW = 160

const ThoughtItem = memo(function ThoughtItem({ ev, child }: { ev: ThoughtEv; child?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const tooLong = ev.text.length > THOUGHT_PREVIEW
  const ts = fmtTs(ev.ts)
  return (
    <div className="flex items-start gap-1.5">
      <Brain className={cn('mt-0.5 h-3 w-3 shrink-0', child ? 'text-ink-5/70' : 'text-ink-4')} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-1.5">
          <p
            className={cn(
              'min-w-0 flex-1 select-text whitespace-pre-wrap italic',
              child ? 'text-[11px] leading-snug text-ink-5' : 'text-ink-4',
              !expanded && tooLong && 'line-clamp-2'
            )}
          >
            {/* Collapsed: clamp the DOM node too, not just paint — a long
                claude/codex thinking run would otherwise put tens of KB into a
                text node re-diffed on every store flush. */}
            {!expanded && tooLong ? `${ev.text.slice(0, 400)}…` : ev.text}
          </p>
          {ts && (
            <span className="shrink-0 text-[10px] tabular-nums text-ink-4">{ts}</span>
          )}
        </div>
        {tooLong && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] text-ink-4 hover:text-ink-3"
          >
            {expanded ? '收起' : '展开'}
          </button>
        )}
      </div>
    </div>
  )
})

function SteerItem({ ev }: { ev: SteerEv }) {
  // A user redirect injected mid-turn (not a tool/thought). Set apart with an
  // amber accent so it reads as the user's input, not the agent's activity.
  return (
    <div className="flex items-start gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-1.5">
      <Navigation className="mt-0.5 h-3 w-3 shrink-0 text-warning/80" />
      <div className="min-w-0 flex-1">
        <span className="text-[10px] uppercase tracking-wide text-warning/70">steer · 改向</span>
        <p className="whitespace-pre-wrap break-words text-warning/70">{ev.text}</p>
      </div>
    </div>
  )
}

/** HH:MM:SS for a live-trace event ts (client epoch). Historical traces carry no
 *  ts (serve's trace endpoint has no per-call timestamps), so this returns null
 *  there and the caller omits the stamp. */
function fmtTs(ts?: number): string | null {
  if (!ts) return null
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// Lines shown before a result card collapses. Long outputs (file reads, search
// dumps) stay scannable; expand reveals the full server-truncated payload.
const RESULT_COLLAPSE_LINES = 5

// Marker line the serve-side spill writes into the preview ("Full output
// saved to: <path>") — the side-store path /toolresult serves.
const SPILL_PATH_RE = /Full output saved to: (.+)/

function ResultBlock({
  result,
  truncated,
  forceCollapse,
  label = '输出',
}: {
  result: string
  truncated?: boolean
  /** Collapse even when short — used for the raw JSON shown under a DiffBlock. */
  forceCollapse?: boolean
  label?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  // Full spilled content fetched from /toolresult (replaces the preview once
  // loaded); `loading`/`failed` gate the fetch button's states.
  const [full, setFull] = useState<string | null>(null)
  const [loadingFull, setLoadingFull] = useState(false)
  const [failed, setFailed] = useState(false)
  const spillPath = truncated ? result.match(SPILL_PATH_RE)?.[1]?.trim() : undefined
  const text = full ?? result
  // split cost scales with the (up-to-15K) payload — memoize so renders that
  // don't change the text (copy flash, expand toggles elsewhere) skip it.
  const lines = useMemo(() => text.split('\n'), [text])
  const lineCount = lines.length
  const collapsible = forceCollapse || lineCount > RESULT_COLLAPSE_LINES
  const shown =
    expanded || !collapsible ? text : lines.slice(0, RESULT_COLLAPSE_LINES).join('\n')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      // clipboard may be unavailable (permissions / non-secure context) — no-op
    }
  }
  const loadFull = async () => {
    if (!spillPath || loadingFull || full != null) return
    setLoadingFull(true)
    try {
      const content = await getFullToolResult(spillPath)
      if (content == null) {
        setFailed(true)
      } else {
        setFull(content)
        setExpanded(true)
      }
    } finally {
      setLoadingFull(false)
    }
  }
  return (
    <div className="mt-1 overflow-hidden rounded-md border border-line bg-app/60">
      <div className="flex items-center gap-1.5 border-b border-line/70 px-2 py-1 text-[10px] text-ink-4">
        <span>{label}</span>
        <span className="rounded bg-elevated px-1 py-0.5 text-ink-3 tabular-nums">
          {lineCount} 行
        </span>
        {spillPath ? (
          full == null ? (
            <button
              type="button"
              onClick={loadFull}
              disabled={loadingFull}
              className="inline-flex items-center gap-1 text-warning/80 hover:text-warning disabled:opacity-50"
            >
              {loadingFull ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              {loadingFull ? '加载中…' : failed ? '加载失败，重试' : '加载完整结果'}
            </button>
          ) : (
            <span className="text-success/80">已加载完整结果</span>
          )
        ) : (
          truncated && <span className="text-warning/80">已截断（完整结果见服务端会话）</span>
        )}
        <button
          type="button"
          onClick={copy}
          className="ml-auto inline-flex items-center gap-1 hover:text-ink-2"
        >
          {copied ? (
            <Check className="h-3 w-3 text-success" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
          {copied ? '已复制' : '复制'}
        </button>
        {collapsible && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="hover:text-ink-2"
          >
            {expanded ? '收起' : '展开'}
          </button>
        )}
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-2 py-1.5 font-mono text-[11px] leading-relaxed text-ink-3">
        {shown}
        {!expanded && collapsible && '\n…'}
      </pre>
    </div>
  )
}
