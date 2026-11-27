import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { conceptsOf, objectivesFrom, progressOf, readMinutes, takeawaysFrom, textOf, wordCount } from './derive'
import { etaSeconds, monitorRows, summarizeRows, type ServerRow, type TaskRow } from './monitor'
import PipelineMonitor from './PipelineMonitor.vue'
import RichSection from './RichSection.vue'
import { toSegments } from '@/features/content/markdown'
import type { PipelineStage } from '@/features/pipeline/pipeline'

const core = [{ key: 'summary', title: 'Overview' }, { key: 'key_concepts', title: 'Key Concepts' }, { key: 'deep_dive', title: 'Deep Dive' }, { key: 'flashcards', title: 'Flashcards' }]
const srv = (o: Partial<ServerRow> & { section_key: string }): ServerRow => ({ title: o.section_key, status: 'COMPLETED', source: 'generated', input_tokens: 100, output_tokens: 200, latency_ms: 4000, attempts: 1, error: null, ...o })
const stage = (id: string, status: PipelineStage['status'], extra: Partial<PipelineStage> = {}): PipelineStage => ({ id: `section:${id}`, label: id, status, ...extra })

describe('reading helpers (deterministic, no model)', () => {
  it('counts words and reading time from any content shape', () => {
    expect(wordCount('')).toBe(0); expect(wordCount('  a b   c ')).toBe(3)
    expect(readMinutes('word '.repeat(450))).toBe(2); expect(readMinutes('short')).toBe(1); expect(readMinutes('')).toBe(0)
    expect(textOf([{ term: 'A', definition: 'b c' }, { x: ['d'] }])).toBe('A b c d'); expect(readMinutes([{ term: 'a', definition: 'b' }])).toBe(1)
  })
  it('derives objectives from key concepts without duplicates and caps them', () => {
    const cs = conceptsOf([{ term: 'Agents', definition: 'x' }, { term: 'agents', definition: 'dup' }, { term: 'Tools', definition: 'y' }, null, { definition: 'no term' }])
    expect(cs.map((c) => c.term)).toEqual(['Agents', 'agents', 'Tools']); expect(objectivesFrom(cs)).toEqual(['Understand Agents', 'Understand Tools'])
    expect(objectivesFrom(Array.from({ length: 20 }, (_, i) => ({ term: `T${i}`, definition: '' })), 6)).toHaveLength(6); expect(objectivesFrom([])).toEqual([]); expect(conceptsOf('nope')).toEqual([])
  })
  it('takes takeaways from the summary bullets, else from its first sentences, as plain text', () => {
    expect(takeawaysFrom('Intro.\n\n- **Decorators** wrap functions to add behaviour\n- They run at definition time, not call time\n- short')).toEqual(['Decorators wrap functions to add behaviour', 'They run at definition time, not call time'])
    const prose = takeawaysFrom('## Title\n\nPython decorators wrap a function and return a new one. They are applied when the function is defined. Another long enough sentence appears here too. A fourth one is cut.')
    expect(prose).toHaveLength(3); expect(prose[0]).toBe('Python decorators wrap a function and return a new one.'); expect(takeawaysFrom('')).toEqual([])
  })
  it('computes progress from understood / opened state', () => {
    const p = progressOf(['a', 'b', 'c', 'd'], { a: { understood: true, opened_at: 'x' }, b: { opened_at: 'x' }, z: { understood: true } })
    expect(p).toEqual({ total: 4, understood: 1, opened: 2, understoodPct: 25, readingPct: 50 }); expect(progressOf([], {}).understoodPct).toBe(0)
  })
})

