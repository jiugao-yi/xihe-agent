import { existsSync } from 'fs'
import { homedir } from 'os'
import * as pty from 'node-pty'

/** The shell panel's local terminals (ConPTY): multi-instance, interactive,
 *  conversation-independent. Sessions survive panel close (the drawer is just
 *  a view); they die on tab close or app quit. Each keeps a small output ring
 *  with a cursor (total chars ever) so a re-attach replays the tail without
 *  duplicating chunks that raced the snapshot. */

export type TermEvent =
  | { id: number; t: 'out'; chunk: string; w: number }
  | { id: number; t: 'exit'; reason: string }

let pusher: ((ev: TermEvent) => void) | null = null
export function setTermPusher(p: (ev: TermEvent) => void): void {
  pusher = p
}

const BACKLOG_CHARS = 16 * 1024

interface Session {
  term: pty.IPty
  backlog: string
  total: number // chars ever written (survives ring truncation)
}

const sessions = new Map<number, Session>()
let nextId = 1

function preferredShell(): { file: string; args: string[] } {
  // PowerShell first (PSReadLine gives ↑ history / better editing); cmd is the
  // always-present fallback.
  const p = `${process.env.SystemRoot ?? 'C:\\Windows'}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe`
  return existsSync(p)
    ? { file: p, args: ['-NoLogo'] }
    : { file: 'cmd.exe', args: [] }
}

export function createTerminal(
  cwd: string | undefined,
  cols: number,
  rows: number
): { ok: true; id: number } | { ok: false; reason: string } {
  const { file, args } = preferredShell()
  const id = nextId++
  let term: pty.IPty
  try {
    term = pty.spawn(file, args, {
      name: 'xterm-256color',
      cols: Math.max(2, cols),
      rows: Math.max(2, rows),
      cwd: cwd && existsSync(cwd) ? cwd : homedir(),
      env: { ...process.env, TERM: 'xterm-256color' } as Record<string, string>,
    })
  } catch (err) {
    return { ok: false, reason: String(err) }
  }
  const s: Session = { term, backlog: '', total: 0 }
  sessions.set(id, s)
  term.onData((chunk) => {
    s.backlog = (s.backlog + chunk).slice(-BACKLOG_CHARS)
    s.total += chunk.length
    pusher?.({ id, t: 'out', chunk, w: s.total })
  })
  term.onExit(({ exitCode }) => {
    sessions.delete(id)
    pusher?.({ id, t: 'exit', reason: `exit ${exitCode}` })
  })
  return { ok: true, id }
}

/** Replay snapshot for a viewer (re)attaching to a session. */
export function attachTerminal(
  id: number
): { ok: true; backlog: string; w: number } | { ok: false; reason: string } {
  const s = sessions.get(id)
  if (!s) return { ok: false, reason: 'session-gone' }
  return { ok: true, backlog: s.backlog, w: s.total }
}

export function writeTerminal(id: number, data: string): boolean {
  const s = sessions.get(id)
  if (!s) return false
  try {
    s.term.write(data)
    return true
  } catch {
    return false // races a concurrent exit — the exit event reports it
  }
}

export function resizeTerminal(id: number, cols: number, rows: number): boolean {
  const s = sessions.get(id)
  if (!s) return false
  try {
    s.term.resize(Math.max(2, cols), Math.max(2, rows))
    return true
  } catch {
    return false // disposed mid-resize
  }
}

export function killTerminal(id: number): boolean {
  const s = sessions.get(id)
  if (!s) return false
  try {
    s.term.kill()
  } catch {
    /* already gone */
  }
  sessions.delete(id)
  return true
}

/** Live shell ids — a reopened panel rediscovers its tabs (newest last). */
export function listTerminals(): { id: number }[] {
  return [...sessions.keys()].map((id) => ({ id }))
}

export function killAllTerminals(): void {
  for (const s of sessions.values()) {
    try {
      s.term.kill()
    } catch {
      /* already gone */
    }
  }
  sessions.clear()
}
