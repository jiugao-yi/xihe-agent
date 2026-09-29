import { useEffect, useRef } from 'react'
import { File as FileIcon } from 'lucide-react'
import { cn } from '../lib/cn'

export interface MentionCandidate {
  /** File name only (display bold). */
  name: string
  /** Workspace-relative path (display dim) — also what gets inserted. */
  relPath: string
}

/** @-mention picker above the composer: filtered file list + keyboard nav
 *  (↑↓ move, Enter/Tab select, Esc close — ChatPanel intercepts the keys
 *  while open). Click selects. Pure presentational; filtering lives in the
 *  caller. */
export function FileMention({ items, index, onHover, onSelect }: {
  items: MentionCandidate[]
  index: number
  onHover: (i: number) => void
  onSelect: (item: MentionCandidate) => void
}) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = listRef.current?.children[index] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [index])
  if (items.length === 0) return null
  return (
    <div
      ref={listRef}
      className="absolute bottom-full left-0 z-30 mb-1 max-h-64 w-max max-w-[26rem] min-w-80 overflow-auto rounded-lg border border-line bg-elevated p-1 shadow-lg"
    >
      {items.map((it, i) => (
        <button
          key={it.relPath}
          onMouseEnter={() => onHover(i)}
          onClick={() => onSelect(it)}
          title={it.relPath}
          className={cn(
            'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left',
            i === index ? 'bg-contrast/80' : 'hover:bg-app/60'
          )}
        >
          <FileIcon className="h-3 w-3 shrink-0 text-ink-4" />
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-xs text-ink-2">{it.name}</span>
            {it.relPath !== it.name && (
              <span className="truncate text-[10px] text-ink-5">{it.relPath}</span>
            )}
          </span>
        </button>
      ))}
    </div>
  )
}
