/** Local absolute paths / file:// URLs → the privileged xfile:// protocol.
 *  The renderer can't load local files directly from either of its origins
 *  (http dev server / file:// build) — main's xfile handler serves them.
 *  Fixed dummy host (`local`): standard schemes reject empty hosts, and a
 *  raw drive letter would be misparsed as the host. The whole path rides as
 *  ONE percent-encoded segment. */
export function localImgUrl(s: string): string {
  let p = s
  if (p.startsWith('file:///')) p = decodeURIComponent(p.slice(8))
  if (/^[A-Za-z]:[\\/]/.test(p) || p.startsWith('\\\\')) {
    return 'xfile://local/' + encodeURIComponent(p.replace(/\\/g, '/'))
  }
  return s
}