describe('monitor mapping: live SSE events win while running, the persisted report explains everything after a refresh', () => {
  it('maps live stage events to queued / running / completed / reused / failed', () => {
    const live = [stage('summary', 'done', { duration_ms: 2100, detail: '320 words' }), stage('key_concepts', 'done', { detail: 'Saved earlier' }), stage('deep_dive', 'running', { detail: 'Writing…' }), stage('flashcards', 'pending')]
    const rows = monitorRows(core, live, undefined, true)
    expect(rows.map((r) => r.status)).toEqual(['completed', 'reused', 'running', 'queued']); expect(rows[0].durationMs).toBe(2100)
    expect(monitorRows(core, [stage('summary', 'failed', { detail: 'Gave up: output was unusable' })], undefined, true)[0]).toMatchObject({ status: 'failed', error: 'Gave up: output was unusable' })
    expect(monitorRows(core, [stage('summary', 'done', { detail: 'From shared saved copy' })], undefined, true)[0].status).toBe('reused')
  })
  it('after a refresh (no live events) the server report decides, including reused vs generated and the last error', () => {
    const rows = monitorRows(core, [], [srv({ section_key: 'summary' }), srv({ section_key: 'key_concepts', source: 'reused', input_tokens: 0, output_tokens: 0, latency_ms: 0 }), srv({ section_key: 'deep_dive', status: 'FAILED', source: null, error: 'section could not be generated', attempts: 2 })], false)
    expect(rows.map((r) => r.status)).toEqual(['completed', 'reused', 'failed', 'not_generated'])
    expect(rows[0]).toMatchObject({ durationMs: 4000, tokens: 300 }); expect(rows[2]).toMatchObject({ error: 'section could not be generated', attempts: 2 })
  })
  it('a finished server row is never shown as running because an old live event says so', () => {
    const rows = monitorRows(core, [stage('summary', 'running')], [srv({ section_key: 'summary' })], false)
    expect(rows[0].status).toBe('completed')
  })
  it('summarises progress, lists what is missing and estimates the time left only from measured durations', () => {
    const rows: TaskRow[] = [{ key: 'a', title: 'A', status: 'completed', durationMs: 4000 }, { key: 'b', title: 'B', status: 'completed', durationMs: 6000 }, { key: 'c', title: 'C', status: 'running' }, { key: 'd', title: 'D', status: 'queued' }, { key: 'e', title: 'E', status: 'failed' }, { key: 'f', title: 'F', status: 'reused' }]
    expect(summarizeRows(rows)).toMatchObject({ total: 6, finished: 3, failed: 1, running: 1, reused: 1, pct: 50, missing: ['e'] })
    expect(etaSeconds(rows)).toBe(10); expect(etaSeconds([{ key: 'a', title: 'A', status: 'queued' }])).toBeNull(); expect(etaSeconds(rows.filter((r) => r.status === 'completed'))).toBeNull()
  })
})

