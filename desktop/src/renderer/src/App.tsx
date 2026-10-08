import { useEffect, useRef, useState, type ReactNode } from 'react'
import { BookOpen, CodeXml, FolderTree, Globe, MessageSquare, PanelLeftOpen, PanelRightClose, PanelRightOpen, Settings, ShoppingBag, SquareTerminal, Terminal } from 'lucide-react'
import { useStore } from './appStore'
import { Sidebar } from './components/Sidebar'
import { ChatPanel } from './components/ChatPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { StorePage } from './components/StorePage'
import { KnowledgePage } from './components/KnowledgePage'
import { FileTree, FileTreePanel } from './components/FileTreePanel'
import { EditorArea } from './components/EditorArea'
import { BrowserPanel } from './components/BrowserPanel'
import { TerminalPanel } from './components/TerminalPanel'
import { ShellPanel } from './components/ShellPanel'
import { FileOverlay } from './components/FileOverlay'
import { FindBar } from './components/FindBar'
import { Resizer, usePanelSize } from './components/Resizer'
import { cn } from './lib/cn'
import { desktop } from './lib/desktop'

/** Re-opener strip left in place of a collapsed panel — keeps the restore
 *  affordance at the edge the panel occupied. */
function EdgeStrip({ side, title, onClick, children }: {
  side: 'left' | 'right'
  title: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cn(
        'flex w-9 shrink-0 flex-col items-center gap-2 pt-3 text-ink-4 transition hover:bg-elevated hover:text-ink',
        side === 'left' ? 'border-r border-line' : 'border-l border-line'
      )}
    >
      {children}
    </button>
  )
}

