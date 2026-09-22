import { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Check,
  Copy,
  Eye,
  FileText,
  Navigation,
  Paperclip,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Map,
  RotateCcw,
  Send,
  Settings,
  ShieldAlert,
  Square,
  X,
} from 'lucide-react'
import { useStore, type Message, type PendingApproval, type PendingClarify } from '../appStore'
import { cn } from '../lib/cn'
import { desktop } from '../lib/desktop'
import { detectLanguage } from '../lib/lang'
import { copyText } from '../lib/clipboard'
import { localImgUrl } from '../lib/localImage'
import { TurnTrace } from './TurnTrace'
import { Markdown } from './Markdown'
import logoUrl from '../assets/logo.png'

/** 空态品牌图（assets/logo.png 由 docs/_make_icons.py 从 docs/banner.jpg 生成）。 */
function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src={logoUrl}
      alt="xihe"
      draggable={false}
      className={cn('rounded-xl object-cover', className)}
    />
  )
}

// Monaco diff is heavy — pulled only when a preview is actually opened.
const MonacoDiffPane = lazy(() =>
  import('./monaco/MonacoDiffPane').then((m) => ({ default: m.MonacoDiffPane }))
)

/** 1234 → "1.2k"; under 1000 stays plain digits. */
function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** Icon button revealed on row hover (user-bubble actions). */
function HoverIconBtn({ title, disabled, onClick, children }: {
  title: string
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="mb-1 flex h-6 w-6 items-center justify-center rounded-md text-ink-4 opacity-0 hover:bg-elevated hover:text-ink-2 disabled:opacity-0 group-hover:opacity-100 disabled:group-hover:opacity-30"
    >
      {children}
    </button>
  )
}

function UsageBadge({ usage }: { usage: Message['usage'] }) {
  if (!usage) return null
  const title = `输入 ${usage.prompt} / 输出 ${usage.completion} tokens（${usage.calls ?? '?'} 次调用）`
  return (
    <div
      title={title}
      className="w-fit select-none rounded-md bg-elevated/60 px-1.5 py-0.5 text-[10px] text-ink-4"
    >
      ↑ {fmtTokens(usage.prompt)} ↓ {fmtTokens(usage.completion)}
    </div>
  )
}

/** First-run card — replaces the chat empty state while the model connection
 *  is unconfigured (api_key unset). Strict `=== false` so the pre-hydration
 *  flash (xiheConfig={}) keeps the ordinary empty state. */
function WelcomeCard({ onGoSettings }: { onGoSettings: () => void }) {
  return (
    <div className="max-w-md rounded-2xl border border-line bg-elevated px-6 py-5 text-center">
      <BrandMark className="mx-auto h-14 w-14" />
      <div className="mt-3 text-base font-semibold text-ink">欢迎使用xihe</div>
      <div className="mt-2 text-sm leading-relaxed text-ink-3">
        还差一步：配置模型连接后就能开始对话。在设置页填写 API Key
        并保存即可（会自动重启xihe生效）。
      </div>
      <button
        onClick={onGoSettings}
        className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        <Settings className="h-4 w-4" />
        去配置模型连接
      </button>
    </div>
  )
}

/** Terminal install-state card — replaces the chat empty state when the serve
 *  child couldn't even spawn (no `xihe` on PATH). serve parks in not_found
 *  (retrying can't help), so recovery is user action + the retry button. */
