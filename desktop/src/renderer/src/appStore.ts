import { create } from 'zustand'
import {
  connectStream,
  deleteSession,
  getHealth,
  getHistory,
  truncateConversation,
  getTrace,
  listCron,
  listMcp,
  listSessions,
  listSkills,
  resetSession,
  setConversationTitle,
  type CronJobInfo,
  type McpServer,
  type SchedulerHealth,
  type ServeEvent,
  type ServeStream,
  type SkillInfo,
} from './lib/serveClient'
import { loadWorkspaceStore, saveWorkspaceStore } from './lib/persist'
import type { TurnUsage } from './lib/serveClient'
import {
  desktop,
  type XiheStatus,
  type XiheConfig,
  type XiheConfigPatch,
  type ThemeMode,
  type LayoutMode,
  type WindowState,
} from './lib/desktop'

/** One conversation. `id` is the serve conv_id. */
export interface ConvMeta {
  id: string
  title: string
  /** true once this conv exists on the server (sent at least once, or seen in
   *  /sessions). An unsent "新对话" is local-only and can be removed without a
   *  server round-trip — DELETE on a conv serve never created returns
   *  deleted:false, which must not block local removal. */
  synced?: boolean
  /** A turn is streaming in this conversation right now (serve truth on
   *  sync; WS turn_start/complete keep it live between syncs). Sidebar
   *  badges the title with a spinner. */
  running?: boolean
}

/** A reusable working directory the user can bind a conversation to. Desktop-only;
 *  serve is workspace-unaware. Persisted in localStorage (lib/persist). */
export interface Workspace {
  id: string
  name: string
  workdir: string
}

export type TraceEvent =
  | { kind: 'thought'; text: string; ts?: number; by?: string }
  | { kind: 'tool'; name: string; args: string; status: 'running' | 'done' | 'interrupted'; elapsed?: number; result?: string; truncated?: boolean; ts?: number; by?: string; id?: number; approval?: { decision: string; summary: string; reason?: string; always?: boolean } }
  | { kind: 'steer'; text: string }

/** Approval card state on an assistant bubble — set by approval_request,
 *  settled by the user's buttons (optimistic) or approval_resolved (timeout /
 *  interrupt / reply from another client), expired when the turn ends with a
 *  card still pending. */
export interface PendingApproval {
  id: string
  name: string
  summary: string
  status: 'pending' | 'approved' | 'denied' | 'expired'
  /** Raw tool-args JSON (serve-side cap 64K) — the 查看变更 preview reads it.
   *  Absent on older cores / non-args events. */
  args?: string
}

/** Clarify card state on an assistant bubble — set by clarify_request,
 *  settled by the user's answer (optimistic) or clarify_resolved. */
export interface PendingClarify {
  id: string
  question: string
  options: string[]
  status: 'pending' | 'answered' | 'expired'
  /** The delivered answer once known (own click or the resolved event). */
  answer?: string
}

/** One-shot comparison payload for a diff tab (Monaco DiffEditor). */
export interface EditorDiffPayload {
  title: string
  oldText: string
  newText: string
  oldHeader?: string
  newHeader?: string
  language?: string
}

/** A tab in the workbench editor area. `file` tabs are keyed by absolute path
 *  (one tab per file, re-opened tabs are re-activated); `diff` tabs are
 *  ephemeral snapshots opened from diff cards / approval cards. */
export interface EditorTab {
  id: string
  kind: 'file' | 'diff'
  title: string
  /** file tabs: the absolute path on disk. */
  path?: string
  diff?: EditorDiffPayload
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 附件（仅用户消息）：live 发送时本地记录；历史同步时来自 serve 的
   *  attachments 列（图片带 desc 描述，气泡可作 title 展示）。图片路径
   *  渲染经 xfile:// 特权协议。 */
  attachments?: { name: string; path: string; size?: number; desc?: string }[]
  /** ISO timestamp — set locally on live sends, from serve DB rows on
   *  history sync. Rendered as HH:mm on the message bubble. */
  ts?: string
  /** serve row uuid of the user row (history-synced messages only) — addresses
   * the message server-side for resend truncation. */
  serveId?: string
  pending?: boolean
  error?: boolean
  /** live approval card for this turn (at most one: approval tools are all
   *  write → sequential dispatch on the server) */
  pendingApproval?: PendingApproval
  /** live clarify card for this turn (at most one) */
  pendingClarify?: PendingClarify
  /** Stop clicked — optimistic, shown until the turn finalises */
  stopping?: boolean
  /** Turn ended because the user stopped it (authoritative, from complete.reason) */
  interrupted?: boolean
  /** Historical turns: serve reported the turn never finished (interrupted
   *  mid-loop / serve process died mid-turn) — renders a 未完成 badge. */
  incomplete?: boolean
  /** ordered tool/thought events for an assistant turn (rendered as a trace) */
  trace?: TraceEvent[]
  /** Historical turns: serve row uuid to lazy-fetch `trace` on expand (absent
   *  on live turns, which accumulate trace from the stream). */
  traceAnchor?: string
  /** Historical turns: tool-call count shown in the trace header before the
   *  trace is lazy-loaded (>0 → render the collapsed header). */
  toolsCount?: number
  /** Historical turns: serve reported persisted reasoning — mount the trace
   *  (with a 思考 badge) before lazy-load, so reasoning-only turns are visible. */
  hasReasoning?: boolean
  /** Token usage of this turn (live: from the complete event; history: from
   *  the persisted final assistant row) — rendered as a cost badge. */
  usage?: TurnUsage
  /** History-synced only: first message of a post-reset round — a reset
   *  divider renders before it. */
  roundStart?: boolean
  /** Reset reason label source ('manual' | 'idle' | 'daily'); absent when
   *  the boundary predates reason persistence. */
  resetReason?: string
  /** Synthetic trailing marker: render ONLY the reset divider, no bubble —
   *  a reset whose round has no messages yet. */
  dividerOnly?: boolean
}

