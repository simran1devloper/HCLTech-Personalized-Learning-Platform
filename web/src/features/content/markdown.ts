import MarkdownIt from 'markdown-it'

type Token = ReturnType<InstanceType<typeof MarkdownIt>['parse']>[number]

/** html:false => raw HTML in the source is escaped, never emitted. Link/image URLs go through markdown-it's
 *  validateLink (blocks javascript:, vbscript:, file:, data: except images). */
export const md = new MarkdownIt({ html: false, linkify: true, breaks: false, typographer: false })

// External links open safely.
const defaultLink = md.renderer.rules.link_open ?? ((t, i, o, _e, s) => s.renderToken(t, i, o))
md.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
  tokens[idx].attrSet('target', '_blank'); tokens[idx].attrSet('rel', 'noopener noreferrer nofollow')
  return defaultLink(tokens, idx, opts, env, self)
}

// $...$ and $$...$$ -> <span class="math" data-tex="..."> (hydrated by KaTeX after mount; data is HTML-escaped)
md.inline.ruler.before('escape', 'math', (state, silent) => {
  const src = state.src, pos = state.pos
  if (src[pos] !== '$') return false
  const display = src[pos + 1] === '$'
  const open = display ? 2 : 1
  const end = src.indexOf(display ? '$$' : '$', pos + open)
  if (end === -1) return false
  const tex = src.slice(pos + open, end)
  if (!tex.trim() || (!display && (/^\s|\s$/.test(tex) || /\d/.test(src[end + 1] ?? '') || tex.includes('\n')))) return false
  if (!silent) { const t = state.push('math', '', 0); t.content = tex; t.meta = { display } }
  state.pos = end + open
  return true
})
md.renderer.rules.math = (tokens, idx) => {
  const t = tokens[idx]
  return `<span class="math" data-display="${t.meta?.display ? 1 : 0}" data-tex="${md.utils.escapeHtml(t.content)}">${md.utils.escapeHtml(t.content)}</span>`
}

// "> **Warning:** ..." / "> **Best practice:** ..." / "> **Tip:** ..." / "> **Note:** ..." / "> **Important:** ..." render as callouts (class only; content stays escaped markdown)
export const CALLOUTS: Record<string, string> = { warning: 'warning', caution: 'warning', 'best practice': 'practice', 'best practices': 'practice', tip: 'tip', note: 'note', important: 'important' }
const defaultQuoteOpen = md.renderer.rules.blockquote_open ?? ((t, i, o, _e, s) => s.renderToken(t, i, o))
md.renderer.rules.blockquote_open = (tokens, idx, opts, env, self) => {
  const kids = tokens[idx + 2]?.type === 'inline' ? (tokens[idx + 2].children ?? []) : []
  const at = kids.findIndex((c) => c.type === 'strong_open')                              // the bold label must come first (only empty text may precede it)
  const label = at >= 0 && kids.slice(0, at).every((c) => !c.content.trim()) ? (kids[at + 1]?.content ?? '').trim().replace(/:$/, '').toLowerCase() : ''
  const kind = CALLOUTS[label]
  if (kind) { tokens[idx].attrSet('class', `callout callout-${kind}`); tokens[idx].attrSet('data-callout', kind) }
  return defaultQuoteOpen(tokens, idx, opts, env, self)
}

export type Segment = { kind: 'html'; key: string; html: string } | { kind: 'code'; key: string; lang: string; code: string } | { kind: 'mermaid'; key: string; code: string }

/** Splits a document into top-level segments so streaming re-renders only the segments whose text changed. */
export function toSegments(src: string): Segment[] {
  const tokens = md.parse(src, {})
  const segs: Segment[] = []
  let buf: Token[] = []
  const flush = () => {
    if (!buf.length) return
    const html = md.renderer.render(buf, md.options, {})
    segs.push({ kind: 'html', key: 'h' + hash(html), html }); buf = []
  }
  for (const t of tokens) {
    if (t.type === 'fence' && t.level === 0) {
      flush()
      const lang = (t.info.trim().split(/\s+/)[0] || 'text').toLowerCase()
      segs.push(lang === 'mermaid' ? { kind: 'mermaid', key: 'm' + hash(t.content), code: t.content } : { kind: 'code', key: 'c' + segs.length, lang, code: t.content.replace(/\n$/, '') })
    } else buf.push(t)
  }
  flush()
  return segs
}
function hash(s: string): string { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36) + s.length }