export default function App() {
  const connectServe = useStore((s) => s.connectServe)
  const hydrateWorkspaceStore = useStore((s) => s.hydrateWorkspaceStore)
  const hydrateXiheConfig = useStore((s) => s.hydrateXiheConfig)
  const activeTab = useStore((s) => s.activeTab)
  const setTab = useStore((s) => s.setTab)
  const serveModel = useStore((s) => s.serveModel)
  const xiheConfig = useStore((s) => s.xiheConfig)
  const modelLabel = serveModel || xiheConfig.model
  const convWorkspace = useStore((s) => s.convWorkspace)
  const workspaces = useStore((s) => s.workspaces)
  // Panel-collapse states (session-local, like showBrowser): the panel folds
  // to an EdgeStrip instead of vanishing, so restore is in place.
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [rightTreeCollapsed, setRightTreeCollapsed] = useState(false)
  const [chatCollapsed, setChatCollapsed] = useState(false)
  const showBrowser = useStore((s) => s.showBrowser)
  const setShowBrowser = useStore((s) => s.setShowBrowser)
  const showTerminal = useStore((s) => s.showTerminal)
  const setShowTerminal = useStore((s) => s.setShowTerminal)
  const showShell = useStore((s) => s.showShell)
  const setShowShell = useStore((s) => s.setShowShell)
  const openShellPanel = useStore((s) => s.openShellPanel)
  const layoutMode = useStore((s) => s.layoutMode)
  const setLayoutMode = useStore((s) => s.setLayoutMode)
  const editorTabs = useStore((s) => s.editorTabs)
  const activeEditorTabId = useStore((s) => s.activeTabId)
  const openFileTab = useStore((s) => s.openFileTab)
  const closeTabsUnder = useStore((s) => s.closeTabsUnder)
  const renameTabPath = useStore((s) => s.renameTabPath)
  // Workbench chat column width (drag its left edge). The trees and drawers
  // own their own sizes internally (FileTree / TerminalPanel / ShellPanel).
  const [chatW, setChatW] = usePanelSize('chatPanelWidth', 460, 360)
  const chatColRef = useRef<HTMLDivElement>(null)
  // A turn is streaming in the active conversation — cues the collapsed
  // workbench chat strip so replies aren't silently missed.
  const chatBusy = useStore((s) => {
    const convId = s.activeConvId
    return !!convId && (s.sessions[convId] ?? []).some((m) => m.role === 'assistant' && m.pending)
  })

  // Path of the active FILE tab (drives the tree's selected highlight in
  // workbench mode). Diff tabs don't highlight anything.
  const activeEditorTab = editorTabs.find((t) => t.id === activeEditorTabId) ?? null
  const activeEditorPath =
    activeEditorTab?.kind === 'file' && activeEditorTab.path ? activeEditorTab.path : null

  // Fallback: if main's xihe:status push never arrives, this /health probe still connects.
  useEffect(() => {
    void connectServe()
  }, [connectServe])

  // Dev only: monaco sits outside the dep optimizer (worker imports), so its
  // first load streams hundreds of source modules. Warm it shortly after
  // startup so the FIRST file open doesn't pay that. Prod keeps the lazy
  // chunk — nothing loads until an editor is actually opened.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const t = setTimeout(() => {
      void import('./components/monaco/env').then((m) => m.ensureMonaco())
    }, 1500)
    return () => clearTimeout(t)
  }, [])

  // xihe:status — pull a snapshot on mount (an early `running` push can be
  // dropped before did-finish-load), then subscribe to pushes.
  const applyXiheStatus = useStore((s) => s.applyXiheStatus)
  useEffect(() => {
    let alive = true
    void desktop.getXiheStatus().then((s) => {
      if (alive && s) applyXiheStatus(s)
    })
    const off = desktop.onXiheStatus(applyXiheStatus)
    return () => {
      alive = false
      off()
    }
  }, [applyXiheStatus])

  useEffect(() => {
    void hydrateWorkspaceStore()
  }, [hydrateWorkspaceStore])

  useEffect(() => {
    void hydrateXiheConfig()
  }, [hydrateXiheConfig])

  const theme = useStore((s) => s.theme)
  const hydrateTheme = useStore((s) => s.hydrateTheme)
  useEffect(() => {
    void hydrateTheme()
  }, [hydrateTheme])

  // <html data-theme> drives every semantic color token (index.css — one
  // block per appearance). system mode follows the OS: Electron mirrors
  // nativeTheme into prefers-color-scheme, so the media listener re-resolves
  // the attribute without a store change.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && mq.matches)
      document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    }
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [theme])

  // Workspace bound to the active conversation. Derived here (not stored on
  // ConvMeta) so it survives syncConversations rebuilds — the binding lives
  // only in the convWorkspace map.
  const activeConvId = useStore((s) => s.activeConvId)
  const boundWsId = activeConvId ? convWorkspace[activeConvId] : undefined
  const activeWs = boundWsId ? workspaces.find((w) => w.id === boundWsId) : undefined
  // Workbench needs a bound workspace (its tree/editor act on it). Without a
  // binding the chat layout renders even while the preference says workbench,
  // and the layout toggle itself is hidden.
  const inWorkbench = !!activeWs && layoutMode === 'workbench'
  const dismissBrowserPanel = useStore((s) => s.dismissBrowserPanel)
  const bindConversationWorkspace = useStore((s) => s.bindConversationWorkspace)
  // Workspace-picker popover for UNBOUND conversations: the two
  // workspace-gated header buttons (workbench toggle, file tree) open it —
  // "先绑定工作空间" became an entry point, not a dead end.
  const [wsPickerOpen, setWsPickerOpen] = useState(false)
  // Click-outside close: a single document listener while open.
  useEffect(() => {
    if (!wsPickerOpen) return
    const close = () => setWsPickerOpen(false)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [wsPickerOpen])
  // Full-content-area file overlay (non-workbench file tree's "expand").
  const [overlayFile, setOverlayFile] = useState<string | null>(null)

  // The browser panel belongs to the conversation that opened it — switching
  // conversations hides the Chrome first (main), then closes the panel without
  // muting auto-open, so a browser tool in the new conversation still pops it.
  // Guarded on showBrowser: at boot (and whenever the panel is closed) this
  // effect's first run must not fire a hide at a serve that may not be up yet.
  // showBrowser is read as a snapshot on purpose — not a dep.
  useEffect(() => {
    if (!showBrowser) return
    void desktop.setBrowserPanelActive(false)
    dismissBrowserPanel()
  }, [activeConvId, dismissBrowserPanel])

  // Settings and the store are full-window pages (own Header + back arrow).
  // The chat layout stays MOUNTED underneath — unmounting it would tear down
  // the browser panel and release the snapped Chrome; instead it hides, and
  // the panel drives Chrome hide/show through its `active` prop.
  return (
    <>
      <div className={cn('flex h-screen flex-col bg-app text-ink', activeTab !== 'chat' && 'hidden')}>
          <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-3">
              {modelLabel && (
                <div
                  className="max-w-[16rem] truncate text-xs text-ink-3"
                  title={modelLabel}
                >
                  {modelLabel}
                </div>
              )}
              <div className="ml-auto flex items-center gap-1">
                {activeWs ? (
                  <button
                    onClick={() => void setLayoutMode(inWorkbench ? 'chat' : 'workbench')}
                    title="工作台布局（文件树 + 编辑器为中心）"
                    className={cn(
                      'rounded-lg p-1.5 transition',
                      inWorkbench
                        ? 'bg-contrast text-sky-300'
                        : 'text-ink-3 hover:bg-elevated hover:text-ink'
                    )}
                  >
                    <CodeXml className="h-4 w-4" />
                  </button>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); setWsPickerOpen((v) => !v) }}
                    title="绑定工作空间后可用 — 点击选择"
                    className={cn(
                      'rounded-lg p-1.5 transition text-ink-3 hover:bg-elevated hover:text-ink',
                      wsPickerOpen && 'bg-elevated text-ink'
                    )}
                  >
                    <CodeXml className="h-4 w-4" />
                  </button>
                )}
                {activeWs ? (
                  <button
                    onClick={() =>
                      inWorkbench
                        ? setTreeCollapsed((v) => !v)
                        : setRightTreeCollapsed((v) => !v)
                    }
                    title="显示/隐藏文件树"
                    className={cn(
                      'rounded-lg p-1.5 transition',
                      inWorkbench ? !treeCollapsed : !rightTreeCollapsed
                        ? 'bg-contrast text-sky-300'
                        : 'text-ink-3 hover:bg-elevated hover:text-ink'
                    )}
                  >
                    <FolderTree className="h-4 w-4" />
                  </button>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); setWsPickerOpen((v) => !v) }}
                    title="绑定工作空间后可用 — 点击选择"
                    className={cn(
                      'rounded-lg p-1.5 transition text-ink-3 hover:bg-elevated hover:text-ink',
                      wsPickerOpen && 'bg-elevated text-ink'
                    )}
                  >
                    <FolderTree className="h-4 w-4" />
                  </button>
                )}
                {wsPickerOpen && (
                  <div
                    className="absolute right-0 top-full z-40 mt-1 w-72 rounded-lg border border-line bg-elevated p-1.5 shadow-lg"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-ink-5">
                      绑定工作空间 — 当前对话
                    </div>
                    {workspaces.length === 0 && (
                      <div className="px-2 py-2 text-xs text-ink-4">
                        尚无工作空间。在左侧边栏创建后即可绑定。
                      </div>
                    )}
                    {workspaces.map((w) => (
                      <button
                        key={w.id}
                        onClick={() => {
                          if (activeConvId) {
                            bindConversationWorkspace(activeConvId, w.id)
                            void setLayoutMode('workbench')
                          }
                          setWsPickerOpen(false)
                        }}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-app"
                      >
                        <FolderTree className="h-3.5 w-3.5 shrink-0 text-ink-4" />
                        <span className="min-w-0 flex-1 truncate text-xs text-ink-2">{w.name}</span>
                        <span className="min-w-0 max-w-[45%] truncate text-[10px] text-ink-5">{w.workdir}</span>
                      </button>
                    ))}
                    {activeWs && (
                      <button
                        onClick={() => {
                          if (activeConvId) bindConversationWorkspace(activeConvId, null)
                          setWsPickerOpen(false)
                        }}
                        className="mt-0.5 flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs text-ink-4 hover:bg-app hover:text-ink-2"
                      >
                        取消绑定（变回通用对话）
                      </button>
                    )}
                  </div>
                )}
                <button
                  onClick={() => setShowBrowser(!showBrowser)}
                  title="浏览器面板（agent 的 Chrome 吸附于此）"
                  className={cn(
                    'rounded-lg p-1.5 transition',
                    showBrowser
                      ? 'bg-contrast text-sky-300'
                      : 'text-ink-3 hover:bg-elevated hover:text-ink'
                  )}
                >
                  <Globe className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setShowTerminal(!showTerminal)}
                  title="agent终端（对话的命令与 SSH 会话实时输出）"
                  className={cn(
                    'rounded-lg p-1.5 transition',
                    showTerminal
                      ? 'bg-contrast text-sky-300'
                      : 'text-ink-3 hover:bg-elevated hover:text-ink'
                  )}
                >
                  <SquareTerminal className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setShowShell(!showShell)}
                  title="本地终端（多开交互式 shell，与对话无关）"
                  className={cn(
                    'rounded-lg p-1.5 transition',
                    showShell
                      ? 'bg-contrast text-sky-300'
                      : 'text-ink-3 hover:bg-elevated hover:text-ink'
                  )}
                >
                  <Terminal className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setTab('store')}
                  title="商店"
                  className="rounded-lg p-1.5 text-ink-3 transition hover:bg-elevated hover:text-ink"
                >
                  <ShoppingBag className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setTab('knowledge')}
                  title="记忆 / 知识库"
                  className="rounded-lg p-1.5 text-ink-3 transition hover:bg-elevated hover:text-ink"
                >
                  <BookOpen className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setTab('manage')}
                  title="设置"
                  className="rounded-lg p-1.5 text-ink-3 transition hover:bg-elevated hover:text-ink"
                >
                  <Settings className="h-4 w-4" />
                </button>
              </div>
            </header>
            <div className="flex min-h-0 flex-1">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex h-full min-h-0 flex-1">
                {inWorkbench ? (
                  <>
                    {treeCollapsed ? (
                      <EdgeStrip
                        side="left"
                        title="展开文件树"
                        onClick={() => setTreeCollapsed(false)}
                      >
                        <PanelLeftOpen className="h-4 w-4" />
                        <FolderTree className="h-3.5 w-3.5" />
                      </EdgeStrip>
                    ) : (
                      <FileTree
                        workspace={activeWs}
                        className="shrink-0 border-r border-line"
                        selectedPath={activeEditorPath}
                        side="left"
                        onCollapse={() => setTreeCollapsed(true)}
                        onRunFile={(c) => openShellPanel(c)}
                        onSelectFile={(e) => openFileTab(e.path)}
                        onPathRenamed={renameTabPath}
                        onPathDeleted={closeTabsUnder}
                      />
                    )}
                    <EditorArea className="min-w-0 flex-1" />
                    {chatCollapsed ? (
                      <EdgeStrip
                        side="right"
                        title="展开对话"
                        onClick={() => setChatCollapsed(false)}
                      >
                        <PanelRightOpen className="h-4 w-4" />
                        <MessageSquare className="h-3.5 w-3.5" />
                        {chatBusy && (
                          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" />
                        )}
                      </EdgeStrip>
                    ) : (
                      <div
                        ref={chatColRef}
                        className="relative shrink-0 border-l border-line"
                        style={{ width: chatW }}
                      >
                        <Resizer
                          axis="col"
                          title="拖动调整面板宽度"
                          onMove={(x) => {
                            const el = chatColRef.current
                            if (!el) return
                            const w = el.getBoundingClientRect().right - x
                            const max = Math.max(window.innerWidth - 420, 360)
                            setChatW(Math.min(Math.max(w, 360), max))
                          }}
                          className="absolute left-0 top-0 h-full w-1.5 -translate-x-1/2"
                        />
                        <button
                          onClick={() => setChatCollapsed(true)}
                          title="折叠对话面板"
                          className="absolute left-0 top-2 z-10 rounded p-1 text-ink-4 opacity-50 transition hover:bg-elevated hover:text-ink hover:opacity-100"
                        >
                          <PanelRightClose className="h-3.5 w-3.5" />
                        </button>
                        <ChatPanel />
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div className="min-w-0 flex-1">
                      <ChatPanel />
                    </div>
                    {activeWs && !rightTreeCollapsed && (
                      <FileTreePanel
                        workspace={activeWs}
                        className="shrink-0 border-l border-line"
                        onCollapse={() => setRightTreeCollapsed(true)}
                        onRunFile={(c) => openShellPanel(c)}
                        onExpandFile={(p) => setOverlayFile(p)}
                      />
                    )}
                    {activeWs && rightTreeCollapsed && (
                      <EdgeStrip
                        side="right"
                        title="展开文件树"
                        onClick={() => setRightTreeCollapsed(false)}
                      >
                        <PanelRightOpen className="h-4 w-4" />
                        <FolderTree className="h-3.5 w-3.5" />
                      </EdgeStrip>
                    )}
                  </>
                )}
                {showBrowser && (
                  <BrowserPanel
                    active={activeTab === 'chat'}
                    className="shrink-0 border-l border-line"
                  />
                )}
              </div>
              {/* The terminal drawer spans the full content width — sessions
                  are cross-conversation (serve-process globals), unlike the
                  browser panel which belongs to the active conversation. */}
              {showTerminal && (
                <TerminalPanel className="shrink-0 border-t border-line" />
              )}
              {/* Both bottom drawers are window-level docks spanning the full
                  content width — neither belongs to the chat column nor the
                  editor area: the agent panel follows cross-conversation
                  serve channels, the shell panel is the user's own
                  conversation/workspace-independent terminals. */}
              {showShell && <ShellPanel className="shrink-0 border-t border-line" />}
            </div>
      </main>
      </div>
      </div>
      {activeTab === 'manage' && <SettingsPanel onBack={() => setTab('chat')} />}
      {activeTab === 'store' && <StorePage onBack={() => setTab('chat')} />}
      {activeTab === 'knowledge' && <KnowledgePage onBack={() => setTab('chat')} />}
      {overlayFile && <FileOverlay path={overlayFile} onClose={() => setOverlayFile(null)} />}
      <FindBar />
    </>
  )
}