interface DesktopState {
  // [[0026]]: xihe is the single BUILT-IN agent; claude/codex are reached via
  // its external_agent tool, never as peer desktop agents.
  /** all conversations of the built-in agent; a new one = a new conv_id */
  conversations: ConvMeta[]
  /** conversation currently shown in the chat panel; null = no conversation
   *  selected (empty list / boot) → chat shows a "start new chat" CTA. */
  activeConvId: string | null
  /** model name of the connected serve (from /health); null until connected */
  serveModel: string | null
  /** keyed by conv_id — one message list per conversation */
  sessions: Record<string, Message[]>
  serveConnected: boolean
  serveVersion: string | null
  /** Process-level status of the built-in xihe serve, pushed by main
   *  (`xihe:status`) and pulled on mount. Two-source design: this (main truth)
   *  decides WHEN to attempt a connection; `serveConnected` (WS truth) decides
   *  whether streaming actually SUCCEEDED. See applyXiheStatus for coordination. */
  xiheStatus: XiheStatus | null
  /** Desktop-wide process-level listings for the "管理" panel.
   *  Loaded lazily by loadManageData() when the panel mounts / serve connects —
   *  not fetched on every connectServe (avoid hitting 3 endpoints nobody opens). */
  mcpServers: McpServer[]
  skills: SkillInfo[]
  cronJobs: CronJobInfo[]
  schedulerHealth: SchedulerHealth | null
  /** active right-pane tab (chat vs manage/store full-window pages). Lifted
   *  into the store so a workspace-open action can land the user on the chat tab. */
  activeTab: 'chat' | 'manage' | 'store' | 'knowledge'
  /** Browser-panel visibility. Lifted into the store so the stream handler can
   *  auto-open it when the agent uses a browser tool (the snapped Chrome then
   *  appears without a manual toggle). */
  showBrowser: boolean
  /** True after the user explicitly closed the panel — suppresses auto-open
   *  for the rest of the current turn; the next sendMessage re-arms it. */
  browserAutoMuted: boolean
  setShowBrowser: (v: boolean) => void
  /** Close the panel WITHOUT muting auto-open — for non-user closings
   *  (switching conversations), where a later browser tool call in the new
   *  conversation should still auto-open the panel. */
  dismissBrowserPanel: () => void
  /** Terminal-panel visibility (bottom drawer over the chat). Fully manual —
   *  nothing auto-opens it; the user toggles the toolbar button. */
  showTerminal: boolean
  setShowTerminal: (v: boolean) => void
  /** Pending terminal-panel focus: the next new resident-process channel
   *  under the active conversation's session. Consumed by the panel, or
   *  dropped when the user has pinned a tab. (Auto-OPEN was removed — this
   *  only steers the tab when the panel is ALREADY open.) */
  agentTermTarget:
    | { kind: 'conv'; key: string }
    | { kind: 'proc'; sessionKey: string }
    | null
  clearAgentTermTarget: () => void
  /** Shell-panel visibility (the user's local terminals — a window-level
   *  bottom drawer, full-width in every layout). Pure user toggle, scoped to
   *  neither conversation nor workspace. */
  showShell: boolean
  setShowShell: (v: boolean) => void
  /** One-shot command delivered when a Play entry (editor tab / tree row)
   *  opens the panel — runs in a NEW shell tab, then cleared. */
  shellCmdDraft: string | null
  openShellPanel: (cmd?: string) => void
  clearShellCmdDraft: () => void
  /** conv_id → serve session_key (recorded from turn_start). The terminal
   *  panel uses the ACTIVE conversation's key when quick-connecting so the
   *  session is grouped with it; agent-triggered sessions carry it already. */
  convSessionKeys: Record<string, string>
  /** reusable working-directory entities (desktop-only, persisted) */
  workspaces: Workspace[]
  /** conv_id → workspace_id binding; the single source of truth for which
   *  workspace a conversation is bound to (kept off ConvMeta so it survives
   *  syncConversations rebuilds). Absent key = 通用/unbound. */
  convWorkspace: Record<string, string>
  /** Non-null while "inside" a workspace: the sidebar then shows that
   *  workspace's bound conversations instead of the full list, and the file
   *  tree is shown on the right. null = the ordinary conversation list. */
  activeWorkspaceId: string | null
  /** Effective xihe config from ~/.xihe-agent/config.yaml (single source).
   *  Empty until hydrateXiheConfig() runs on mount; api_key is present only as
   *  api_key_set (never the plaintext). */
  xiheConfig: XiheConfig
  sendMessage: (
    text: string,
    plan?: boolean,
    regenerate?: boolean,
    attachments?: { name: string; path: string; size?: number; desc?: string }[]
  ) => void
  /** Resend a user message: truncate the conversation to before it (serve
   * rows + local state) and send its text again as a fresh turn. No-ops while
   * a turn is streaming or serve is unreachable. */
  resendMessage: (index: number) => Promise<void>
  /** Edit a user message and resend it: same rollback as resendMessage, but
   * the edited text replaces the original as the fresh turn. */
  editAndResendMessage: (index: number, newText: string) => Promise<void>
  /** Regenerate an assistant reply: keep the user message before it, truncate
   * from the turn's first row, and re-send that user message. */
  regenerateMessage: (index: number) => Promise<void>
  interrupt: () => void
  steer: (text: string) => void
  /** Answer the active turn's pending approval card (approve/deny buttons).
   *  always pairs with approval ("本会话不再询问" — session memory server-side). */
  approve: (id: string, approved: boolean, always?: boolean) => void
  /** Answer the active turn's pending clarify card (option click / free text). */
  answerClarify: (id: string, answer: string) => void
  newConversation: () => void
  selectConversation: (convId: string) => void
  resetConversation: (convId: string) => Promise<void>
  deleteConversation: (convId: string) => Promise<void>
  /** Rename a conversation. Connected + already on the server → POST /title
   *  (persists; xihe's auto-title skips sessions that already have a title, so a
   *  manual rename sticks). An unsent "新对话" (synced !== true) has no serve
   *  session yet → skip the round-trip and rename locally only (its first send
   *  will create the session and run auto-title, which may overwrite — an
   *  accepted edge case). */
  renameConversation: (convId: string, title: string) => Promise<void>
  /** Reload a conversation's transcript from serve + refresh the list (picks up
   *  serve-generated titles). `convId` targets a specific conversation (the
   *  per-conv refresh button — opens it + reloads); omit for the active one. */
  refreshConversation: (convId?: string) => Promise<void>
  setTab: (tab: 'chat' | 'manage' | 'store' | 'knowledge') => void
  addWorkspace: (workdir: string, name?: string) => void
  removeWorkspace: (id: string) => void
  openConversationInWorkspace: (workspaceId: string) => void
  /** Enter a workspace: scope the sidebar to its conversations, pick the most
   *  recent bound one (or seed a fresh bound conv if it has none), land on the
   *  chat tab. The middle/right panes follow because the active conv is bound. */
  openWorkspace: (workspaceId: string) => void
  /** Leave workspace view; the sidebar returns to the full conversation list. */
  exitWorkspace: () => void
  hydrateWorkspaceStore: () => Promise<void>
  /** Read effective xihe config from ~/.xihe-agent/config.yaml into state. */
  hydrateXiheConfig: () => Promise<void>
  /** Desktop theme mode. 'dark' until hydrateTheme() reads the persisted
   *  value (~/.xihe-desktop/settings.json — desktop-local, never xihe
   *  config). The html.light class toggle driven by it lives in App.tsx. */
  theme: ThemeMode
  hydrateTheme: () => Promise<void>
  /** Apply + persist a theme immediately (click-to-apply; main flips
   *  nativeTheme and pushes the resolved light/dark to xihe so the browser
   *  panel's Chrome matches). */
  setTheme: (t: ThemeMode) => Promise<void>
  /** Layout mode: chat (conversation-centric, default) vs workbench
   *  (editor-centric — tree left / editor center / chat right). Hydrated from
   *  the same settings.json as theme; switching persists immediately. */
  layoutMode: LayoutMode
  setLayoutMode: (m: LayoutMode) => Promise<void>
  /** Workbench editor tabs. Session-scoped (not persisted). */
  editorTabs: EditorTab[]
  activeTabId: string | null
  /** Open (or re-activate) a file tab by absolute path. */
  openFileTab: (path: string) => void
  /** Open a new ephemeral diff tab from a snapshot payload. */
  openDiffTab: (diff: EditorDiffPayload) => void
  closeTab: (id: string) => void
  setActiveTab: (id: string) => void
  /** Close every file tab at *path* or under it (tree delete follow-up). */
  closeTabsUnder: (path: string) => void
  /** Re-point a file tab whose path was renamed in the tree (id is path-keyed).
   *  An unsaved draft keyed by the old path is not carried over — the tab
   *  reloads from disk. */
  renameTabPath: (oldPath: string, newPath: string) => void
  /** Bumped whenever workspace files change on disk (agent write_file/patch
   *  results, desktop-side CRUD, editor saves) — the file tree, git status
   *  and open editor tabs re-read on change of this counter. */
  fsVersion: number
  bumpFsVersion: () => void
  /** Line-patch xihe config.yaml with the given keys (comment-preserving), then
   *  re-read effective values into state. Returns whether the write succeeded.
   *  Edits take effect on the running serve only after serveRestart(). */
  saveXiheConfig: (patch: XiheConfigPatch) => Promise<boolean>
  connectServe: () => Promise<boolean>
  /** Apply a process-level status update from main. running → drive connectServe
   *  (the real connect trigger now); stopped/errored → drop serveConnected and
   *  stop the WS client from racing main's restart. */
  applyXiheStatus: (s: XiheStatus) => void
  /** Pull MCP/skills/cron listings from serve into state (no-op unless serve is
   *  connected). Called on ManagePanel mount + when serve connects; also wired
   *  to the panel's refresh button. */
  loadManageData: () => Promise<void>
  /** Lazy-load a historical turn's tool-call trace from serve by its anchor
   *  row id, writing it onto the message so the trace panel can render it.
   *  No-op if the message isn't found or already has a trace. */
  loadTrace: (convId: string, msgId: string) => Promise<void>
}

// View-state restore bookkeeping. `wsHydrated` gates the window-state writer —
// before the store file has been read, boot-time nulls must not clobber it.
// `restoredConvId` is the one-shot pickActive preference: consumed by the
// first syncConversations (or dropped when the conv no longer exists).
let wsHydrated = false
let restoredConvId: string | null = null
// Stable identity of THIS window's persisted record. Future extra windows
// each carry their own entry in workspaces.json's `windows` array.
const WINDOW_ID = 'main'

// Editor-tab sets per workspace (key = workspace id, '' = unbound view). The
// live editorTabs/activeTabId ARE the currently-mounted bucket; a workspace
// switch parks the outgoing set here and mounts the incoming one (swap
// subscription below). Seeded from workspaces.json at hydrate; buckets of
// workspaces that no longer exist are dropped at persist time.
let tabBuckets: Record<string, { tabs: EditorTab[]; active: string | null }> = {}

/** Bucket key for the current view: the workspace the ACTIVE conversation is
 *  bound to (what the tree/editor act on), '' when unbound. */
function tabKeyOf(s: { activeConvId: string | null; convWorkspace: Record<string, string> }): string {
  return (s.activeConvId && s.convWorkspace[s.activeConvId]) || ''
}

