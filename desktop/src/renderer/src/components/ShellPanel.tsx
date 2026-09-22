import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { Plus, X } from 'lucide-react'
import { desktop, type TermEvent } from '../lib/desktop'
import { useStore } from '../appStore'
import { cn } from '../lib/cn'
import { Resizer, usePanelSize } from './Resizer'

const MIN_HEIGHT = 160
const DEFAULT_HEIGHT = 300
const HEIGHT_KEY = 'shellPanelHeight'

// The pty starts at 100×30 and follows the viewer's fitted size via
// term:resize — wrapping is computed at the panel's real width.
const FIT_DEBOUNCE_MS = 150

/** The user's own local terminals (IDEA-style terminal drawer): multi-tab
 *  interactive shells, conversation-independent — new tabs seed their cwd
 *  from the active workspace when one is bound, then own it. Shells live in
 *  main and survive panel close; a dead tab stays until closed or restarted. */
export function ShellPanel({ className }: { className?: string }) {
  const [tabs, setTabs] = useState<number[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [dead, setDead] = useState<Set<number>>(new Set())
  const [height, setHeight] = usePanelSize(HEIGHT_KEY, DEFAULT_HEIGHT, MIN_HEIGHT)

  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  // session id → chars already rendered (attach snapshot cursor; out events
  // at or below it are replay duplicates).
  const writtenRef = useRef<Record<number, number>>({})
  const selectedRef = useRef(selected)
  selectedRef.current = selected

  const convId = useStore((s) => s.activeConvId)
  const convWorkspace = useStore((s) => s.convWorkspace)
  const workspaces = useStore((s) => s.workspaces)
  const wsId = convId ? convWorkspace[convId] : undefined
  const seedCwd = wsId ? workspaces.find((w) => w.id === wsId)?.workdir : undefined

  const shellCmdDraft = useStore((s) => s.shellCmdDraft)
  const clearShellCmdDraft = useStore((s) => s.clearShellCmdDraft)

  const createShell = async (runCmd?: string): Promise<number | null> => {
    const r = await desktop.termCreate(seedCwd, 100, 30)
    if (!r.ok) return null
    setTabs((prev) => [...prev, r.id])
    setSelected(r.id)
    if (runCmd) void desktop.termInput(r.id, runCmd + '\r')
    return r.id
  }

  // Rediscover live shells on mount (they survive panel close); a fresh
  // panel with none opens one so the drawer is never empty.
  useEffect(() => {
    let alive = true
    void desktop.termList().then(async (rows) => {
      if (!alive) return
      if (rows.length === 0) {
        await createShell()
      } else {
        setTabs(rows.map((r) => r.id))
        setSelected(rows[rows.length - 1].id)
      }
    })
    return () => {
      alive = false
    }
  }, [])

  // One-shot command from a Play entry point (editor tab / tree row): run it
  // in a NEW tab — interactive, so the launched program can be used.
  useEffect(() => {
    if (shellCmdDraft != null) {
      const c = shellCmdDraft
      clearShellCmdDraft()
      void createShell(c)
    }
  }, [shellCmdDraft, clearShellCmdDraft])

  // Live output / exits. Chunks for the selected session render through the
  // cursor (attach raced the snapshot); others are dropped — the backlog
  // covers them when their tab is switched back to.
  useEffect(() => {
    const off = desktop.onTermEvent((ev: TermEvent) => {
      if (ev.t === 'out') {
        if (ev.id !== selectedRef.current) return
        if (ev.w <= (writtenRef.current[ev.id] ?? 0)) return
        writtenRef.current[ev.id] = ev.w
        try {
          termRef.current?.write(ev.chunk)
        } catch {
          /* xterm mid-rebuild on a tab switch */
        }
      } else {
        setDead((prev) => {
          const n = new Set(prev)
          n.add(ev.id)
          return n
        })
        if (ev.id === selectedRef.current)
          try {
            termRef.current?.write(`\r\n\x1b[90m—— 终端已退出（${ev.reason}）——\x1b[0m\r\n`)
          } catch {
            /* same window */
          }
      }
    })
    return off
  }, [])

  // Terminal for the selected tab. Rebuilt on every switch — attach replays
  // the backlog tail so the fresh view feels continuous.
  useEffect(() => {
    if (selected == null) return
    const host = hostRef.current
    if (!host) return

    let alive = true

    const term = new Terminal({
      cols: 100,
      rows: 30,
      fontFamily: 'Consolas, "Courier New", monospace',
      fontSize: 12,
      cursorBlink: true,
      scrollback: 5000,
      theme: {
        background: '#0a0a0a',
        foreground: '#d4d4d4',
        cursor: '#d4d4d4',
        selectionBackground: '#64748b',
      },
    })
    termRef.current = term
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)

    const applyFit = (): void => {
      try {
        const dims = fit.proposeDimensions()
        if (!dims || !dims.cols || !dims.rows) return
        if (dims.cols === term.cols && dims.rows === term.rows) return
        term.resize(dims.cols, dims.rows)
        void desktop.termResize(selected, dims.cols, dims.rows)
      } catch {
        /* disposed mid-fit */
      }
    }
    let fitTimer: ReturnType<typeof setTimeout> | null = null
    const ro = new ResizeObserver(() => {
      if (fitTimer) clearTimeout(fitTimer)
      fitTimer = setTimeout(applyFit, FIT_DEBOUNCE_MS)
    })
    ro.observe(host)

    const dataSub = term.onData((d) => {
      void desktop.termInput(selected, d)
    })

    void desktop.termAttach(selected).then((r) => {
      if (!alive) return
      if (!r.ok) {
        setDead((prev) => new Set(prev).add(selected))
        return
      }
      term.write(r.backlog)
      writtenRef.current[selected] = r.w
      applyFit()
      term.focus()
    })

    return () => {
      alive = false
      if (fitTimer) clearTimeout(fitTimer)
      dataSub.dispose()
      ro.disconnect()
      term.dispose()
      termRef.current = null
    }
  }, [selected])

  const closeTab = (id: number): void => {
    void desktop.termKill(id)
    setDead((prev) => {
      const n = new Set(prev)
      n.delete(id)
      return n
    })
    const rest = tabs.filter((t) => t !== id)
    setTabs(rest)
    setSelected((cur) => (cur === id ? rest[rest.length - 1] ?? null : cur))
  }

  const restartTab = (id: number): void => {
    void desktop.termKill(id)
    setDead((prev) => {
      const n = new Set(prev)
      n.delete(id)
      return n
    })
    setTabs((prev) => prev.filter((t) => t !== id))
    void createShell()
  }

  return (
    <section className={cn('relative flex flex-col bg-app', className)} style={{ height }}>
      <Resizer
        axis="row"
        title="拖动调整面板高度"
        onMove={(_x, y) => {
          const max = Math.max(window.innerHeight - 240, MIN_HEIGHT)
          setHeight(Math.min(Math.max(window.innerHeight - y, MIN_HEIGHT), max))
        }}
        className="absolute left-0 top-0 h-1.5 w-full -translate-y-1/2"
      />

      <header className="flex items-center gap-1 border-b border-line px-3 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {tabs.map((id) => (
            <div
              key={id}
              className={cn(
                'group flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs transition',
                selected === id
                  ? 'bg-contrast text-ink'
                  : 'text-ink-3 hover:bg-elevated hover:text-ink'
              )}
            >
              <button onClick={() => setSelected(id)} className="flex items-center gap-1.5">
                <span
                  className={cn(
                    'h-1.5 w-1.5 rounded-full',
                    dead.has(id) ? 'bg-danger' : 'bg-accent'
                  )}
                />
                <span>终端 {id}</span>
              </button>
              <button
                onClick={() => closeTab(id)}
                title="关闭该终端"
                className="rounded p-0.5 text-ink-4 opacity-0 transition group-hover:opacity-100 hover:bg-elevated hover:text-ink"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => void createShell()}
          title="新建终端"
          className="shrink-0 rounded p-1.5 text-ink-3 transition hover:bg-elevated hover:text-ink"
        >
          <Plus className="h-4 w-4" />
        </button>
      </header>

      <div className="relative min-h-0 flex-1" onClick={() => termRef.current?.focus()}>
        {selected != null ? (
          <>
            <div
              ref={hostRef}
              className="h-full w-full overflow-auto px-2 py-1"
              style={{ background: '#0a0a0a' }}
            />
            {dead.has(selected) && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center gap-2 pb-2">
                <button
                  onClick={() => restartTab(selected)}
                  className="pointer-events-auto rounded bg-elevated px-2 py-1 text-[10px] text-accent"
                >
                  终端已退出 — 重启
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-4">
            <p className="text-xs">无终端</p>
            <button
              onClick={() => void createShell()}
              className="rounded-md bg-elevated px-3 py-1.5 text-xs text-ink-2 hover:text-ink"
            >
              新建终端
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
