import { Suspense, lazy, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { desktop } from '../lib/desktop'
import { detectLanguage } from '../lib/lang'
import { localImgUrl } from '../lib/localImage'
import { cn } from '../lib/cn'
import { Markdown } from './Markdown'

const MonacoPane = lazy(() =>
  import('./monaco/MonacoPane').then((m) => ({ default: m.MonacoPane }))
)

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i
const MD_EXT = /\.md$/i

type State =
  | { s: 'loading' }
  | { s: 'img' }
  | { s: 'text'; text: string; truncated: boolean }
  | { s: 'binary' }
  | { s: 'error' }

/** Full-content-area overlay viewer for a workspace file — the non-workbench
 *  layout's answer to "the drawer is too small". Read-only quick look with a
 *  big surface: images via xfile, markdown rendered, everything else in a
 *  Monaco pane (read-only). Esc / ✕ / backdrop click closes. */
export function FileOverlay({ path, onClose }: { path: string; onClose: () => void }) {
  const [st, setSt] = useState<State>({ s: 'loading' })
  const isImg = IMAGE_EXT.test(path)
  const isMd = MD_EXT.test(path)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (isImg) {
      setSt({ s: 'img' })
      return
    }
    setSt({ s: 'loading' })
    let cancelled = false
    void desktop.readFile(path).then((r) => {
      if (cancelled) return
      if (!r.ok) setSt({ s: r.reason === 'binary' ? 'binary' : 'error' })
      else setSt({ s: 'text', text: r.content, truncated: r.truncated })
    })
    return () => { cancelled = true }
  }, [path, isImg])

  const name = path.replace(/\\/g, '/').split('/').pop() ?? path
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className={cn(
          'flex h-[92%] w-[92%] flex-col overflow-hidden rounded-xl border border-line bg-app shadow-2xl'
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
          <span className="min-w-0 flex-1 truncate text-xs text-ink-3" title={path}>{name}</span>
          <span className="text-[10px] text-ink-5">Esc 关闭 · 只读快览</span>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-4 transition hover:bg-elevated hover:text-ink"
            title="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {st.s === 'loading' && <div className="p-4 text-xs text-ink-4">读取中…</div>}
          {st.s === 'img' && (
            <div
              className="flex h-full items-center justify-center p-6"
              style={{
                backgroundImage:
                  'linear-gradient(45deg,#8882 25%,transparent 25%,transparent 75%,#8882 75%),linear-gradient(45deg,#8882 25%,transparent 25%,transparent 75%,#8882 75%)',
                backgroundSize: '16px 16px',
                backgroundPosition: '0 0,8px 8px',
              }}
            >
              <img src={localImgUrl(path)} alt={name} className="max-h-full max-w-full object-contain" draggable={false} />
            </div>
          )}
          {st.s === 'text' && isMd && (
            <div className="mx-auto max-w-3xl px-8 py-6 text-ink">
              <Markdown content={st.text} />
              {st.truncated && (
                <div className="mt-4 border-t border-line bg-warning/5 px-4 py-1.5 text-[10px] text-warning/80">
                  文件过大，仅显示前 1 MB（只读）。
                </div>
              )}
            </div>
          )}
          {st.s === 'text' && !isMd && (
            <div className="h-full">
              <Suspense fallback={<div className="p-4 text-xs text-ink-4">编辑器加载中…</div>}>
                <MonacoPane
                  path={path}
                  value={st.text}
                  language={detectLanguage(path)}
                  readOnly
                  wordWrap
                />
              </Suspense>
              {st.truncated && (
                <div className="border-t border-line bg-warning/5 px-4 py-1.5 text-[10px] text-warning/80">
                  文件过大，仅显示前 1 MB（只读）。
                </div>
              )}
            </div>
          )}
          {st.s === 'binary' && (
            <div className="p-4 text-xs text-ink-4">二进制文件，无法预览</div>
          )}
          {st.s === 'error' && <div className="p-4 text-xs text-ink-4">读取失败</div>}
        </div>
      </div>
    </div>
  )
}