/** This window's persisted record — a pure function of live state + the tab
 *  buckets, so every writer computes it fresh with no copy to keep in sync. */
function windowRecordOf(s: {
  activeConvId: string | null
  activeWorkspaceId: string | null
  workspaces: Workspace[]
}): WindowState {
  const tabsByWorkspace: WindowState['tabsByWorkspace'] = {}
  for (const [k, b] of Object.entries(tabBuckets)) {
    if (k !== '' && !s.workspaces.some((w) => w.id === k)) continue
    const tabs: string[] = []
    let active: string | null = null
    for (const t of b.tabs) {
      if (t.kind === 'file' && t.path) {
        tabs.push(t.path)
        if (t.id === b.active) active = t.path
      }
    }
    if (tabs.length > 0) tabsByWorkspace[k] = { tabs, active }
  }
  return {
    id: WINDOW_ID,
    workspaceId: s.activeWorkspaceId,
    convId: s.activeConvId,
    tabsByWorkspace,
  }
}

/** Resident transcript cache ceiling — beyond this, oldest non-active convs are
 *  evicted from `sessions` (their transcript refetches lazily on reopen). */
const MAX_SESSIONS = 16

export const useStore = create<DesktopState>()((set, get) => {
  let stream: ServeStream | null = null
  let deliberateClose = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  // Guards connectServe against concurrent re-entry: the xihe:status pull and
  // push can both fire on the same `running` transition, and App mount calls us
  // once too. serveConnected alone can't dedup (it flips only after the async
  // getHealth resolves), so an in-flight flag is needed.
  let connectInFlight = false

  // Delta coalescing: text/thought deltas arrive once per model chunk — one
  // zustand set + one render per chunk made long turns quadratic (every delta
  // re-rendered the transcript). Buffer per conv, apply as ONE patch per
  // ~50ms; any non-delta event flushes first so trace ordering holds. While
  // the chat tab is hidden the flush is postponed (nothing paints) and
  // complete/error still force-flush.
  type DeltaOp = (m: Message) => Message
  const deltaBuf = new Map<string, DeltaOp[]>()
  let deltaTimer: ReturnType<typeof setTimeout> | null = null

  function bufferDelta(convId: string | undefined, op: DeltaOp) {
    if (!convId) return
    const ops = deltaBuf.get(convId)
    if (ops) ops.push(op)
    else deltaBuf.set(convId, [op])
    scheduleDeltaFlush()
  }

  function scheduleDeltaFlush() {
    if (deltaTimer) return
    deltaTimer = setTimeout(() => {
      deltaTimer = null
      if (deltaBuf.size === 0) return
      if (get().activeTab !== 'chat') {
        setTimeout(scheduleDeltaFlush, 200)
        return
      }
      flushDeltas()
    }, 50)
  }

  /** Apply every conv's buffered delta ops as one message patch + one set. */
  function flushDeltas() {
    if (deltaBuf.size === 0) return
    for (const [convId, ops] of [...deltaBuf]) {
      deltaBuf.delete(convId)
      if (ops.length === 0) continue
      patchPending(convId, (m) => ops.reduce((mm, op) => op(mm), m))
    }
  }

  /** Keep the resident transcript cache bounded: evict the oldest
   * non-protected conv entries (whole key — reopening lazily refetches from
   * serve, `convId in sessions` is the cache sentinel). Protected: the
   * active conv and any conv with a live turn. */
  function trimSessions() {
    const s = get()
    const keys = Object.keys(s.sessions)
    if (keys.length <= MAX_SESSIONS) return
    const protect = new Set([s.activeConvId ?? ''])
    for (const k of keys) {
      if ((s.sessions[k] ?? []).some((m) => m.role === 'assistant' && m.pending))
        protect.add(k)
    }
    let excess = keys.length - MAX_SESSIONS
    const sessions = { ...s.sessions }
    for (const k of keys) {
      if (excess <= 0) break
      if (protect.has(k)) continue
      delete sessions[k]
      excess--
    }
    if (Object.keys(sessions).length !== keys.length) set({ sessions })
  }

  function retryStreamConnection() {
    if (reconnectTimer || deliberateClose) return
    // Don't hammer /stream while main reports the serve process is down (or
    // still starting) — main owns the restart, and reconnecting would race it.
    // Only retry transient socket blips that happen WHILE running.
    const st = get().xiheStatus?.state
    if (st && st !== 'running') return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      void openStream()
    }, 3000)
  }

  async function openStream() {
    if (deliberateClose) return
    try {
      stream = await connectStream(handleEvent, (connected) => {
        set({ serveConnected: connected })
        if (!connected && !deliberateClose) retryStreamConnection()
      })
      await resyncAfterReconnect()
    } catch {
      if (!deliberateClose) retryStreamConnection()
    }
  }

  /** After (re)connecting: a turn that survived a dropout server-side (serve
   *  keeps detached turns running for a grace window) resumes streaming to
   *  this socket via `attach`. The transcript refetch self-skips while a turn
   *  streams locally (refetchConv's guard — wiping the live bubble was the
   *  missing-正在思考… bug); a turn that COMPLETED while we were away is
   *  settled by the attached{running:false} ack's force-refetch instead. */
  async function resyncAfterReconnect() {
    const convId = get().activeConvId
    if (!convId || !get().serveConnected) return
    stream?.attach(convId)
    await refetchConv(convId)
  }

  function handleEvent(e: ServeEvent) {
    // Non-delta events flush any buffered deltas first — a tool_call/complete
    // must observe the text that streamed before it.
    if (e.type !== 'text_delta' && e.type !== 'thought_delta') flushDeltas()
    if (e.type === 'text_delta')
      bufferDelta(e.conv_id, (m) => ({ ...m, content: m.content + e.text, pending: true }))
    else if (e.type === 'thought_delta')
      // Accumulate consecutive reasoning deltas into one thought entry — but
      // only within the same source (`by`): claude's thinking must not glue
      // onto the main agent's mid-stream.
      bufferDelta(e.conv_id, (m) => {
        const trace = m.trace ? m.trace.slice() : []
        const last = trace[trace.length - 1]
        if (last && last.kind === 'thought' && last.by === e.by)
          trace[trace.length - 1] = { ...last, text: last.text + e.text }
        else trace.push({ kind: 'thought', text: e.text, ts: Date.now(), by: e.by })
        return { ...m, trace }
      })
    else if (e.type === 'tool_call') {
      // Agent picked up a browser tool → surface the panel so the snapped
      // Chrome is visible without a manual toggle (unless the user closed it).
      if (e.name.startsWith('browser_') && !get().browserAutoMuted) {
        set({ showBrowser: true })
      }
      patchPending(e.conv_id, (m) => {
        const trace = m.trace ? m.trace.slice() : []
        trace.push({ kind: 'tool', name: e.name, args: e.args, status: 'running', ts: Date.now(), by: e.by, id: e.id })
        return { ...m, trace }
      })
    }
    else if (e.type === 'tool_result') {
      // Agent-side file writes move disk state under the workspace — bump so
      // the tree / git status / open editor tabs re-read.
      if (e.name === 'write_file' || e.name === 'patch') {
        set((s) => ({ fsVersion: s.fsVersion + 1 }))
      }
      // Pair the result with its running tool_call by echoed ring id —
      // exact even for parallel same-name tools. A result whose id matches
      // nothing (duplicate resolve / stale) is dropped.
      patchPending(e.conv_id, (m) => {
        if (!m.trace) return m
        const trace = m.trace.slice()
        const idx = trace.findIndex(
          (t) => t.kind === 'tool' && t.status === 'running' && t.id === e.id)
        if (idx < 0) return { ...m, trace }
        const cur = trace[idx]
        if (cur.kind === 'tool')
          trace[idx] = { ...cur, status: 'done', elapsed: e.elapsed, result: e.result, truncated: e.truncated }
        return { ...m, trace }
      })
    }
    else if (e.type === 'turn_start') {
      // Sidebar spinner: this conv has a live turn now (cleared on
      // complete/error — syncConversations re-syncs serve truth later).
      setConvRunning(e.conv_id, true)
      // Record the conv's serve session_key — the terminal panel joins
      // desktop quick-connects to the conversation they were opened from.
      if (e.session_key && !get().convSessionKeys[e.conv_id])
        set((s) => ({ convSessionKeys: { ...s.convSessionKeys, [e.conv_id]: e.session_key } }))
      // This send crossed a reset boundary server-side (auto reset fires
      // inside get_or_create_session, after our optimistic append): flag the
      // just-appended user bubble so the round divider shows live, not only
      // after the next transcript refetch.
      if (e.auto_reset) {
        set((s) => {
          const list = s.sessions[e.conv_id]
          if (!list) return {}
          for (let i = list.length - 1; i >= 0; i--) {
            if (list[i].role === 'user') {
              const next = list.slice()
              next[i] = { ...next[i], roundStart: true, resetReason: e.auto_reset ?? undefined }
              return { sessions: { ...s.sessions, [e.conv_id]: next } }
            }
          }
          return {}
        })
      }
      // The pending assistant bubble is created optimistically in
      // sendMessage. Interrupt targets conv_id, not turn_id.
    } else if (e.type === 'cron_result') {
      // 定时任务结果推送（DesktopChannel，serve 模式）：已加载的会话就地
      // 追加一条；未加载只刷新列表。非当前会话时弹系统通知。
      const convId = e.conv_id
      if (convId && get().sessions[convId]) {
        set((s) => ({
          sessions: {
            ...s.sessions,
            [convId]: [
              ...(s.sessions[convId] ?? []),
              { id: uid(), role: 'assistant', content: e.text ?? '' },
            ],
          },
        }))
      }
      void syncConversations()
      // 任务跑完，调度列表里的 next/last/state 已变——顺带刷新管理面板数据
      void get().loadManageData()
      const isActive = get().activeConvId === convId
      if (!isActive) {
        // 主进程通知（web Notification 在 Windows 开发构建下静默失败）
        void desktop.notify('xihe 定时任务', (e.text ?? '').slice(0, 120))
      }
    } else if (e.type === 'attached') {
      // serve's attach ack. running=false with a local pending bubble → the
      // turn settled server-side while we were disconnected; no complete is
      // coming, so force-refetch to replace the stale bubble with the
      // persisted reply (refetchConv's normal guard would skip it). Without a
      // pending bubble the resync refetch already covers staleness.
      if (!e.running) {
        const live = (get().sessions[e.conv_id] ?? []).some(
          (m) => m.role === 'assistant' && m.pending
        )
        if (live) void settleDroppedTurn(e.conv_id)
      }
    } else if (e.type === 'approval_request') {
      patchPending(e.conv_id, (m) => ({
        ...m,
        pendingApproval: {
          id: e.id,
          name: e.name,
          summary: e.summary,
          status: 'pending',
          args: e.args,
        },
      }))
    } else if (e.type === 'clarify_request') {
      patchPending(e.conv_id, (m) => ({
        ...m,
        pendingClarify: {
          id: e.id,
          question: e.question,
          options: e.options ?? [],
          status: 'pending',
        },
      }))
    } else if (e.type === 'clarify_resolved') {
      // Authoritative for every path but our own click (which settles
      // optimistically below): the other client's answer, timeout, interrupt.
      patchPending(e.conv_id, (m) => {
        const cl = m.pendingClarify
        if (!cl || cl.id !== e.id || cl.status !== 'pending') return m
        return {
          ...m,
          pendingClarify: {
            ...cl,
            status: e.status === 'answered' ? 'answered' : 'expired',
            answer: e.answer ?? undefined,
          },
        }
      })
    } else if (e.type === 'approval_resolved') {
      // Every resolution path lands here — the other client's reply, timeout,
      // interrupt. Our own button click already settled the card optimistically;
      // this is authoritative for everything else.
      patchPending(e.conv_id, (m) => {
        const ap = m.pendingApproval
        if (!ap || ap.id !== e.id || ap.status !== 'pending') return m
        return {
          ...m,
          pendingApproval: {
            ...ap,
            status: e.approved ? 'approved' : 'denied',
          },
        }
      })
    } else if (e.type === 'complete') {
      setConvRunning(e.conv_id, false)
      // Turn settled: drop a proc target the turn may still hold (a start
      // whose channel never appeared).
      {
        const t = get().agentTermTarget
        if (t?.kind === 'proc' && get().convSessionKeys[e.conv_id] === t.sessionKey)
          set({ agentTermTarget: null })
      }
      const interrupted = e.reason === 'interrupted'
      // chat() returns "API error: …" as text instead of raising — flag it so
      // the bubble renders as an error, not a normal reply.
      const failed = e.reason === 'api_error' || e.reason === 'api_timeout'
      patchPending(e.conv_id, (m) => ({
        ...m,
        // On interrupt keep the partial text already streamed; don't overwrite
        // it with the agent's "[interrupted]" marker.
        content: interrupted ? m.content : e.text || m.content,
        pending: false,
        stopping: undefined,
        interrupted: interrupted || undefined,
        error: failed || undefined,
        usage: e.usage,
        pendingApproval: expireApproval(m.pendingApproval),
        pendingClarify: expireClarify(m.pendingClarify),
        trace: sweepRunningTools(m.trace, interrupted ? 'interrupted' : 'done'),
      }))
      if (get().serveConnected) {
        // Refresh the conversation list so a serve-generated title (first turn)
        // appears and the list re-sorts by recency. syncConversations never
        // touches messages, so the just-finalised content is safe.
        void syncConversations()
        // serve generates the session title on a fire-and-forget aux-LLM thread
        // started inside agent.chat, which lands AFTER `complete` — so the
        // sync above usually reads the row before its title is written. Re-sync
        // a moment later to pick up the generated title (manual refresh covers
        // the slow-aux tail).
        setTimeout(() => {
          if (get().serveConnected) void syncConversations()
        }, 2500)
      }
      trimSessions()
    } else if (e.type === 'error') {
      patchPending(e.conv_id, (m) => ({
        ...m,
        content: `⚠️ ${e.message}`,
        pending: false,
        stopping: undefined,
        error: true,
        pendingApproval: expireApproval(m.pendingApproval),
        pendingClarify: expireClarify(m.pendingClarify),
        trace: sweepRunningTools(m.trace),
      }))
      if (e.conv_id) setConvRunning(e.conv_id, false)
    }
    // hello: connection metadata, already applied via connectServe
  }

  /** attach acked running=false while we held a pending bubble: the turn ended
   *  server-side while we were disconnected. Adopt the persisted transcript —
   *  but when it shows the turn never actually finished (serve process died
   *  mid-turn / interrupted elsewhere), the force-refetch alone would silently
   *  swap the in-flight bubble for a folded tool-call history that reads like
   *  a finished reply. Say so instead. */
  async function settleDroppedTurn(convId: string) {
    const mapped = await refetchConv(convId, true)
    if (!mapped) return
    const last = mapped[mapped.length - 1]
    if (last && last.role === 'assistant' && !last.incomplete) return
    set((s) => ({
      sessions: {
        ...s.sessions,
        [convId]: [
          ...(s.sessions[convId] ?? []),
          {
            id: uid(),
            role: 'assistant',
            content: '⚠️ 连接中断，本轮回复未完成。已执行的工具调用结果已保留，可点上方未完成标记旁的「继续」按钮续跑。',
            error: true,
          },
        ],
      },
    }))
  }

  /** Routes a streaming event to the pending assistant bubble of its conv_id.
   * Events already carry conv_id, so we index sessions directly (no agent
   * reverse-lookup needed). */
  function patchPending(convId: string | undefined, fn: (m: Message) => Message) {
    if (!convId) return
    const list = get().sessions[convId] ?? []
    let idx = -1
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].role === 'assistant' && list[i].pending) {
        idx = i
        break
      }
    }
    // No pending bubble — normally events can't precede the optimistic bubble
    // sendMessage creates, but after a reconnect resync replaced the
    // transcript, resumed deltas arrive with no bubble to land on. Create one.
    if (idx < 0) {
      const fresh: Message = { id: uid(), role: 'assistant', content: '', pending: true }
      set((s) => ({
        sessions: { ...s.sessions, [convId]: [...list, fn(fresh)] },
      }))
      return
    }
    const next = list.slice()
    next[idx] = fn(next[idx])
    set((s) => ({ sessions: { ...s.sessions, [convId]: next } }))
  }

  /** Flip a conversation's sidebar running flag in place (WS turn lifecycle:
   *  turn_start / complete / error). No-op for unknown conv ids — a conv the
   *  list doesn't know (e.g. deleted mid-turn) needs no badge. */
  function setConvRunning(convId: string, running: boolean) {
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === convId)
      if (idx < 0 || !!s.conversations[idx].running === running) return {}
      const next = s.conversations.slice()
      next[idx] = { ...next[idx], running: running || undefined }
      return { conversations: next }
    })
  }

  /** Rebuild the conversation list from serve `/sessions`: server rows
   *  update title/order; local unsent new conversations (not yet on the server)
   *  are preserved at the top. Never touches messages. */
  async function syncConversations() {
    if (!get().serveConnected) return
    const { activeConvId: prevActive, conversations: prevConvs } = get()
    const rows = await listSessions()
    const serverIds = new Set(rows.map((r) => r.conv_id))
    const localOnly = prevConvs.filter((c) => !serverIds.has(c.id))
    // localOnly was snapshotted before the await; a sync/new-conv during it can
    // resurface a stale local copy beside its server copy, so dedupe.
    const merged: ConvMeta[] = []
    const seen = new Set<string>()
    for (const r of rows) {
      if (seen.has(r.conv_id)) continue
      seen.add(r.conv_id)
      merged.push({ id: r.conv_id, title: r.title || '新对话', synced: true,
                    running: r.running || undefined })
    }
    for (const c of localOnly) {
      if (seen.has(c.id)) continue
      seen.add(c.id)
      merged.push(c)
    }
    // Nothing selected yet (fresh boot, or the user just deleted the last conv)
    // and the server has history → land on the most recent so the chat isn't an
    // empty CTA despite a populated list. Self-limiting: once a conv is active
    // this never overrides the user's selection. A restored lastView convId
    // (app restart) outranks recency — one-shot, consumed here.
    const prefer = restoredConvId
    restoredConvId = null
    const pickActive =
      prevActive ??
      (prefer && merged.some((m) => m.id === prefer)
        ? prefer
        : merged.length > 0
          ? merged[0].id
          : null)
    // Session keys ride along — the terminal panel scopes channels by them.
    // turn_start is the live-write path; this fills conversations that had no
    // turn this session (keys are deterministic per conversation, so no
    // conflict).
    const keyAdds: Record<string, string> = {}
    for (const r of rows) {
      if (r.session_key) keyAdds[r.conv_id] = r.session_key
    }
    set((s) => ({
      conversations: merged,
      activeConvId: pickActive,
      convSessionKeys: Object.keys(keyAdds).length
        ? { ...s.convSessionKeys, ...keyAdds }
        : s.convSessionKeys,
    }))
  }

  /** Fetch + map a conversation's transcript from serve into Message[]. Shared
   *  by lazy first-load (loadActiveHistory) and forced reload (refreshConversation). */
  async function fetchHistory(convId: string): Promise<Message[]> {
    const resp = await getHistory(convId)
    const msgs = resp?.messages ?? []
    const mapped: Message[] = msgs
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({
        id: uid(),
        role: m.role as 'user' | 'assistant',
        content: m.content,
        ts: m.ts,
        serveId: m.role === 'user' && typeof m.id === 'string' ? m.id : undefined,
        // Assistant turns fold their tool calls into a count + a stable anchor
        // (serve row uuid); the trace itself is lazy-fetched on expand.
        traceAnchor: typeof m.id === 'string' ? m.id : undefined,
        toolsCount: typeof m.tools === 'number' ? m.tools : undefined,
        hasReasoning: m.has_reasoning === true,
        incomplete: m.incomplete === true,
        usage: m.usage,
        roundStart: m.round_start || undefined,
        resetReason: m.reset_reason,
        attachments: m.attachments?.length ? m.attachments : undefined,
      }))
    // A reset with no messages in its round yet: append a trailing divider
    // so the reset is visible immediately, not only on the next message.
    if (resp?.pending_reset != null) {
      mapped.push({
        id: `reset-pending-${convId}`,
        role: 'assistant',
        content: '',
        dividerOnly: true,
        resetReason: resp.pending_reset || undefined,
      })
    }
    return mapped
  }

  /** Replace a conv's transcript with freshly fetched history. Skips while a
   *  turn is streaming — a replace would wipe the in-flight bubble (its
   *  正在思考… hint plus any already-streamed text) and leave a blank gap
   *  until the next delta auto-recreates one. `force` bypasses the guard for
   *  a turn serve confirmed already settled (the attached{running:false}
   *  ack): the stale bubble must be replaced by the persisted final text.
   *  Returns the applied transcript, or null when skipped. */
  async function refetchConv(convId: string, force = false): Promise<Message[] | null> {
    const streaming = () =>
      (get().sessions[convId] ?? []).some((m) => m.role === 'assistant' && m.pending)
    if (!force && streaming()) return null
    const mapped = await fetchHistory(convId)
    // Re-check after the await: a send that started mid-fetch must not have
    // its optimistic bubble wiped by this now-stale snapshot.
    if (!force && streaming()) return null
    set((s) => ({ sessions: { ...s.sessions, [convId]: mapped } }))
    return mapped
  }

  /** Lazy-load persisted history from serve for the active conversation
   *  (first open only — skips if already loaded). */
  async function loadActiveHistory() {
    if (!get().serveConnected) return
    const convId = get().activeConvId
    if (!convId || convId in get().sessions) return
    const mapped = await fetchHistory(convId)
    set((s) => ({ sessions: { ...s.sessions, [convId]: mapped } }))
  }

  /** Mirror workspaces + bindings to ~/.xihe-desktop/workspaces.json. Called
   *  after every mutation; fire-and-forget — state is the UI's source of truth,
   *  the file is the persisted copy. */
  function persistWs(): void {
    const s = get()
    void saveWorkspaceStore({
      workspaces: s.workspaces,
      convWorkspace: s.convWorkspace,
      windows: [windowRecordOf(s)],
    })
  }

  /** Shared rollback for 重新发送/编辑重发/重新生成: pull a fresh transcript,
   *  truncate the conversation at the user bubble addressed by `index`, adopt
   *  the post-truncate truth, then send `text` (null = re-send the row's own
   *  content). No-op while a turn is streaming or serve is unreachable. */
  async function rollbackAndSend(index: number, text: string | null,
                                 opts: { regenerate?: boolean } = {}) {
    const convId = get().activeConvId
    if (!convId || !get().serveConnected) {
      console.warn('[rollback] skipped: no active conv or serve disconnected')
      return
    }
    if ((get().sessions[convId] ?? []).some((m) => m.role === 'assistant' && m.pending)) {
      console.warn('[rollback] skipped: a turn is still streaming')
      return
    }
    const mapped = await fetchHistory(convId)
    const target = mapped[index]
    if (!target || target.role !== 'user' || target.serveId == null) {
      console.warn('[rollback] skipped: no user row with a serve id at index', index)
      return
    }
    const ok = await truncateConversation(convId, target.serveId)
    if (!ok) {
      console.warn('[rollback] truncate refused by serve (turn in progress?)')
      return
    }
    // Adopt the post-truncate truth (everything before the target), then
    // let the normal send path append + stream the fresh turn. `regenerate`
    // tells serve to inject a no-persist "answer independently" hint — the
    // industry regenerate semantics: the model must not anchor on its own
    // earlier answer to the same prompt.
    set((s) => ({ sessions: { ...s.sessions, [convId]: mapped.slice(0, index) } }))
    // 重发保留原消息的附件（serve 重新注入路径提示，模型上下文不丢）。
    get().sendMessage(text ?? target.content, undefined, opts.regenerate, target.attachments)
  }

  return {
    conversations: [],
    activeConvId: null,
    serveModel: null,
    sessions: {},
    serveConnected: false,
    serveVersion: null,
    // null until the first xihe:status push/pull lands (main starts the serve
    // child on app boot and reports back within ~1s).
    xiheStatus: null,
    // Empty until loadManageData() runs (ManagePanel mount / serve connect).
    mcpServers: [],
    skills: [],
    cronJobs: [],
    schedulerHealth: null,
    activeTab: 'chat',
    showBrowser: false,
    browserAutoMuted: false,
    showTerminal: false,
    agentTermTarget: null,
    showShell: false,
    shellCmdDraft: null,
    convSessionKeys: {},
    // Empty until hydrateWorkspaceStore() loads ~/.xihe-desktop/workspaces.json
    // on app mount (file IO is async, so the store can't seed synchronously).
    workspaces: [],
    convWorkspace: {},
    activeWorkspaceId: null,
    // Empty until hydrateXiheConfig() reads ~/.xihe-agent/config.yaml on mount
    // (file IO is async). Keys present here are EFFECTIVE values (file value or
    // xihe's default); api_key only as api_key_set.
    xiheConfig: {},
    // 'dark' both as the persisted default and pre-hydrate stand-in, so the
    // first paint already matches the last session's likely look.
    theme: 'dark',
    layoutMode: 'chat',
    editorTabs: [],
    activeTabId: null,
    fsVersion: 0,

    hydrateTheme: async () => {
      const s = await desktop.settingsLoad()
      set({ theme: s.theme, layoutMode: s.layoutMode })
    },
    setTheme: async (t) => {
      set({ theme: t })
      await desktop.setTheme(t)
    },
    setLayoutMode: async (m) => {
      set({ layoutMode: m })
      await desktop.setLayoutMode(m)
    },

    openFileTab: (path) => {
      const id = 'file:' + path
      set((s) => {
        if (s.editorTabs.some((t) => t.id === id)) return { activeTabId: id }
        return {
          editorTabs: [...s.editorTabs, { id, kind: 'file', title: baseNameOf(path), path }],
          activeTabId: id,
        }
      })
    },

    openDiffTab: (diff) => {
      const id = 'diff:' + uid()
      set((s) => ({
        editorTabs: [...s.editorTabs, { id, kind: 'diff', title: diff.title, diff }],
        activeTabId: id,
      }))
    },

    closeTab: (id) => {
      set((s) => {
        const idx = s.editorTabs.findIndex((t) => t.id === id)
        if (idx < 0) return {}
        const tabs = s.editorTabs.filter((t) => t.id !== id)
        const activeTabId =
          s.activeTabId === id ? (tabs[Math.min(idx, tabs.length - 1)]?.id ?? null) : s.activeTabId
        return { editorTabs: tabs, activeTabId }
      })
    },

    setActiveTab: (id) => set({ activeTabId: id }),

    closeTabsUnder: (path) => {
      set((s) => {
        const keep = (t: EditorTab) =>
          !(t.kind === 'file' && t.path && (t.path === path || t.path.startsWith(path + '\\') || t.path.startsWith(path + '/')))
        const tabs = s.editorTabs.filter(keep)
        if (tabs.length === s.editorTabs.length) return {}
        return {
          editorTabs: tabs,
          activeTabId: tabs.some((t) => t.id === s.activeTabId) ? s.activeTabId : (tabs[tabs.length - 1]?.id ?? null),
        }
      })
    },

    renameTabPath: (oldPath, newPath) => {
      set((s) => {
        let changed = false
        const tabs = s.editorTabs.map((t) => {
          if (t.kind !== 'file' || !t.path) return t
          if (t.path === oldPath) {
            changed = true
            return { ...t, id: 'file:' + newPath, path: newPath, title: baseNameOf(newPath) }
          }
          if (t.path.startsWith(oldPath + '\\') || t.path.startsWith(oldPath + '/')) {
            changed = true
            const p = newPath + t.path.slice(oldPath.length)
            return { ...t, id: 'file:' + p, path: p, title: baseNameOf(p) }
          }
          return t
        })
        if (!changed) return {}
        return {
          editorTabs: tabs,
          activeTabId: s.activeTabId === 'file:' + oldPath ? 'file:' + newPath : s.activeTabId,
        }
      })
    },

    bumpFsVersion: () => set((s) => ({ fsVersion: s.fsVersion + 1 })),

    sendMessage: (text: string, plan?: boolean, regenerate?: boolean, attachments?: { name: string; path: string; size?: number; desc?: string }[]) => {
      const convId = get().activeConvId
      if (!convId) return

      const prev = get().sessions[convId] ?? []
      const userMsg: Message = {
        id: uid(), role: 'user', content: text, ts: new Date().toISOString(),
        ...(attachments?.length ? { attachments } : {}),
      }
      const pending: Message = { id: uid(), role: 'assistant', content: '', pending: true, ts: new Date().toISOString() }
      // A fresh turn re-arms the browser panel's auto-open (an explicit close
      // only mutes within that turn). The terminal drawer stays muted — its
      // close means "stop popping this session" until a manual reopen.
      set((s) => ({
        sessions: { ...s.sessions, [convId]: [...prev, userMsg, pending] },
        browserAutoMuted: false,
      }))

      if (get().serveConnected && stream) {
        // Resolve the conversation's bound workspace workdir so serve threads it
        // into the agent as cwd (relative paths + terminal then land in the
        // workspace). Unbound conv → undefined → frame carries no cwd → serve
        // falls back to its process cwd (unchanged for 通用 conversations).
        const wsId = convId ? get().convWorkspace[convId] : undefined
        const workdir = wsId
          ? get().workspaces.find((w) => w.id === wsId)?.workdir
          : undefined
        stream.sendTurn(convId, text, workdir, plan || undefined, regenerate || undefined, attachments?.length ? attachments : undefined)
        // First send creates the session server-side → mark synced so a later
        // delete knows to delete it there (not just locally).
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === convId ? { ...c, synced: true } : c
          ),
        }))
        return
      }
      // Not connected to serve — no mock fallback. Mark the pending bubble as
      // an error so the user sees the message didn't go through.
      set((s) => ({
        sessions: {
          ...s.sessions,
          [convId]: (s.sessions[convId] ?? []).map((m) =>
            m.id === pending.id
              ? {
                  ...m,
                  content: '⚠️ xihe未连接，无法发送。请在「设置」页检查连接后重试。',
                  pending: false,
                  error: true,
                }
              : m
          ),
        },
      }))
    },

    // Live-sent messages carry no serve row id, so both rollback paths re-pull
    // the transcript before addressing the target server-side (indexes match
    // 1:1: the local list is either built from this same reshape or mirrors it).
    resendMessage: (index) => rollbackAndSend(index, null),

    editAndResendMessage: async (index, newText) => {
      const t = newText.trim()
      if (!t) return
      await rollbackAndSend(index, t)
    },

    regenerateMessage: async (index) => {
      // Same rollback as 重新发送, addressed at the prompt row: truncate AT
      // the user message (deleting it together with the reply), then re-send
      // its content — flagged so serve injects the "answer independently"
      // hint (industry regenerate semantics: the model must not see its own
      // earlier answer to this prompt as something to defer to).
      await rollbackAndSend(index - 1, null, { regenerate: true })
    },

    interrupt: () => {
      const convId = get().activeConvId
      if (!convId) return
      if (!stream) return
      stream.interrupt(convId)
      // Optimistic: show "正在停止…" right away so the click feels responsive.
      // Authoritative state arrives with `complete` (interrupted badge if the
      // turn really stopped, normal finalize if it finished first).
      patchPending(convId, (m) => ({ ...m, stopping: true }))
    },

    steer: (text) => {
      const convId = get().activeConvId
      if (!convId) return
      if (!stream) return
      stream.steer(convId, text)
      // serve injects the steer into the running turn silently (no WS event
      // back), so echo it into the turn's trace so the user sees their redirect
      // landed — in arrival order relative to tools/thoughts.
      patchPending(convId, (m) => {
        const trace = m.trace ? m.trace.slice() : []
        trace.push({ kind: 'steer', text })
        return { ...m, trace }
      })
    },

    approve: (id, approved, always) => {
      const convId = get().activeConvId
      if (!convId) return
      if (!stream) return
      stream.approve(convId, id, approved, always)
      // Optimistic settle so the buttons react instantly; the server's
      // approval_resolved for our own click is a no-op (status already set).
      patchPending(convId, (m) => {
        const ap = m.pendingApproval
        if (!ap || ap.id !== id || ap.status !== 'pending') return m
        return { ...m, pendingApproval: { ...ap, status: approved ? 'approved' : 'denied' } }
      })
    },

    answerClarify: (id, answer) => {
      const convId = get().activeConvId
      const text = answer.trim()
      if (!convId || !text) return
      if (!stream) return
      stream.answerClarify(convId, id, text)
      patchPending(convId, (m) => {
        const cl = m.pendingClarify
        if (!cl || cl.id !== id || cl.status !== 'pending') return m
        return { ...m, pendingClarify: { ...cl, status: 'answered', answer: text } }
      })
    },

    newConversation: () => {
      // Reuse the active conversation if it's still a blank, unsent "新对话" —
      // avoids a stack of identical empty entries from repeated clicks.
      const cur = get().conversations.find((c) => c.id === get().activeConvId)
      if (cur && !cur.synced && (get().sessions[cur.id] ?? []).length === 0) return
      const convId = 'desktop-' + uid()
      set((s) => ({
        conversations: [{ id: convId, title: '新对话' }, ...s.conversations],
        activeConvId: convId,
        sessions: { ...s.sessions, [convId]: [] },
      }))
      trimSessions()
    },

    selectConversation: (convId) => {
      set({ activeConvId: convId })
      // First open → full load. Already cached → render the cache immediately,
      // then silently refetch in the background (cron pushes / cross-window
      // changes land in DB while the user is elsewhere). refetchConv's streaming
      // guard skips the replace for conversations mid-turn.
      if (convId in get().sessions) {
        void refetchConv(convId)
      } else {
        void loadActiveHistory()
      }
    },

    resetConversation: async (convId) => {
      // Connected: reset server-side — a fresh context round under the same
      // conv. The transcript spans rounds server-side, so reload it (the
      // pre-reset history stays visible); the title is conversation-scoped
      // and survives the round switch. Offline: nothing server-backed to keep.
      if (!get().serveConnected) {
        set((s) => ({ sessions: { ...s.sessions, [convId]: [] } }))
        return
      }
      const ok = await resetSession(convId)
      if (!ok) return
      await refetchConv(convId)
    },

    deleteConversation: async (convId) => {
      const wasActive = get().activeConvId === convId
      // If the live conv has a running turn, stop it first — otherwise the
      // turn keeps persisting and get_or_create_session would resurrect the
      // session we just deleted on its next write.
      if (wasActive) {
        const list = get().sessions[convId] ?? []
        if (list.some((m) => m.role === 'assistant' && m.pending) && stream) {
          stream.interrupt(convId)
        }
      }
      const conv = get().conversations.find((c) => c.id === convId)
      // Connected AND already on the server (sent once / seen in /sessions)
      // → delete server-side, bail on real failure. An unsent "新对话" never
      // reached serve, so remove it locally without a round-trip — otherwise
      // DELETE returns deleted:false and the entry gets stuck undeletable.
      if (get().serveConnected && conv?.synced) {
        const ok = await deleteSession(convId)
        if (!ok) return
      }
      set((s) => {
        const remaining = s.conversations.filter((c) => c.id !== convId)
        const sessions = { ...s.sessions }
        delete sessions[convId]
        // Deleted the active one → fall back to the next conversation, or null
        // when that was the last one (the chat panel then shows its "start new
        // chat" empty state instead of forcing a phantom 新对话 back into
        // existence — the undeletable-conversation bug).
        const activeConvId =
          convId === s.activeConvId
            ? remaining.length > 0
              ? remaining[0].id
              : null
            : s.activeConvId
        return { sessions, conversations: remaining, activeConvId }
      })
      // Deleted the active conversation → the fallback selection was never
      // opened this session; lazy-load its history like selectConversation
      // does, else it renders blank until manually re-selected.
      if (wasActive) void loadActiveHistory()
    },

    renameConversation: async (convId, title) => {
      const t = title.trim()
      if (!t) return
      const conv = get().conversations.find((c) => c.id === convId)
      // Connected AND already on the server → persist the rename there.
      // xihe's auto-title skips sessions that already have a title, so once this
      // lands the manual rename won't be overwritten. An unsent "新对话" has no
      // session yet → skip the POST (it 404s) and keep the name locally; its
      // first send creates the session and may auto-title over it (edge case).
      if (get().serveConnected && conv?.synced) {
        const ok = await setConversationTitle(convId, t)
        if (!ok) return
      }
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === convId ? { ...c, title: t } : c
        ),
      }))
    },

    // Reload the active conversation from serve: re-pull its transcript (so the
  // chat reflects server truth, e.g. after an external edit) AND refresh the
  // conversation list (which is how serve-generated titles sync back). Skips
  // the message reload while a turn is streaming — it would drop the in-flight
  // assistant bubble whose deltas are still arriving.
  refreshConversation: async (convId) => {
    if (!get().serveConnected) return
    // Target the given conversation (per-conv refresh button) or fall back to
    // the active one (top refresh button).
    const target = convId ?? get().activeConvId
    if (!target) return
    // Open the target if it isn't already active. selectConversation leaves the
    // workspace view intact (it doesn't touch activeWorkspaceId), so refreshing
    // a conv from inside a workspace doesn't kick you out of it.
    if (target !== get().activeConvId) get().selectConversation(target)
    // Force-reload its transcript from serve (overrides the lazy-load cache);
    // refetchConv skips while a turn is streaming.
    await refetchConv(target)
    // Refresh the list too — this is how serve-generated titles sync back.
    await syncConversations()
  },

    setTab: (tab) => {
      set({ activeTab: tab })
      // Returning to the chat tab: catch up any deltas buffered while hidden.
      if (tab === 'chat') flushDeltas()
    },

    // Opening (by click or auto) re-arms auto-open; closing mutes it for the
    // rest of the current turn so a running agent's later browser calls don't
    // fight the user's explicit close.
    setShowBrowser: (v) =>
      set(
        v
          ? { showBrowser: true, browserAutoMuted: false }
          : { showBrowser: false, browserAutoMuted: true }
      ),

    dismissBrowserPanel: () => set({ showBrowser: false }),

    // Terminal drawer: pure user toggle — nothing auto-opens it.
    setShowTerminal: (v) => set({ showTerminal: v }),
    clearAgentTermTarget: () => set({ agentTermTarget: null }),

    // Shell drawer: pure user toggle (no agent-driven auto-open).
    // openShellPanel with a cmd runs it in a new shell tab (Play entry point).
    setShowShell: (v) => set({ showShell: v }),
    openShellPanel: (cmd) =>
      set({ showShell: true, shellCmdDraft: cmd != null ? cmd : null }),
    clearShellCmdDraft: () => set({ shellCmdDraft: null }),

    addWorkspace: (workdir, name) => {
      // Default name = last path segment (pure JS; the renderer can't use
      // node's `path`). User-supplied name wins if non-empty.
      const seg = workdir.split(/[\\/]/).filter(Boolean).pop()
      const ws: Workspace = {
        id: 'ws-' + uid(),
        name: name?.trim() || seg || workdir,
        workdir,
      }
      set((s) => ({ workspaces: [...s.workspaces, ws] }))
      persistWs()
    },

    removeWorkspace: (id) => {
      set((s) => {
        const workspaces = s.workspaces.filter((w) => w.id !== id)
        // Drop bindings to the removed workspace; bound conversations are NOT
        // deleted — they fall back to 通用 (the selector returns undefined →
        // chip/tree vanish on their own).
        const convWorkspace: Record<string, string> = {}
        for (const [cid, wid] of Object.entries(s.convWorkspace)) {
          if (wid !== id) convWorkspace[cid] = wid
        }
        return { workspaces, convWorkspace }
      })
      persistWs()
    },

    // Advanced path: always open a FRESH conversation bound to this workspace,
    // and land on the chat tab. Unlike newConversation it always binds — this
    // is the workspace fast path (also the "新对话" button inside workspace view).
    openConversationInWorkspace: (workspaceId) => {
      if (!get().workspaces.some((w) => w.id === workspaceId)) return
      // Reuse the active conv if it's still a blank, unsent "新对话" already
      // bound to this workspace — avoids a stack of empty entries from repeated
      // clicks in workspace view.
      const activeConvId = get().activeConvId
      const cur = get().conversations.find((c) => c.id === activeConvId)
      if (
        cur &&
        !cur.synced &&
        (get().sessions[cur.id] ?? []).length === 0 &&
        get().convWorkspace[cur.id] === workspaceId
      ) {
        return
      }
      const convId = 'desktop-' + uid()
      set((s) => ({
        activeTab: 'chat',
        convWorkspace: { ...s.convWorkspace, [convId]: workspaceId },
        sessions: { ...s.sessions, [convId]: [] },
        conversations: [{ id: convId, title: '新对话' }, ...s.conversations],
        activeConvId: convId,
      }))
      persistWs()
    },

    // Enter a workspace: the sidebar becomes scoped to this workspace's
    // conversations (left = history), the active conv drives the chat (middle)
    // and its binding drives the tree (right). Select the most recent bound
    // conversation; if there is none yet, seed a fresh bound one so the middle
    // pane isn't empty.
    openWorkspace: (workspaceId) => {
      if (!get().workspaces.some((w) => w.id === workspaceId)) return
      const bound = get().conversations.filter(
        (c) => get().convWorkspace[c.id] === workspaceId
      )
      if (bound.length > 0) {
        set({
          activeWorkspaceId: workspaceId,
          activeTab: 'chat',
          activeConvId: bound[0].id,
        })
      } else {
        // No history yet — seed a blank bound conversation.
        get().openConversationInWorkspace(workspaceId)
        set({ activeWorkspaceId: workspaceId })
      }
      void loadActiveHistory()
    },

    exitWorkspace: () => set({ activeWorkspaceId: null }),

    // Load ~/.xihe-desktop/workspaces.json into state. Called once on app mount
    // (file IO is async, so the store seeds empty and fills shortly after).
    // Also restores this window's last view: the workspace the sidebar was
    // scoped to, the conversation that was open, and each workspace's file
    // tabs (seeded as buckets; the swap subscription mounts the right one).
    hydrateWorkspaceStore: async () => {
      const data = await loadWorkspaceStore()
      const win = data.windows.find((w) => w.id === WINDOW_ID) ?? data.windows[0]
      restoredConvId = win?.convId ?? null
      const seeded: typeof tabBuckets = {}
      for (const [k, rec] of Object.entries(win?.tabsByWorkspace ?? {})) {
        const tabs: EditorTab[] = rec.tabs.map((p) => ({
          id: 'file:' + p,
          kind: 'file',
          title: baseNameOf(p),
          path: p,
        }))
        seeded[k] = {
          tabs,
          active:
            rec.active != null && tabs.some((t) => t.id === 'file:' + rec.active)
              ? 'file:' + rec.active
              : (tabs[tabs.length - 1]?.id ?? null),
        }
      }
      tabBuckets = seeded
      const wsId = win?.workspaceId ?? null
      const wsOk = wsId != null && data.workspaces.some((w) => w.id === wsId)
      set({
        workspaces: data.workspaces,
        convWorkspace: data.convWorkspace,
        activeWorkspaceId: wsOk ? wsId : null,
      })
      // Remount the current key's bucket: the swap subscription may have
      // mounted an empty one already when conversations synced before this
      // file read landed (its buckets weren't seeded yet).
      const cur = tabBuckets[tabKeyOf(get())] ?? { tabs: [], active: null }
      useStore.setState({ editorTabs: cur.tabs, activeTabId: cur.active })
      // Race: connectServe's sync may have ALREADY picked a conversation (the
      // auto-pick couldn't see restoredConvId yet) — re-apply the restored one
      // on top. Conversations still empty → leave the preference for pickActive.
      const convs = get().conversations
      if (restoredConvId && convs.some((c) => c.id === restoredConvId)) {
        if (get().activeConvId !== restoredConvId) {
          const convId = restoredConvId
          set({ activeConvId: convId })
          void loadActiveHistory()
        }
        restoredConvId = null
      } else if (convs.length > 0) {
        // Synced, but the restored conv is gone (deleted elsewhere / was an
        // unsent local 新对话) — the auto-pick stands.
        restoredConvId = null
      }
      wsHydrated = true
    },

    hydrateXiheConfig: async () => {
      const xiheConfig = await desktop.xiheConfigLoad()
      set({ xiheConfig })
    },

    // Patch config.yaml then re-read effective values (no secret in state —
    // api_key returns only as api_key_set). Edits apply to the RUNNING serve
    // only after serveRestart() (serve reads config at boot).
    saveXiheConfig: async (patch) => {
      const ok = await desktop.xiheConfigSave(patch)
      if (ok) {
        const xiheConfig = await desktop.xiheConfigLoad()
        set({ xiheConfig })
      }
      return ok
    },

    loadManageData: async () => {
      // No-op when serve isn't up — the panel shows empty + the effect re-fires
      // once serveConnected flips true (App calls connectServe on mount).
      if (!get().serveConnected) return
      const [mcpServers, skills, cron] = await Promise.all([
        listMcp(),
        listSkills(),
        listCron(),
      ])
      set({
        mcpServers,
        skills,
        cronJobs: cron.jobs,
        schedulerHealth: cron.scheduler,
      })
    },

    loadTrace: async (convId, msgId) => {
      // No-op if the message is gone or already has a trace (re-expand guard).
      const list = get().sessions[convId] ?? []
      const idx = list.findIndex((m) => m.traceAnchor === msgId)
      if (idx < 0 || list[idx].trace) return
      const trace = await getTrace(convId, msgId)
      set((s) => {
        const cur = s.sessions[convId] ?? []
        const i = cur.findIndex((m) => m.traceAnchor === msgId)
        if (i < 0 || cur[i].trace) return {} // raced / already loaded
        const next = cur.slice()
        next[i] = { ...next[i], trace: trace as TraceEvent[] }
        return { sessions: { ...s.sessions, [convId]: next } }
      })
    },

    applyXiheStatus: (s) => {
      set({ xiheStatus: s })
      if (s.state === 'running') {
        // main reports serve up → drive the (idempotent) streaming connect.
        // Skip when already connected (App-mount connectServe may have got there
        // first) to avoid opening a second WS.
        if (!get().serveConnected) void get().connectServe()
      } else {
        // starting | stopped | errored → not streaming. Drop serveConnected at
        // once (don't wait for the socket to notice) and arm deliberateClose so
        // any pending reconnect no-ops while main restarts; connectServe clears
        // it when `running` returns.
        if (get().serveConnected) set({ serveConnected: false })
        deliberateClose = true
      }
    },

    connectServe: async () => {
      // Idempotent vs concurrent re-entry (xihe:status pull+push + App mount).
      if (connectInFlight || get().serveConnected) return get().serveConnected
      connectInFlight = true
      try {
        const health = await getHealth()
        if (!health) {
          set({ serveConnected: false })
          return false
        }
        deliberateClose = false
        set({
          serveConnected: true,
          serveVersion: health.version,
          serveModel: health.model,
        })
        void openStream()
        // Sync the conversation list first (it may auto-select a conv when
        // none is active), THEN load that conv's history — firing both
        // concurrently makes load bail on the pre-sync null activeConvId and
        // leaves the just-selected conv empty.
        void syncConversations().then(() => loadActiveHistory())
        return true
      } finally {
        connectInFlight = false
      }
    },
  }
})

