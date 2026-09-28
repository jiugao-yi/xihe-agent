import { useEffect, useRef, useState } from 'react'
import { X, ChevronUp, ChevronDown } from 'lucide-react'
import { desktop } from '../lib/desktop'
import { cn } from '../lib/cn'

/** In-page find bar (Ctrl+F) — app-styled Chromium find. The UI lives here;
 *  the actual search runs in main via webContents.findInPage, so matches
 *  highlight exactly like a browser's (any rendered DOM text). Enter / F3
 *  next, Shift+Enter / Shift+F3 previous, Esc closes and clears highlights.
 *
 *  Known limit: findInPage sees only LIVE DOM — messages virtualized off or
 *  inside collapsed trace cards are matched once rendered/scrolled to. */
export function FindBar() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const offOpen = desktop.onFindOpen(() => {
      setOpen(true)
      // Focus after the bar mounts/updates; select any previous query so
      // typing replaces it (browser behavior).
      requestAnimationFrame(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      })
    })
    const offNext = desktop.onFindNext((dir) => {
      void desktop.findStart(text, dir > 0)
    })
    return () => { offOpen(); offNext() }
  }, [text])

  // Fire the search (debounced by typing cadence; findNext:true steps forward
  // on repeated calls with the same query — matches browser behavior).
  useEffect(() => {
    void desktop.findStart(text)
  }, [text])

  if (!open) return null
  const close = () => {
    setOpen(false)
    setText('')
    void desktop.findStop()
  }
  return (
    <div
      className={cn(
        'fixed right-4 top-3 z-50 flex items-center gap-1 rounded-lg border border-line',
        'bg-elevated/95 px-2 py-1.5 shadow-lg backdrop-blur'
      )}
      onKeyDown={(e) => {
        if (e.key === 'Escape') { e.stopPropagation(); close() }
        else if (e.key === 'Enter') {
          e.preventDefault()
          void desktop.findStart(text, !e.shiftKey)
        }
      }}
    >
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="在页面中查找…"
        spellCheck={false}
        className="w-56 rounded-md border border-line bg-app px-2 py-1 text-sm text-ink outline-none placeholder:text-ink-5 focus:border-ink-4"
      />
      <button
        title="上一个 (Shift+F3)"
        onClick={() => void desktop.findStart(text, false)}
        className="rounded-md p-1 text-ink-3 hover:bg-app hover:text-ink"
      >
        <ChevronUp className="h-4 w-4" />
      </button>
      <button
        title="下一个 (F3)"
        onClick={() => void desktop.findStart(text, true)}
        className="rounded-md p-1 text-ink-3 hover:bg-app hover:text-ink"
      >
        <ChevronDown className="h-4 w-4" />
      </button>
      <button
        title="关闭 (Esc)"
        onClick={close}
        className="rounded-md p-1 text-ink-3 hover:bg-app hover:text-ink"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
