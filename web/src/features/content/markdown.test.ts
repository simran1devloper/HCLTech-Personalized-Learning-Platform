import { describe, expect, it } from 'vitest'
import { md, toSegments } from './markdown'

const html = (src: string) => toSegments(src).filter((s) => s.kind === 'html').map((s: any) => s.html).join('')

describe('markdown safety', () => {
  it('escapes raw HTML and script tags', () => {
    const out = html('<script>alert(1)</script> <img src=x onerror=alert(1)> <b>bold</b>')
    expect(out).not.toMatch(/<script|<img|<b>/i); expect(out).toContain('&lt;script&gt;')
  })
  it('blocks javascript:, vbscript: and data: links', () => {
    for (const u of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'vbscript:x', 'data:text/html,<script>1</script>']) {
      const out = html(`[click](${u})`); expect(out).not.toMatch(/href="(javascript|vbscript|data):/i)
    }
  })
  it('adds noopener/noreferrer to external links', () => {
    expect(html('[x](https://example.com)')).toContain('rel="noopener noreferrer nofollow"')
  })
  it('extracts fenced code and mermaid as separate segments (never as HTML)', () => {
    const s = toSegments('intro\n\n```python\nprint("<script>")\n```\n\n```mermaid\nflowchart TD\n A-->B\n```\n\nend')
    expect(s.map((x) => x.kind)).toEqual(['html', 'code', 'mermaid', 'html'])
    expect((s[1] as any).lang).toBe('python'); expect((s[1] as any).code).toContain('<script>')
  })
  it('math spans are escaped and not confused with currency', () => {
    expect(html('Euler: $e^{i\\pi}+1=0$')).toContain('class="math"')
    expect(html('It costs $5 and $6 total')).not.toContain('class="math"')
    expect(html('$$<img src=x onerror=1>$$')).not.toContain('<img')
  })
  it('streaming: unchanged leading segments keep stable keys', () => {
    const a = toSegments('Para one.\n\n```py\nx = 1')
    const b = toSegments('Para one.\n\n```py\nx = 1\ny = 2')
    expect(a[0].key).toBe(b[0].key); expect(a[1].key).toBe(b[1].key)
  })
  it('html:false is configured', () => expect(md.options.html).toBe(false))
})