/** Workspace-scoped editor tabs: when the active conversation's workspace
 *  binding changes, park the open tabs in the outgoing bucket and mount the
 *  incoming one — switching workspaces swaps the open files along with the
 *  tree. Registered BEFORE the persist writer so buckets are current when the
 *  record is built. */
let mountedTabKey: string | null = null
useStore.subscribe((s, prev) => {
  const key = tabKeyOf(s)
  if (key !== mountedTabKey) {
    const parked = mountedTabKey
    mountedTabKey = key
    // The set that moved the key didn't touch tabs — s.editorTabs are still
    // the outgoing set. Park them, then mount the incoming bucket.
    if (parked != null) tabBuckets[parked] = { tabs: s.editorTabs, active: s.activeTabId }
    const mount = tabBuckets[key] ?? { tabs: [], active: null }
    tabBuckets[key] = mount
    useStore.setState({ editorTabs: mount.tabs, activeTabId: mount.active })
    return
  }
  if (s.editorTabs !== prev.editorTabs || s.activeTabId !== prev.activeTabId) {
    tabBuckets[key] = { tabs: s.editorTabs, active: s.activeTabId }
  }
})

/** Persist this window's view state to workspaces.json, debounced: workspace
 *  view + open conversation + editor tabs (one `windows` record — a pure
 *  function of state, see windowRecordOf). Workspace/binding mutations persist
 *  immediately via persistWs; this subscription covers view/tab changes,
 *  which no action hook owns. Gated on hydration so boot-time defaults never
 *  clobber a file not yet read. */