describe('callouts and sanitization in the markdown renderer', () => {
  const html = (src: string) => toSegments(src).filter((s) => s.kind === 'html').map((s: any) => s.html).join('')
  it('renders Warning / Best practice / Tip / Note / Important blockquotes as styled callouts', () => {
    expect(html('> **Warning:** never trust input')).toMatch(/<blockquote class="callout callout-warning" data-callout="warning">/)
    expect(html('> **Best practice:** pin versions')).toContain('callout-practice'); expect(html('> **Tip:** use a venv')).toContain('callout-tip')
    expect(html('> **Note:** x')).toContain('callout-note'); expect(html('> **Important:** y')).toContain('callout-important'); expect(html('> **Caution** careful')).toContain('callout-warning')
  })
  it('leaves ordinary quotes alone and never lets markup through', () => {
    expect(html('> just a quote')).not.toContain('callout')
    const evil = html('> **Warning:** <img src=x onerror=alert(1)> <script>alert(1)</script> [x](javascript:alert(1))')
    expect(evil).toContain('callout-warning'); expect(evil).not.toMatch(/<img|<script|href="javascript/i)          // no real tag, no executable link
    expect(evil).toContain('&lt;script&gt;'); expect(evil).toContain('&lt;img')                                            // the text is shown, escaped and inert
  })
  it('keeps code and mermaid inside callout-bearing documents as their own segments', () => {
    const kinds = toSegments('> **Tip:** hi\n\n```mermaid\nflowchart TD\n A-->B\n```\n\n```python\nprint(1)\n```').map((s) => s.kind)
    expect(kinds).toEqual(['html', 'mermaid', 'code'])
  })
})

describe('PipelineMonitor', () => {
  const rows: TaskRow[] = [{ key: 'a', title: 'Plan', status: 'completed', durationMs: 2000, tokens: 300 }, { key: 'b', title: 'Concepts', status: 'reused' }, { key: 'c', title: 'Deep dive', status: 'running' }, { key: 'd', title: 'Cards', status: 'queued' },
    { key: 'e', title: 'Roadmap', status: 'failed', error: 'Gave up: output was unusable', attempts: 2 }]
  it('shows a compact bar with the real percentage and a collapsible per-task list with statuses, time, tokens and errors', async () => {
    const w = mount(PipelineMonitor, { props: { rows, running: true, topic: 'Agentic AI', tokens: { input: 10, output: 20 } } })
    expect(w.text()).toContain('Running in background'); expect(w.text()).toContain('40%'); expect(w.text()).toContain('Agentic AI'); expect(w.find('[data-test=pipeline-details]').exists()).toBe(false)
    await w.get('button[aria-label="Expand details"]').trigger('click')
    const d = w.get('[data-test=pipeline-details]').text()
    for (const t of ['Completed · 2.0s · 300 tok', 'Reused', 'Running', 'Queued', 'Failed · attempt 2', 'Gave up: output was unusable', 'Tokens used so far: 10 in / 20 out']) expect(d).toContain(t)
    expect(w.get('button[aria-label="Collapse details"]').attributes('aria-expanded')).toBe('true')
  })
  it('offers Cancel while running, and Retry failed / Continue when stopped, with per-task retry', async () => {
    const run = mount(PipelineMonitor, { props: { rows, running: true } }); await run.findAll('button').find((b) => b.text().includes('Cancel'))!.trigger('click'); expect(run.emitted('cancel')).toHaveLength(1)
    const idle = mount(PipelineMonitor, { props: { rows, running: false } })
    expect(idle.text()).toContain('Needs attention'); await idle.findAll('button').find((b) => b.text().includes('Retry failed'))!.trigger('click'); expect(idle.emitted('continue')).toHaveLength(1)
    await idle.get('button[aria-label="Expand details"]').trigger('click'); await idle.get('button[aria-label="Retry Roadmap"]').trigger('click'); expect(idle.emitted('retry')![0]).toEqual(['e'])
    expect(mount(PipelineMonitor, { props: { rows: rows.slice(0, 2), running: false } }).text()).toContain('Complete')
    expect(mount(PipelineMonitor, { props: { rows: [{ key: 'a', title: 'A', status: 'completed' }, { key: 'b', title: 'B', status: 'not_generated' }], running: false } }).findAll('button').some((b) => b.text().includes('Continue generating'))).toBe(true)
  })
})

describe('RichSection', () => {
  const stubs = { MarkdownView: { props: ['source'], template: '<div data-test="md">{{ source }}</div>' }, FlashcardDeck: { props: ['cards'], template: '<div data-test="deck">{{ cards.length }} cards</div>' } }
  const concepts = [{ term: 'Agents', definition: 'Autonomous loops' }, { term: 'Tools', definition: 'Callable functions' }]
  it('summary shows objectives derived from concepts and takeaways derived from its own bullets', () => {
    const w = mount(RichSection, { props: { sectionKey: 'summary', value: 'Intro text.\n\n- Agents plan before they act on a goal\n- Tools extend what an agent can do', concepts }, global: { stubs } })
    expect(w.get('[aria-label="Learning objectives"]').text()).toContain('Understand Agents'); expect(w.get('[aria-label="Key takeaways"]').text()).toContain('Agents plan before they act on a goal')
  })
  it('renders concept cards, flashcards and structured recommendations, and plain prose through markdown', () => {
    expect(mount(RichSection, { props: { sectionKey: 'key_concepts', value: concepts, concepts }, global: { stubs } }).text()).toContain('Callable functions')
    expect(mount(RichSection, { props: { sectionKey: 'flashcards', value: [{ question: 'q', answer: 'a' }], concepts: null }, global: { stubs } }).get('[data-test=deck]').text()).toBe('1 cards')
    const rec = mount(RichSection, { props: { sectionKey: 'recommendations', value: { weak_areas: ['Joins'], next_topics: [{ title: 'Indexes' }], practice_suggestions: ['Write 5 queries'] }, concepts: null }, global: { stubs } }).text()
    for (const t of ['Joins', 'Indexes', 'Write 5 queries']) expect(rec).toContain(t)
    const prose = mount(RichSection, { props: { sectionKey: 'deep_dive', value: '## Deep', concepts }, global: { stubs } }); expect(prose.get('[data-test=md]').text()).toBe('## Deep'); expect(prose.find('[aria-label="Learning objectives"]').exists()).toBe(false)
  })
})
