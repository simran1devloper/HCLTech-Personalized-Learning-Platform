import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { parseBlocks } from '@/schemas/blocks'
import BlockRenderer from './BlockRenderer.vue'
import ContentBlockRenderer from './ContentBlockRenderer.vue'
import MathBlock from './MathBlock.vue'
import { chartOption, chartSummary } from './chart'
import { looksLeaked, toDisplayText } from '@/utils/display'

const stubs = { RouterLink: true, EChart: { props: ['option', 'label', 'summary'], template: '<div data-test="chart" :data-label="label" :data-summary="summary" />' } }
const mountOne = (block: any) => mount(ContentBlockRenderer, { props: { block }, global: { stubs } })

describe('ContentBlockRenderer (v2 blocks)', () => {
  it('heading level maps under the section h2 (level 1-2 -> h3, 3 -> h4)', () => {
    expect(mountOne({ type: 'heading', level: 2, text: 'A' }).find('h3').text()).toBe('A')
    expect(mountOne({ type: 'heading', level: 3, text: 'B' }).find('h4').text()).toBe('B')
    expect(mountOne({ type: 'heading', level: 1, text: 'C' }).find('h3').text()).toBe('C')
  })
  it('ordered and unordered lists', () => {
    expect(mountOne({ type: 'list', ordered: true, items: ['a', 'b'] }).find('ol').findAll('li')).toHaveLength(2)
    expect(mountOne({ type: 'list', ordered: false, items: ['a'] }).find('ul').exists()).toBe(true)
  })
  it('callouts announce their meaning in words, not colour only', () => {
    const w = mountOne({ type: 'callout', variant: 'warning', text: 'Careful here' })
    expect(w.find('aside').attributes('role')).toBe('note'); expect(w.text()).toContain('Warning:'); expect(w.text()).toContain('Careful here')
  })
  it('images are lazy, referrer-less and always carry alt text', () => {
    const img = mountOne({ type: 'image', url: 'https://example.com/a.png', alt: 'A diagram of the flow', caption: 'cap' }).find('img')
    expect(img.attributes()).toMatchObject({ alt: 'A diagram of the flow', loading: 'lazy', referrerpolicy: 'no-referrer', src: 'https://example.com/a.png' })
  })
  it('citations link out safely and show a short quote', () => {
    const w = mountOne({ type: 'citation', label: '[1]', source_title: 'Docs', url: 'https://docs.python.org/3/', quote: 'short' })
    expect(w.find('a').attributes()).toMatchObject({ href: 'https://docs.python.org/3/', rel: 'noopener noreferrer nofollow', target: '_blank' }); expect(w.text()).toContain('“short”')
  })
  it('algorithm shows steps and complexity', () => {
    const w = mountOne({ type: 'algorithm', name: 'Binary search', steps: ['pick mid', 'compare'], time_complexity: 'O(log n)' })
    expect(w.text()).toContain('Binary search'); expect(w.text()).toContain('Time: O(log n)'); expect(w.findAll('ol li')).toHaveLength(2)
  })
  it('chart block gets a text alternative and a data table', async () => {
    const block = { type: 'chart', chart_type: 'bar', categories: ['a', 'b'], series: [{ name: 's', data: [1, 2] }], caption: 'Latency', x_label: 'Region' }
    const w = mountOne(block); await flushPromises()
    expect(w.find('details').text()).toContain('View data as a table'); expect(w.findAll('tbody tr')).toHaveLength(2)
    expect(w.find('[data-test=chart]').attributes('data-summary')).toContain('bar chart. s: a 1, b 2.')
  })
  it('a text block renders through safe markdown only', () => {
    const w = mountOne({ type: 'text', markdown: '**bold** text' })
    expect(w.html()).toContain('<strong>bold</strong>')
  })
})

describe('BlockRenderer list', () => {
  it('groups consecutive flashcard blocks into a single deck and hides invalid blocks with a notice', () => {
    const w = mount(BlockRenderer, { props: { blocks: [
      { id: 'blk-001', type: 'heading', level: 2, text: 'Cards' },
      { id: 'blk-002', type: 'flashcard', front: 'Q1', back: 'A1' }, { id: 'blk-003', type: 'flashcard', front: 'Q2', back: 'A2' }, { id: 'blk-004', type: 'flashcard', front: 'Q3', back: 'A3' },
      { id: 'blk-005', type: 'video', url: 'https://x' }] }, global: { stubs } })
    expect(w.findAll('[role=group][aria-label=Flashcards]')).toHaveLength(1); expect(w.text()).toContain('Card 1 of 3'); expect(w.text()).toContain('1 content block(s) were hidden')
  })
  it('blocks without ids still render (index fallback)', () => {
    expect(mount(BlockRenderer, { props: { blocks: [{ type: 'heading', level: 2, text: 'No id' }] }, global: { stubs } }).text()).toContain('No id')
  })
})