let viewPersistTimer: ReturnType<typeof setTimeout> | null = null
useStore.subscribe((s, prev) => {
  if (!wsHydrated) return
  const conv = s.activeConvId
  const prevConv = prev.activeConvId
  if (
    conv === prevConv &&
    s.activeWorkspaceId === prev.activeWorkspaceId &&
    s.editorTabs === prev.editorTabs &&
    s.activeTabId === prev.activeTabId
  )
    return
  if (viewPersistTimer) clearTimeout(viewPersistTimer)
  viewPersistTimer = setTimeout(() => {
    const st = useStore.getState()
    void saveWorkspaceStore({
      workspaces: st.workspaces,
      convWorkspace: st.convWorkspace,
      windows: [windowRecordOf(st)],
    })
  }, 400)
})

/** Mark any still-running tool entries when a turn ends — an interrupt can
 * leave a tool mid-flight with no matching tool_result (avoids stuck spinners).
 * `to` controls the final status: 'done' for a normal/errored end, 'interrupted'
 * when the user stopped the turn (so the tool reads as cancelled, not ✓).
 * Returns the same array reference if nothing changed. */
function sweepRunningTools(trace?: TraceEvent[], to: 'done' | 'interrupted' = 'done'): TraceEvent[] | undefined {
  if (!trace || !trace.length) return trace
  let changed = false
  const next = trace.map((t) => {
    if (t.kind === 'tool' && t.status === 'running') {
      changed = true
      return { ...t, status: to }
    }
    return t
  })
  return changed ? next : trace
}

/** A pending approval outliving its turn (complete/error with no
 *  approval_resolved — e.g. the socket dropped) reads as unanswered. */
function expireApproval(ap?: PendingApproval): PendingApproval | undefined {
  if (!ap || ap.status !== 'pending') return ap
  return { ...ap, status: 'expired' }
}

/** Same contract for a pending clarify. */
function expireClarify(cl?: PendingClarify): PendingClarify | undefined {
  if (!cl || cl.status !== 'pending') return cl
  return { ...cl, status: 'expired' }
}

function uid(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
}

function baseNameOf(p: string): string {
  return p.split(/[\\/]/).filter(Boolean).pop() ?? p
}
