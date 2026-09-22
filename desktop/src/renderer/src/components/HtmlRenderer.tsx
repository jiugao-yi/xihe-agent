import { useEffect, useMemo, useRef, useState } from 'react'

const MIN_HEIGHT = 160
const MAX_HEIGHT = 640

// Height reporter injected as the document's LAST element: posts the content
// height on load and on any resize (charts grow/shrink with data). Runs inside
// the sandbox (opaque origin) — postMessage still reaches the parent.
const HEIGHT_REPORTER = `<script>(function(){
  function post(){
    try { parent.postMessage({ __xiheHtmlHeight: document.documentElement.scrollHeight }, '*') } catch (e) {}
  }
  window.addEventListener('load', post)
  try { new ResizeObserver(post).observe(document.documentElement) } catch (e) {}
})()</script>`

/** In-chat renderer for ```html type="renderer" fences (the visualization
 *  skill's interactive channel): the agent's self-contained HTML runs in a
 *  sandboxed iframe — `allow-scripts` only, no same-origin, no navigation —
 *  so it can draw but never touch the app or the workspace. Height follows
 *  the content via the injected reporter, capped with internal scrolling. */
export function HtmlRenderer({ src }: { src: string }) {
  const [height, setHeight] = useState(MIN_HEIGHT)
  const frameRef = useRef<HTMLIFrameElement | null>(null)

  const doc = useMemo(() => {
    if (/<\/body>/i.test(src)) return src.replace(/<\/body>/i, HEIGHT_REPORTER + '</body>')
    return src + HEIGHT_REPORTER
  }, [src])

  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      // Only trust messages from OUR frame, and only the height field.
      if (e.source !== frameRef.current?.contentWindow) return
      const h = (e.data as { __xiheHtmlHeight?: unknown } | null)?.__xiheHtmlHeight
      if (typeof h === 'number' && Number.isFinite(h))
        setHeight(Math.min(Math.max(Math.ceil(h), MIN_HEIGHT), MAX_HEIGHT))
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  return (
    <iframe
      ref={frameRef}
      title="html 渲染"
      sandbox="allow-scripts"
      srcDoc={doc}
      style={{ height }}
      className="my-2.5 w-full rounded-lg border border-line bg-white"
    />
  )
}