describe('safety across every v2 block type', () => {
  const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'
  it('blocks carrying markup in any text field are rejected by validation, so they are never rendered', () => {
    const attempts: any[] = [
      { type: 'heading', level: 2, text: evil }, { type: 'text', markdown: evil }, { type: 'list', items: [evil] }, { type: 'table', headers: [evil], rows: [] },
      { type: 'callout', text: evil }, { type: 'image', url: 'https://x.example/a.png', alt: evil }, { type: 'flashcard', front: evil, back: 'b' },
      { type: 'exercise', prompt: evil }, { type: 'citation', label: '[1]', source_title: evil, url: 'https://x.example' }, { type: 'algorithm', name: evil, steps: ['s'] },
      { type: 'chart', categories: ['a', 'b'], series: [{ name: evil, data: [1, 2] }] }]
    const r = parseBlocks(attempts)
    expect(r.blocks).toHaveLength(0); expect(r.rejected).toHaveLength(attempts.length)
    const w = mount(BlockRenderer, { props: { blocks: attempts }, global: { stubs } })
    expect(w.element.querySelector('script')).toBeNull(); expect(w.element.querySelector('img')).toBeNull(); expect((window as any).__pwned).toBeUndefined()
  })
  it('code and pseudocode are rendered as text, never as HTML', () => {
    const w = mountOne({ type: 'code', language: 'html', code: '<script>window.__pwned=1</script><b>x</b>' })
    expect(w.element.querySelector('script')).toBeNull(); expect(w.text()).toContain('<script>')
  })
})

describe('MathBlock', () => {
  it('shows the LaTeX source (as text) when KaTeX cannot render it, never HTML, never nothing', async () => {
    const w = mount(MathBlock, { props: { latex: '\\frac{a}{b' } })
    await vi.waitFor(() => expect(w.find('pre code').exists()).toBe(true), { timeout: 4000 })
    expect(w.find('pre code').text()).toBe('\\frac{a}{b'); expect(w.find('pre').attributes('role')).toBe('note')
  })
  it('renders valid LaTeX with KaTeX and lists variable meanings', async () => {
    const w = mount(MathBlock, { props: { latex: '\\frac{a}{b}', variables: [{ symbol: 'a', meaning: 'numerator' }] } })
    await vi.waitFor(() => expect(w.find('.katex').exists()).toBe(true), { timeout: 4000 })
    expect(w.find('.katex').exists()).toBe(true); expect(w.find('dl').text()).toContain('numerator')
  })
})

describe('chart option builders', () => {
  const base: any = { type: 'chart', categories: ['a', 'b', 'c'], series: [{ name: 's1', data: [1, 2, 3] }, { name: 's2', data: [3, 2, 1] }] }
  it('bar/line use category axes and one series per input series; pie uses the first series', () => {
    const bar = chartOption({ ...base, chart_type: 'bar' }) as any
    expect(bar.xAxis.data).toEqual(['a', 'b', 'c']); expect(bar.series.map((s: any) => s.name)).toEqual(['s1', 's2']); expect(bar.legend).toBeTruthy()
    const pie = chartOption({ ...base, chart_type: 'pie' }) as any
    expect(pie.series[0].data).toEqual([{ name: 'a', value: 1 }, { name: 'b', value: 2 }, { name: 'c', value: 3 }])
    expect((chartOption({ ...base, series: [base.series[0]], chart_type: 'line' }) as any).legend).toBeUndefined()
  })
  it('summary lists every value (text alternative)', () => { expect(chartSummary({ ...base, chart_type: 'line' })).toContain('s2: a 3, b 2, c 1') })
})

describe('display safety (no leaked objects)', () => {
  it('never prints [object Object] or container dumps', () => {
    for (const v of [{ title: 'Caching', reason: 'fast' }, { a: { b: [1, { c: 2 }] } }, [{ name: 'x' }, 'y'], "{'overview': 'hi', 'flag': True, 'none': None}", '[{"step": "one"}, {"step": "two"}]']) {
      const t = toDisplayText(v); expect(looksLeaked(t), t).toBe(false); expect(t.length).toBeGreaterThan(0)
    }
    expect(toDisplayText({ title: 'Caching', reason: 'fast' })).toBe('Caching — reason: fast'); expect(toDisplayText(null)).toBe(''); expect(toDisplayText(3)).toBe('3')
    expect(toDisplayText("{'a': 1}")).toBe('a: 1')
    expect(toDisplayText('Plain {curly} text')).toBe('Plain {curly} text')
  })
})