function XiheMissingCard({ message }: { message?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (!(await copyText('pip install -e .'))) return
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="max-w-md rounded-2xl border border-danger/50 bg-elevated px-6 py-5 text-center">
      <div className="text-base font-semibold text-ink">未找到 xihe 命令</div>
      <div className="mt-2 text-sm leading-relaxed text-ink-3">
        桌面版依赖 xihe CLI。在仓库根目录安装后再回来重试：
      </div>
      <div className="mt-3 flex items-center justify-center gap-1.5">
        <code className="rounded-md bg-panel px-2.5 py-1 text-xs text-ink-2">pip install -e .</code>
        <button
          onClick={() => void copy()}
          title="复制安装命令"
          className="rounded-md p-1.5 text-ink-4 transition hover:bg-elevated hover:text-ink-2"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
      <div className="mt-2 text-xs text-ink-4">
        安装在别处？设 XIHE_BIN 环境变量指向 xihe 可执行文件后重启桌面版
      </div>
      <div className="mt-4 flex items-center justify-center gap-2">
        <button
          onClick={() => void desktop.serveRestart()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          <RefreshCw className="h-4 w-4" />
          已安装，重试
        </button>
        <button
          onClick={() => void desktop.openServeLog()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-4 py-2 text-sm text-ink-2 transition hover:bg-elevated"
        >
          <FileText className="h-4 w-4" />
          查看日志
        </button>
      </div>
      {message && <div className="mt-3 break-all text-[11px] text-danger/70">{message}</div>}
    </div>
  )
}

/** Approval-time path resolution. The approval gate fires BEFORE dispatch's
 *  path rewriting, so args.path is the raw model-supplied value — often
 *  relative to the workspace the conversation is bound to (resolve at click
 *  time via the store snapshot; the card is only visible in the active conv).
 *  Returns null when no binding can anchor a relative path. */
function resolveApprovalPath(rawPath: string): string | null {
  if (typeof rawPath !== 'string' || !rawPath) return null
  if (/^[a-zA-Z]:[\\/]/.test(rawPath) || rawPath.startsWith('/') || rawPath.startsWith('\\\\')) {
    return rawPath
  }
  const s = useStore.getState()
  const convId = s.activeConvId
  const wsId = convId ? s.convWorkspace[convId] : undefined
  const workdir = wsId ? s.workspaces.find((w) => w.id === wsId)?.workdir : undefined
  if (!workdir) return null
  const sep = workdir.includes('\\') ? '\\' : '/'
  return workdir.endsWith(sep) ? workdir + rawPath : workdir + sep + rawPath
}

const RESET_REASON_LABEL: Record<string, string> = {
  manual: '手动',
  idle: '闲置超时',
  daily: '每日重置',
}

/** Reset divider shown before the first message of a post-reset round (or as
 *  a trailing marker when the new round has no messages yet). Takes the place
 *  of the time divider — stacking both reads as noise. */
function resetDividerLabel(reason?: string): string {
  const label = RESET_REASON_LABEL[reason ?? ''] ?? (reason ? reason : '')
  return label ? `⟲ 上下文已重置（${label}）` : '⟲ 上下文已重置'
}

const PREVIEW_MAX_CHARS = 256 * 1024

type PreviewState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | {
      kind: 'diff'
      oldText: string
      newText: string
      oldHeader: string
      newHeader: string
      language?: string
    }
  | { kind: 'unavailable'; reason: string }

/** 「查看变更」 body of the approval card. While the approval is PENDING the
 *  write hasn't landed, so desktop readFile sees the true pre-change content —
 *  that's the old side of the write_file diff. patch compares its own
 *  old/new snippets (hunk-level, more focused than the whole file). */
function ApprovalChangePreview({ ap }: { ap: PendingApproval }) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<PreviewState>({ kind: 'idle' })

  async function load(): Promise<void> {
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(ap.args ?? '')
    } catch {
      setState({ kind: 'unavailable', reason: '参数不可解析（旧版 serve 或参数过大被截断）' })
      return
    }
    if (ap.name === 'patch') {
      const oldText = typeof parsed.old === 'string' ? parsed.old : ''
      const newText = typeof parsed.new === 'string' ? parsed.new : ''
      if (Math.max(oldText.length, newText.length) > PREVIEW_MAX_CHARS) {
        setState({ kind: 'unavailable', reason: '变更内容过大，请批准后从 diff 卡查看' })
        return
      }
      setState({
        kind: 'diff',
        oldText,
        newText,
        oldHeader: '替换前（片段）',
        newHeader: '替换后（片段）',
        language: detectLanguage(String(parsed.path ?? '')),
      })
      return
    }
    // write_file
    const newText = typeof parsed.content === 'string' ? parsed.content : ''
    const absPath = resolveApprovalPath(String(parsed.path ?? ''))
    if (!absPath) {
      setState({ kind: 'unavailable', reason: '相对路径且会话未绑定工作空间，无法定位文件' })
      return
    }
    if (newText.length > PREVIEW_MAX_CHARS) {
      setState({ kind: 'unavailable', reason: '写入内容过大，请批准后从 diff 卡查看' })
      return
    }
    const r = await desktop.readFile(absPath)
    if (r.ok && r.truncated) {
      setState({ kind: 'unavailable', reason: '原文件超过 1 MB，无法完整对比' })
      return
    }
    const oldText = r.ok ? r.content : ''
    setState({
      kind: 'diff',
      oldText,
      newText,
      oldHeader: r.ok ? '磁盘当前内容' : '（新文件，磁盘无此文件）',
      newHeader: '将写入的内容',
      language: detectLanguage(absPath),
    })
  }

  function toggle(): void {
    const next = !open
    setOpen(next)
    if (next && state.kind === 'idle') {
      setState({ kind: 'loading' })
      void load()
    }
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={toggle}
        className="inline-flex items-center gap-1 text-xs text-warning/90 hover:text-warning"
      >
        <Eye className="h-3 w-3" />
        {open ? '收起变更' : '查看变更'}
      </button>
      {open && (
        <div className="mt-1.5 w-[36rem] max-w-full overflow-hidden rounded-lg border border-line bg-app">
          {state.kind === 'loading' && (
            <div className="px-3 py-2 text-xs text-ink-4">读取变更内容…</div>
          )}
          {state.kind === 'unavailable' && (
            <div className="px-3 py-2 text-xs text-ink-4">{state.reason}</div>
          )}
          {state.kind === 'diff' && (
            <div className="h-64">
              <Suspense fallback={<div className="px-3 py-2 text-xs text-ink-4">对比视图加载中…</div>}>
                <MonacoDiffPane
                  oldText={state.oldText}
                  newText={state.newText}
                  oldHeader={state.oldHeader}
                  newHeader={state.newHeader}
                  language={state.language}
                />
              </Suspense>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Approval card — surfaces a dangerous operation blocked mid-turn for the
 *  user's verdict. Buttons only while the turn (and the request) is live;
 *  settled/expired cards stay as a record with a result badge. */
function ApprovalCard({ ap, running, onApprove }: {
  ap: PendingApproval
  running: boolean
  onApprove: (id: string, approved: boolean, always?: boolean) => void
}) {
  const settled =
    ap.status === 'approved' ? '已批准'
    : ap.status === 'denied' ? '已拒绝'
    : ap.status === 'expired' ? '已失效'
    : null
  // Preview is pending-only: after settle the write has landed (or the turn
  // moved on), so a disk read would no longer show the pre-change content.
  const previewable =
    ap.status === 'pending' && ap.args != null && (ap.name === 'write_file' || ap.name === 'patch')
  return (
    <div className="w-fit rounded-xl border border-warning/40 bg-warning/10 px-3 py-2.5 text-sm">
      <div className="flex items-center gap-1.5 font-medium text-warning">
        <ShieldAlert className="h-4 w-4" />
        危险操作待确认
        <span className="text-[10px] font-normal text-ink-4">{ap.name}</span>
      </div>
      <div className="mt-1 max-w-md whitespace-pre-wrap break-all text-ink-2">{ap.summary}</div>
      {previewable && <ApprovalChangePreview ap={ap} />}
      {ap.status === 'pending' ? (
        running ? (
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => onApprove(ap.id, true)}
              className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500"
            >
              批准
            </button>
            <button
              onClick={() => onApprove(ap.id, true, true)}
              title="批准这一次，且本会话内相同操作不再询问"
              className="rounded-lg border border-success/50 px-3 py-1 text-xs font-medium text-success hover:bg-success/10"
            >
              批准，不再询问
            </button>
            <button
              onClick={() => onApprove(ap.id, false)}
              className="rounded-lg bg-rose-600 px-3 py-1 text-xs font-medium text-white hover:bg-rose-500"
            >
              拒绝
            </button>
          </div>
        ) : (
          // Request outlived the running flag locally (socket dropped before
          // complete) — same treatment as expired, without rewriting status.
          <div className="mt-2 text-xs text-ink-4">等待答复（回合已结束，无法批复）</div>
        )
      ) : (
        <div className="mt-2">
          <span
            className={
              'rounded-md px-1.5 py-0.5 text-[10px] ' +
              (ap.status === 'approved'
                ? 'bg-success/10 text-success'
                : 'bg-danger/10 text-danger')
            }
          >
            {settled}
          </span>
        </div>
      )}
    </div>
  )
}

/** Clarify card — the agent asks the user a question mid-turn and WAITS:
 *  the answer (option click or free text) resolves the pending question
 *  server-side and the turn continues with it. Settled/expired cards stay as
 *  a record with the delivered answer. */
function ClarifyCard({ cl, running, onAnswer }: {
  cl: PendingClarify
  running: boolean
  onAnswer: (id: string, answer: string) => void
}) {
  const [draft, setDraft] = useState('')
  const live = cl.status === 'pending' && running
  return (
    <div className="w-fit max-w-md rounded-xl border border-sky-400/40 bg-sky-400/10 px-3 py-2.5 text-sm">
      <div className="flex items-center gap-1.5 font-medium text-sky-300">
        <Navigation className="h-4 w-4 rotate-0" />
        需要澄清
      </div>
      <div className="mt-1 whitespace-pre-wrap break-all text-ink-2">{cl.question}</div>
      {live ? (
        <>
          {cl.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {cl.options.map((opt) => (
                <button
                  key={opt}
                  onClick={() => onAnswer(cl.id, opt)}
                  className="rounded-lg border border-sky-400/50 px-2.5 py-1 text-xs text-sky-200 transition hover:bg-sky-400/20"
                >
                  {opt}
                </button>
              ))}
            </div>
          )}
          <div className="mt-2 flex gap-1.5">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && draft.trim()) {
                  e.preventDefault()
                  onAnswer(cl.id, draft)
                  setDraft('')
                }
              }}
              placeholder="或输入自定义回答…"
              className="w-56 rounded-lg border border-line bg-panel px-2 py-1 text-xs text-ink outline-none focus:border-sky-400/60"
            />
            <button
              onClick={() => {
                if (!draft.trim()) return
                onAnswer(cl.id, draft)
                setDraft('')
              }}
              disabled={!draft.trim()}
              className="rounded-lg bg-sky-500 px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-30"
            >
              回答
            </button>
          </div>
        </>
      ) : (
        <div className="mt-2">
          {cl.status === 'answered' ? (
            <span className="rounded-md bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300">
              已回答：{cl.answer ?? '（另一个客户端）'}
            </span>
          ) : cl.status === 'pending' ? (
            <span className="text-xs text-ink-4">等待回答（回合已结束，无法答复）</span>
          ) : (
            <span className="rounded-md bg-elevated/60 px-1.5 py-0.5 text-[10px] text-ink-4">
              未收到回答
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/** Stable-callback bundle shared by the memoized rows — the object identity is
 *  preserved across renders (useMemo in ChatPanel), so unchanged rows skip
 *  re-render entirely during a streaming turn. */
interface UserRowCbs {
  onStartEdit: (m: Message) => void
  onEditText: (t: string) => void
  onConfirmEdit: (index: number, text: string) => void
  onCancelEdit: () => void
  onCopy: (m: Message) => void
  onResend: (index: number) => void
}

interface AssistantRowCbs {
  onApprove: (id: string, approved: boolean, always?: boolean) => void
  onAnswerClarify: (id: string, answer: string) => void
  onLoadTrace: (anchor: string) => void
  onToggleRaw: (id: string) => void
  onCopy: (m: Message) => void
  onRegenerate: (index: number) => void
  onGoSettings: () => void
  onContinue: () => void
}

const UserRow = memo(function UserRow({ m, i, editing, editText, copied, disabled, cbs }: {
  m: Message
  i: number
  editing: boolean
  editText: string
  copied: boolean
  disabled: boolean
  cbs: UserRowCbs
}) {
  const editTaRef = useRef<HTMLTextAreaElement>(null)
  // Auto-grow the edit textarea with its content (capped by max-h-64).
  useEffect(() => {
    if (!editing) return
    const ta = editTaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${ta.scrollHeight}px`
  }, [editText, editing])
  return (
    <div className="flex justify-end">
      {editing ? (
        <div className="flex w-[75%] flex-col items-end gap-1.5">
          <textarea
            ref={editTaRef}
            autoFocus
            value={editText}
            onChange={(e) => cbs.onEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                cbs.onConfirmEdit(i, editText)
              } else if (e.key === 'Escape') {
                e.preventDefault()
                cbs.onCancelEdit()
              }
            }}
            className="max-h-64 w-full resize-none overflow-y-auto rounded-2xl rounded-br-sm border border-line bg-contrast px-3.5 py-2 text-sm text-white outline-none focus:border-brand"
          />
          <div className="flex gap-2">
            <button
              onClick={cbs.onCancelEdit}
              className="rounded-lg border border-line px-3 py-1 text-xs text-ink-3 hover:bg-elevated"
            >
              取消
            </button>
            <button
              onClick={() => cbs.onConfirmEdit(i, editText)}
              disabled={!editText.trim()}
              className="rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-30"
            >
              发送
            </button>
          </div>
        </div>
      ) : (
        <div className="group flex max-w-[75%] items-end gap-1.5">
          {m.ts && <HoverTimestamp iso={m.ts} side="left" />}
          <HoverIconBtn title="复制" onClick={() => cbs.onCopy(m)}>
            {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
          </HoverIconBtn>
          <HoverIconBtn
            title="编辑：修改这条消息后重新发送（之后的回复会被撤回）"
            disabled={disabled}
            onClick={() => cbs.onStartEdit(m)}
          >
            <Pencil className="h-3.5 w-3.5" />
          </HoverIconBtn>
          <HoverIconBtn
            title="重新发送：撤回此消息及之后的回复，重新发送"
            disabled={disabled}
            onClick={() => cbs.onResend(i)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </HoverIconBtn>
          <div className="flex max-w-full flex-col items-end gap-1">
            {!!m.attachments?.length && (
              <div className="flex max-w-full flex-wrap justify-end gap-1">
                {m.attachments.map((a) => {
                  const isImg = /\.(png|jpe?g|gif|webp|bmp)$/i.test(a.path)
                  return isImg ? (
                    <img
                      key={a.path}
                      src={localImgUrl(a.path)}
                      alt={a.name}
                      title={a.desc || a.path}
                      className="max-h-40 max-w-[240px] rounded-lg border border-line object-cover"
                    />
                  ) : (
                    <span
                      key={a.path}
                      className="inline-flex items-center gap-1 rounded-lg border border-line bg-panel px-2 py-1 text-xs text-ink-3"
                      title={a.path}
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0" />
                      <span className="max-w-[180px] truncate">{a.name}</span>
                    </span>
                  )
                })}
              </div>
            )}
            <div className="whitespace-pre-wrap rounded-2xl rounded-br-sm bg-contrast px-3.5 py-2 text-sm text-white">
              {m.content}
            </div>
          </div>
        </div>
      )}
    </div>
  )
})

/** 重新生成 truncates this turn AND everything after it — destructive enough
 *  to deserve a two-step confirm (click → armed with a timeout → click again
 *  to fire). Later turns the user hasn't read may vanish. */
function RegenButton({ index, pending, onRegenerate }: {
  index: number
  pending: boolean
  onRegenerate: (index: number) => void
}) {
  const [armed, setArmed] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])
  const click = (): void => {
    if (armed) {
      if (timerRef.current) clearTimeout(timerRef.current)
      setArmed(false)
      onRegenerate(index)
      return
    }
    setArmed(true)
    timerRef.current = setTimeout(() => setArmed(false), 3_000)
  }
  return (
    <button
      onClick={click}
      disabled={pending}
      title="重新生成本轮回答；该轮之后的对话将被删除"
      className={cn(
        'flex select-none items-center gap-1 text-[10px] transition disabled:opacity-30',
        armed
          ? 'rounded bg-warning/15 px-1.5 py-0.5 text-warning'
          : 'text-ink-4 hover:text-ink-2'
      )}
    >
      <RefreshCw className="h-3 w-3" />
      {armed ? '确认删除后续对话并重新生成？' : '重新生成'}
    </button>
  )
}

const AssistantRow = memo(function AssistantRow({ m, i, running, pending, isRaw, copied, cbs }: {
  m: Message
  i: number
  running: boolean
  pending: boolean
  isRaw: boolean
  copied: boolean
  cbs: AssistantRowCbs
}) {
  const liveTools = m.trace?.filter((t) => t.kind === 'tool').length ?? 0
  const total = liveTools || m.toolsCount || 0
  const hasThought = m.trace?.some((t) => t.kind === 'thought')
  const hasSteer = m.trace?.some((t) => t.kind === 'steer')
  const showTrace = !!total || !!hasThought || !!hasSteer || !!m.hasReasoning
  // Absolute image paths written in the reply TEXT render as an attachment
  // strip under the message — single source of truth: content is persisted,
  // so live turns and reloaded history behave identically (image_render's
  // result hint makes the model mention the path in plain text).
  const renderImages = !pending
    ? [...new Set(
        m.content.match(
          /[A-Za-z]:[\\/][^\s`()（）[\]]+?\.(?:png|jpe?g|gif|webp|bmp)/gi) ?? []
      )]
    : []
  return (
    <div className="group flex items-start justify-start">
      <div className="flex max-w-[80%] flex-col gap-1">
        {showTrace && (
          <TurnTrace
            trace={m.trace}
            pending={!!m.pending}
            toolsCount={total}
            hasReasoning={m.hasReasoning}
            anchor={m.traceAnchor}
            onLoadTrace={cbs.onLoadTrace}
          />
        )}
        {m.pendingApproval && (
          <ApprovalCard
            ap={m.pendingApproval}
            running={running}
            onApprove={cbs.onApprove}
          />
        )}
        {m.pendingClarify && (
          <ClarifyCard
            cl={m.pendingClarify}
            running={running}
            onAnswer={cbs.onAnswerClarify}
          />
        )}
        <div
          className={cn(
            'rounded-2xl rounded-bl-sm px-3.5 py-2 text-sm',
            m.error
              ? 'border border-danger/40 bg-danger/10 text-danger'
              : 'bg-elevated text-ink'
          )}
        >
          {/* Pending turns render plain text: re-parsing the whole growing
              markdown (react-markdown + re-highlighting every code block) per
              delta was the long-turn freeze. Full markdown — including
              mermaid, whose render costs 100ms+ — mounts once the turn
              completes and this row's identity goes stable. */}
          {m.pending || isRaw ? (
            <div className="whitespace-pre-wrap break-words">{m.content}</div>
          ) : (
            <Markdown content={m.content} />
          )}
          {m.pending && !m.stopping && (() => {
            // The pending cue must stay visible for the WHOLE turn —
            // text already streamed + a long tool running otherwise
            // looks like a finished reply.
            const runningTool = m.trace?.some(
              (t) => t.kind === 'tool' && t.status === 'running'
            )
            if (runningTool)
              return <span className="text-ink-4">正在执行工具…</span>
            if (m.content === '')
              return <span className="text-ink-4">正在思考…</span>
            return (
              <span className="ml-0.5 inline-flex items-center gap-1 align-middle text-ink-4">
                <span className="inline-block h-3 w-1.5 animate-pulse bg-ink-3" />
                处理中…
              </span>
            )
          })()}
          {m.pending && m.stopping && (
            <span className="ml-1 text-warning">正在停止…</span>
          )}
        </div>
        {renderImages.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {renderImages.map((p) => (
              <img
                key={p}
                src={localImgUrl(p)}
                alt="image_render"
                onError={(e) => {
                  e.currentTarget.style.display = 'none'
                }}
                className="max-h-96 max-w-full self-start rounded-lg border border-line"
              />
            ))}
          </div>
        )}
        {!m.pending && m.error && /api_key|配置/.test(m.content) && (
          <button
            onClick={cbs.onGoSettings}
            className="w-fit text-[10px] text-danger hover:text-danger/80"
          >
            打开设置
          </button>
        )}
        {!m.pending && m.content && (
          <div className="flex items-center gap-3">
            <button
              onClick={() => cbs.onToggleRaw(m.id)}
              className="select-none text-[10px] text-ink-4 hover:text-ink-2"
            >
              {isRaw ? '渲染显示' : '查看原文'}
            </button>
            <button
              onClick={() => cbs.onCopy(m)}
              className="flex select-none items-center gap-1 text-[10px] text-ink-4 hover:text-ink-2"
            >
              {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
              {copied ? '已复制' : '复制'}
            </button>
            <RegenButton index={i} pending={pending} onRegenerate={cbs.onRegenerate} />
          </div>
        )}
        {m.interrupted && (
          <div className="inline-flex w-fit items-center gap-1 rounded-md bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning/80">
            <Square className="h-2.5 w-2.5" />
            已停止
          </div>
        )}
        {m.incomplete && !m.interrupted && (
          <div className="inline-flex w-fit items-center gap-1.5 rounded-md bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning/80">
            <span className="inline-flex items-center gap-1">
              <Square className="h-2.5 w-2.5" />
              未完成
            </span>
            <button
              onClick={cbs.onContinue}
              disabled={pending}
              title="已执行的工具调用结果已保留，继续从中断处处理"
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium text-warning transition hover:bg-warning/20 disabled:opacity-30"
            >
              <Play className="h-2.5 w-2.5" />
              继续
            </button>
          </div>
        )}
        {!m.pending && !m.error && <UsageBadge usage={m.usage} />}
      </div>
      {m.ts && !m.pending && <HoverTimestamp iso={m.ts} side="right" />}
    </div>
  )
})

/** 企微式时间体系：
 *  分隔条 = 间隔超阈值时在消息间插入居中自适应时间（今天 HH:mm / 昨天 HH:mm /
 *  MM月DD日 HH:mm / YYYY年MM月DD日 HH:mm），连续快速对话不插入。
 *  单条消息 = hover 时在消息旁空白处显示完整时间（YYYY-MM-DD HH:mm:ss），
 *  用户消息左侧、agent 消息右侧，不常驻。 */
const TIME_DIVIDER_GAP_MS = 5 * 60 * 1000 // 5 分钟

/** 分隔条用的自适应格式——短、一眼读出"这是多久以前的事"。 */
function formatDividerTime(iso: string): string | null {
  const date = new Date(iso)
  if (isNaN(date.getTime())) return null
  const now = new Date()
  const time = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86400_0000
  const msgTime = date.getTime()
  if (msgTime >= startOfToday) return time
  if (msgTime >= startOfYesterday) return `昨天 ${time}`
  if (date.getFullYear() === now.getFullYear())
    return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${time}`
}

/** hover 用的完整格式——精确到秒，排查"到底几点发的"用。 */
function formatFullTime(iso: string): string | null {
  const date = new Date(iso)
  if (isNaN(date.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
         `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** Whether a time divider should appear before message index i. */
function needsDivider(messages: Message[], index: number): string | null {
  const current = messages[index]
  if (!current?.ts) return null
  const currentTime = new Date(current.ts).getTime()
  if (isNaN(currentTime)) return null
  const previous = index > 0 ? messages[index - 1] : null
  if (!previous?.ts) return formatDividerTime(current.ts)
  const previousTime = new Date(previous.ts).getTime()
  if (isNaN(previousTime)) return formatDividerTime(current.ts)
  if (currentTime - previousTime < TIME_DIVIDER_GAP_MS) return null
  return formatDividerTime(current.ts)
}

/** Hover-revealed per-message timestamp — hidden by default, fades in when
 *  the mouse enters the message row. Rendered in the blank margin beside
 *  the bubble (user: left, assistant: right). */
function HoverTimestamp({ iso, side }: { iso: string; side: 'left' | 'right' }) {
  const text = formatFullTime(iso)
  if (!text) return null
  return (
    <span
      className={cn(
        'shrink-0 self-center whitespace-nowrap text-[9px] text-ink-5 opacity-0 transition-opacity group-hover:opacity-100',
        side === 'left' ? 'pr-0.5' : 'pl-0.5'
      )}
    >
      {text}
    </span>
  )
}

export function ChatPanel() {
  const activeConvId = useStore((s) => s.activeConvId)
  const resetConversation = useStore((s) => s.resetConversation)
  const convCount = useStore((s) => s.conversations.length)
  const messages = useStore((s) =>
    s.activeConvId ? s.sessions[s.activeConvId] ?? [] : []
  )
  const sendMessage = useStore((s) => s.sendMessage)
  const newConversation = useStore((s) => s.newConversation)
  const interrupt = useStore((s) => s.interrupt)
  const steer = useStore((s) => s.steer)
  const resendMessage = useStore((s) => s.resendMessage)
  const regenerateMessage = useStore((s) => s.regenerateMessage)
  const editAndResendMessage = useStore((s) => s.editAndResendMessage)
  const approve = useStore((s) => s.approve)
  const answerClarify = useStore((s) => s.answerClarify)
  const loadTrace = useStore((s) => s.loadTrace)
  const serveConnected = useStore((s) => s.serveConnected)
  const xiheConfig = useStore((s) => s.xiheConfig)
  const xiheStatus = useStore((s) => s.xiheStatus)
  const setTab = useStore((s) => s.setTab)
  const [input, setInput] = useState('')
  // 已上传待发送的附件（serve 已落盘，带服务器端 path）。选文件即上传，
  // 发送时随 send 帧的 attachments 字段回传；chip 可单个移除。
  const [pendingFiles, setPendingFiles] = useState<{ name: string; path: string; size: number }[]>([])
  const [uploading, setUploading] = useState(false)
  // Plan mode: sends run read-only planning + an approval card; approval
  // auto-starts the execution turn (serve-side). Sticky — stays armed until
  // toggled off, for consecutive planning tasks.
  const [planMode, setPlanMode] = useState(false)
  // User-message edit: the bubble in edit mode (editingId) swaps to a
  // textarea; confirm rolls the conversation back to before it and sends the
  // edited text as a fresh turn.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  // Message whose copy click just succeeded — swaps Copy→Check briefly.
  const [copiedId, setCopiedId] = useState<string | null>(null)
  // Assistant messages render markdown by default; per-message escape hatch
  // back to the raw text (查看原文).
  const [rawIds, setRawIds] = useState<Set<string>>(new Set())
  const toggleRaw = useCallback((id: string) =>
    setRawIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    }), [])
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const pending = messages.some((m) => m.role === 'assistant' && m.pending)

  // xihe (when serveConnected) supports steer while a turn runs. The stop
  // button shows while `running`.
  const running = pending && serveConnected

  // Don't yank the view when the user scrolls up (reading history, expanding a
  // 思考/工具 trace): auto-follow new content only while already near the
  // bottom. Opening/switching a conversation re-arms the follow.
  const stickRef = useRef(true)

  // Bottom sentinel: jump/follow scroll THIS node into view instead of
  // scrollTo(scrollHeight) — scrollHeight at call time is stale when async
  // content (image attachments) loads afterwards and grows the list, leaving
  // the view stranded mid-way. Anchoring to the sentinel re-resolves on every
  // layout change, including late image loads.
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    stickRef.current = true
    setEditingId(null)
    // Jump after the new conv's DOM commits (double-rAF: commit → layout →
    // paint), so the sentinel exists at its final position.
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ block: 'end' })
      })
    })
    return () => cancelAnimationFrame(raf)
  }, [activeConvId])

  useEffect(() => {
    if (!stickRef.current) return
    // rAF-aligned: coalesced store flushes still fire this effect ~20×/s
    // during a stream; aligning the scroll to paint avoids stacking layout
    // reads between commits.
    const raf = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ block: 'end' })
    })
    return () => cancelAnimationFrame(raf)
  }, [messages])

  // Keystrokes die whenever focus falls on <body>: opening a conversation
  // leaves focus on the clicked row/button, and deleting one unmounts the row
  // that held focus (the "takes forever to accept input" reports). So every
  // conversation switch (post-mount runs) takes focus, and a list-length
  // change re-takes it when it dropped to <body>. The body guard keeps an
  // in-progress rename (sidebar input) or other deliberate focus from being
  // yanked; the mount runs keep the old no-grab behavior for a synced conv.
  const seenConvRef = useRef(false)
  const seenLenRef = useRef(false)
  useEffect(() => {
    // conversations read as a snapshot on purpose (not a dep): list syncs
    // must not re-fire this and yank focus from an in-progress sidebar rename.
    const c = useStore.getState().conversations.find((x) => x.id === activeConvId)
    const first = !seenConvRef.current
    seenConvRef.current = true
    if (c && (!c.synced || !first)) inputRef.current?.focus()
  }, [activeConvId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const first = !seenLenRef.current
    seenLenRef.current = true
    if (!first && document.activeElement === document.body)
      inputRef.current?.focus()
  }, [convCount]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-grow the composer with its content (capped by max-h-32, then it
  // scrolls inside). Without this a multi-line paste shows only the last line.
  useEffect(() => {
    const ta = inputRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${ta.scrollHeight}px`
  }, [input, activeConvId])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    // A rollback (重新生成/重新发送 truncates the list) makes scrollHeight
    // collapse below scrollTop for one frame — that programmatic jump must
    // not read as "user scrolled up to read". Content shrunk, so distance
    // would exceed the container height; a real upward scroll is a smaller,
    // bounded distance.
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickRef.current = distance < 80 || distance > el.clientHeight
  }

  // Hooks must ALL run before this point — the early return below would
  // otherwise change the hook count between renders (React crashes the tree).
  // 上传失败提示（chip 位置短暂显示）：3.5s 自动消失。
  const [uploadErrors, setUploadErrors] = useState<{ name: string }[]>([])
  const pushUploadError = useCallback((name: string) => {
    setUploadErrors((cur) => [...cur, { name }])
    setTimeout(() => setUploadErrors((cur) => cur.filter((e) => e.name !== name)), 3500)
  }, [])

  const copyMessage = useCallback(async (m: Message) => {
    if (!(await copyText(m.content))) return
    setCopiedId(m.id)
    setTimeout(() => setCopiedId((cur) => (cur === m.id ? null : cur)), 1500)
  }, [])

  // Stable callback bundles feeding the memoized rows (identity held by
  // useMemo, deps all stable store fns / convId).
  const userCbs = useMemo<UserRowCbs>(() => ({
    onStartEdit: (m) => {
      setEditingId(m.id)
      setEditText(m.content)
    },
    onEditText: setEditText,
    onConfirmEdit: (index, text) => {
      const t = text.trim()
      if (!t) return
      setEditingId(null)
      setEditText('')
      void editAndResendMessage(index, t)
    },
    onCancelEdit: () => {
      setEditingId(null)
      setEditText('')
    },
    onCopy: (m) => void copyMessage(m),
    onResend: (index) => void resendMessage(index),
  }), [editAndResendMessage, resendMessage, copyMessage])

  const assistantCbs = useMemo<AssistantRowCbs>(() => ({
    onApprove: (id, approved, always) => approve(id, approved, always),
    onAnswerClarify: (id, answer) => answerClarify(id, answer),
    onLoadTrace: (anchor) => {
      const c = activeConvId
      if (c) void loadTrace(c, anchor)
    },
    onToggleRaw: toggleRaw,
    onCopy: (m) => void copyMessage(m),
    onRegenerate: (index) => void regenerateMessage(index),
    onGoSettings: () => setTab('manage'),
    // 继续 = 往同一会话发一条续跑指令。serve 每轮加载持久化历史并修复
    // 悬空 tool_calls，所以已执行的工具记录随上下文复用，模型从中断处
    // 接着做而不重跑。
    onContinue: () => void sendMessage(
      '继续上一轮中断的任务（已执行的工具调用结果已保留，不要重复已完成的工作）'),
  }), [activeConvId, approve, answerClarify, loadTrace, toggleRaw, copyMessage, regenerateMessage, setTab, sendMessage])

  // Early return is after the last hook — hooks above must always run.
  if (!activeConvId) {
    if (xiheStatus?.state === 'not_found') {
      return (
        <div className="flex h-full flex-col items-center justify-center text-center">
          <XiheMissingCard message={xiheStatus.message} />
        </div>
      )
    }
    if (xiheConfig.api_key_set === false) {
      return (
        <div className="flex h-full flex-col items-center justify-center">
          <WelcomeCard onGoSettings={() => setTab('manage')} />
        </div>
      )
    }
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <BrandMark className="h-14 w-14" />
        <div className="text-sm text-ink-4">还没有对话</div>
        <button
          onClick={() => newConversation()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          <Plus className="h-4 w-4" />
          开始新对话
        </button>
      </div>
    )
  }

  const submit = () => {
    const text = input.trim()
    if (!text && !pendingFiles.length) return
    sendMessage(
      text,
      planMode || undefined,
      undefined,
      pendingFiles.length ? pendingFiles : undefined
    )
    setInput('')
    setPendingFiles([])
  }

  const steerSubmit = () => {
    const text = input.trim()
    if (!text) return
    steer(text)
    setInput('')
  }

  // 选文件 → 立即上传到 serve（拿到服务器端 path）→ 显示 chip。发送时
  // 附件已在服务器上，send 帧只带元数据。上传失败提示文件名（超限/网络）。
  const pickAndUpload = async () => {
    const convId = activeConvId
    if (!convId || uploading) return
    const paths = await desktop.openFiles()
    if (!paths.length) return
    setUploading(true)
    try {
      const { uploadAttachment } = await import('../lib/serveClient')
      for (const p of paths) {
        const up = await uploadAttachment(convId, p)
        if (up) {
          setPendingFiles((cur) => [...cur, { name: up.name, path: up.path, size: up.size }])
        } else {
          pushUploadError(p.split(/[\\/]/).pop() || p)
        }
      }
    } finally {
      setUploading(false)
    }
  }

  // While a turn runs, Enter steers it (non-interrupting redirect). Idle →
  // normal send.
  const onPrimary = () => {
    if (running) return steerSubmit()
    return submit()
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
        {messages.length === 0 && xiheConfig.api_key_set === false && (
          <div className="mt-10 flex justify-center">
            <WelcomeCard onGoSettings={() => setTab('manage')} />
          </div>
        )}
        {messages.length === 0 && xiheConfig.api_key_set !== false && (
          <div className="mt-10 text-center text-sm text-ink-4">
            <BrandMark className="mx-auto h-14 w-14" />
            <div className="mt-3">发送消息开始对话</div>
            {!serveConnected && (
              <div className="mt-1 text-xs text-warning/70">xihe未连接，等待启动…</div>
            )}
          </div>
        )}
        {messages.map((m, i) => {
          const showReset = m.roundStart || m.dividerOnly
          const divider = showReset
            ? resetDividerLabel(m.resetReason)
            : needsDivider(messages, i)
          return (
            <div key={m.id}>
              {divider && (
                <div className="py-1.5 text-center text-[10px] text-ink-5">
                  {showReset ? (
                    <span className="inline-flex items-center gap-1 rounded-md bg-elevated/60 px-2 py-0.5 text-ink-4">
                      {divider}
                    </span>
                  ) : (
                    divider
                  )}
                </div>
              )}
              {m.dividerOnly ? null : m.role === 'user' ? (
                <UserRow
                  m={m}
                  i={i}
                  editing={editingId === m.id}
                  editText={editText}
                  copied={copiedId === m.id}
                  disabled={pending}
                  cbs={userCbs}
                />
              ) : (
                <AssistantRow
                  m={m}
                  i={i}
                  running={running}
                  pending={pending}
                  isRaw={rawIds.has(m.id)}
                  copied={copiedId === m.id}
                  cbs={assistantCbs}
                />
              )}
            </div>
          )
        })}
        <div ref={bottomRef} className="h-px" />
      </div>
      <div className="border-t border-line p-3">
        {!!uploadErrors.length && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {uploadErrors.map((er) => (
              <span
                key={er.name}
                className="inline-flex items-center gap-1 rounded-lg border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger"
              >
                <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                <span className="max-w-[200px] truncate" title={er.name}>
                  {er.name}
                </span>
                上传失败
              </span>
            ))}
          </div>
        )}
        {!!pendingFiles.length && !running && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {pendingFiles.map((f) => {
              const isImg = /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.path)
              return (
                <span
                  key={f.path}
                  className="group/att inline-flex items-center gap-1 rounded-lg border border-line bg-panel px-2 py-1 text-xs text-ink-3"
                  title={f.path}
                >
                  {isImg ? (
                    <img src={localImgUrl(f.path)} alt="" className="h-6 w-6 rounded object-cover" />
                  ) : (
                    <FileText className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="max-w-[160px] truncate">{f.name}</span>
                  <button
                    onClick={() => setPendingFiles((cur) => cur.filter((x) => x.path !== f.path))}
                    className="rounded p-0.5 text-ink-5 hover:bg-elevated hover:text-ink-2"
                    title="移除"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              )
            })}
          </div>
        )}
        <div
          onClick={(e) => {
            // The visible input bar has padding the textarea doesn't cover, so
            // clicking the padding (most of the box) misses the field. Treat the
            // whole bar as the input: a click on the bar itself focuses it.
            // (e.target === e.currentTarget → the click landed on the padding/
            // gap, not on the textarea or a button, which handle themselves.)
            if (e.target === e.currentTarget) inputRef.current?.focus()
          }}
          className="flex items-end gap-2 rounded-xl bg-panel px-3 py-2"
        >
          <button
            onClick={() => activeConvId && void resetConversation(activeConvId)}
            disabled={!serveConnected || !activeConvId || running}
            title="重置上下文：保留对话历史，清空 agent 对本轮之前内容的记忆（历史中会出现分隔线）"
            className="mb-0.5 rounded-lg p-1.5 text-ink-4 transition hover:bg-elevated hover:text-warning disabled:opacity-30 disabled:hover:bg-transparent"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <button
            onClick={() => void pickAndUpload()}
            disabled={!serveConnected || !activeConvId || running || uploading}
            title={uploading ? '上传中…' : '添加附件（文件随消息发送给 agent）'}
            className="mb-0.5 rounded-lg p-1.5 text-ink-4 transition hover:bg-elevated hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent"
          >
            <Paperclip className="h-4 w-4" />
          </button>
          <button
            onClick={() => setPlanMode((v) => !v)}
            disabled={!serveConnected || running}
            title="plan 模式：消息只读探查并产出计划，批准后自动开始执行；再次点击关闭"
            className={cn(
              'mb-0.5 rounded-lg p-1.5 transition disabled:opacity-30',
              planMode
                ? 'bg-contrast text-sky-300'
                : 'text-ink-4 hover:bg-elevated hover:text-ink'
            )}
          >
            <Map className="h-4 w-4" />
          </button>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const cd = e.clipboardData
              // 剪贴板带文件（截图 Ctrl+V、复制的文件）→ 直接上传成附件
              // chip，字节已在 File 里，不经 preload 读盘桥；阻止默认行为
              // 免得文件名被当文本粘进输入框。
              if (cd && cd.files?.length) {
                e.preventDefault()
                const convId = activeConvId
                const files = Array.from(cd.files)
                if (!convId || uploading) return
                setUploading(true)
                void (async () => {
                  try {
                    const { uploadFile } = await import('../lib/serveClient')
                    for (const f of files) {
                      const up = await uploadFile(convId, f)
                      if (up) {
                        setPendingFiles((cur) => [
                          ...cur,
                          { name: up.name, path: up.path, size: up.size },
                        ])
                      } else {
                        pushUploadError(f.name || 'clipboard file')
                      }
                    }
                  } finally {
                    setUploading(false)
                  }
                })()
                return
              }
              // 复制消息/历史时常带尾随换行，粘贴进输入框后多出空行；去掉尾部
              // 空白，保留中间内容与行内格式。无尾随空白时走默认粘贴。
              const text = cd ? cd.getData('text') : ''
              if (!text) return
              const cleaned = text.replace(/\s+$/, '')
              if (cleaned === text) return
              e.preventDefault()
              const ta = e.currentTarget
              const next =
                input.slice(0, ta.selectionStart ?? 0) +
                cleaned +
                input.slice(ta.selectionEnd ?? 0)
              setInput(next)
              const pos = (ta.selectionStart ?? 0) + cleaned.length
              requestAnimationFrame(() => {
                if (inputRef.current) {
                  inputRef.current.selectionStart = pos
                  inputRef.current.selectionEnd = pos
                }
              })
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onPrimary()
              }
            }}
            rows={1}
            placeholder={
              running ? '追加指示（steer 改向，不打断）…' : '输入消息，Enter 发送…'
            }
            className="max-h-32 flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-ink-4"
          />
          {running ? (
            <div className="flex items-end gap-2">
              {input.trim() && (
                <button
                  onClick={steerSubmit}
                  title="改向当前回合（steer，不打断）"
                  className="rounded-lg bg-amber-600 p-2 text-white hover:bg-amber-500"
                >
                  <Navigation className="h-4 w-4" />
                </button>
              )}
              <button
                onClick={() => interrupt()}
                title="停止生成"
                className="rounded-lg bg-contrast p-2 text-white hover:brightness-125"
              >
                <Square className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <button
              onClick={submit}
              disabled={(!input.trim() && !pendingFiles.length) || !serveConnected}
              title={!serveConnected ? 'xihe未连接' : undefined}
              className="rounded-lg bg-brand p-2 text-white disabled:opacity-30"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
