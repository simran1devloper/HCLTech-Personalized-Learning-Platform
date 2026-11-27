import { z } from 'zod'

/** Mirrors backend/app/domains/content_engine/schema.py (schema v2, 15 block types) plus the v1 types that older
 *  content may still contain (flashcards, case_study, tutorial). Anything not matching is rejected before rendering. */
export const SCHEMA_VERSION = '2.0'
export const V2_TYPES = ['heading', 'text', 'list', 'table', 'callout', 'mermaid', 'math', 'chart', 'image', 'code', 'algorithm', 'quiz', 'flashcard', 'exercise', 'citation'] as const

// Same patterns as the backend (assert_safe_text / latex policy). Defence in depth: the backend already rejects these.
const UNSAFE = /<\s*\/?\s*(script|iframe|object|embed|style|link|meta|form|svg|math)\b|javascript\s*:|data\s*:\s*text\/html|\bon\w+\s*=/i
const FORBIDDEN_TEX = /\\(href|url|includegraphics|input|include|write|immediate|openin|openout|read|def|gdef|edef|xdef|let|futurelet|newcommand|renewcommand|providecommand|newenvironment|csname|endcsname|catcode|expandafter|noexpand|message|errmessage|verb|show|html(Class|Id|Style|Data))(?![A-Za-z])/
const httpsUrl = z.string().url().refine((u) => { try { const p = new URL(u); return p.protocol === 'https:' && !p.username && !p.password } catch { return false } }, 'url must be https without credentials')
const safe = (max: number, min = 0) => z.string().min(min).max(max).refine((s) => !UNSAFE.test(s), 'unsafe markup rejected')
// Mirror of backend validate_mermaid(): supported diagram types, no scripting directives, balanced brackets.
const MERMAID_TYPES = ['flowchart', 'graph', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'stateDiagram-v2', 'erDiagram', 'gantt', 'pie', 'mindmap', 'journey']
const MERMAID_BAD = /(%%\{\s*init|click\s+\w+\s+(call|href)|javascript:|<\s*script|\bon\w+\s*=)/i
export function mermaidProblem(src: string): string | null {
  const t = src.trim()
  if (!t || t.length > 5000) return 'mermaid source empty or too long'
  if (MERMAID_BAD.test(t)) return 'mermaid directive not allowed'
  const lines = t.split(/\r?\n/)
  const kind = lines[0].trim().split(/\s+/)[0]
  if (!MERMAID_TYPES.includes(kind)) return 'unsupported or missing mermaid diagram type'
  const unquoted = t.replace(/"[^"\n]*"/g, '')
  for (const [o, c] of [['[', ']'], ['(', ')'], ['{', '}']]) if (unquoted.split(o).length !== unquoted.split(c).length) return 'unbalanced brackets in mermaid source'
  if ((kind === 'flowchart' || kind === 'graph') && lines.length < 2) return 'flowchart has no nodes'
  return null
}
const base = { id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$/).optional(), title: safe(200).optional() }

const heading = z.object({ ...base, type: z.literal('heading'), level: z.number().int().min(1).max(4).default(2), text: safe(200, 1) })
const text = z.object({ ...base, type: z.literal('text'), markdown: safe(20000, 1) })
const list = z.object({ ...base, type: z.literal('list'), ordered: z.boolean().default(false), items: z.array(safe(1000).refine((s) => s.trim().length > 0)).min(1).max(50) })
const table = z.object({ ...base, type: z.literal('table'), headers: z.array(safe(200)).min(1).max(12), rows: z.array(z.array(safe(1000))).max(100), caption: safe(300).optional() })
  .refine((t) => t.rows.every((r) => r.length === t.headers.length), 'table rows must match header width')
const callout = z.object({ ...base, type: z.literal('callout'), variant: z.enum(['info', 'tip', 'warning', 'danger', 'note']).default('info'), text: safe(2000, 1) })
const mermaid = z.object({ ...base, type: z.literal('mermaid'), source: z.string().superRefine((v, ctx) => { const p = mermaidProblem(v); if (p) ctx.addIssue({ code: 'custom', message: p }) }), caption: safe(300).optional() })
const math = z.object({ ...base, type: z.literal('math'), latex: z.string().min(1).max(1000).refine((s) => !FORBIDDEN_TEX.test(s), 'latex command not allowed'),
  display: z.boolean().default(true), variables: z.array(z.object({ symbol: safe(60), meaning: safe(300) })).max(20).default([]) })
const chart = z.object({ ...base, type: z.literal('chart'), chart_type: z.enum(['bar', 'line', 'pie', 'scatter']).default('bar'), categories: z.array(safe(100)).min(2).max(200),
  series: z.array(z.object({ name: safe(100, 1), data: z.array(z.number().finite()).min(1).max(200) })).min(1).max(8),
  x_label: safe(100).optional(), y_label: safe(100).optional(), caption: safe(300).optional(), source: safe(300).optional() })
  .refine((c) => c.series.every((s) => s.data.length === c.categories.length), 'every series must have one value per category')
const image = z.object({ ...base, type: z.literal('image'), url: httpsUrl, alt: safe(300, 5), caption: safe(300).optional() })
const code = z.object({ ...base, type: z.literal('code'), language: z.string().regex(/^[a-zA-Z0-9_+#-]{1,20}$/), code: z.string().min(1).max(10000), filename: z.string().max(120).optional(), caption: safe(300).optional() })
const algorithm = z.object({ ...base, type: z.literal('algorithm'), name: safe(120, 1), steps: z.array(safe(1000)).min(1).max(40), time_complexity: safe(60).optional(), space_complexity: safe(60).optional(), pseudocode: z.string().max(5000).optional() })
const quiz = z.object({ ...base, type: z.literal('quiz'), question_ids: z.array(z.string()).min(1).max(50) })
const flashcard = z.object({ ...base, type: z.literal('flashcard'), front: safe(500, 1), back: safe(1500, 1), tags: z.array(safe(40)).max(10).default([]) })
const exercise = z.object({ ...base, type: z.literal('exercise'), prompt: safe(3000, 1), hints: z.array(safe(1000)).max(6).default([]), solution_outline: safe(3000).default(''), difficulty: z.number().int().min(1).max(5).optional() })
const citation = z.object({ ...base, type: z.literal('citation'), label: safe(20, 1), source_title: safe(300, 1), url: httpsUrl, quote: safe(300).optional(), accessed_at: z.string().max(30).optional(), source_id: z.string().max(64).optional() })
// v1 types still accepted for older stored content
const flashcards = z.object({ ...base, type: z.literal('flashcards'), cards: z.array(z.object({ front: safe(500), back: safe(1500) })).min(1).max(50) })
const caseStudy = z.object({ ...base, type: z.literal('case_study'), title: safe(300), scenario: safe(5000), questions: z.array(safe(500)).default([]) })
const tutorial = z.object({ ...base, type: z.literal('tutorial'), steps: z.array(safe(1000)).min(1).max(30) })

const validators = { heading, text, list, table, callout, mermaid, math, chart, image, code, algorithm, quiz, flashcard, exercise, citation, flashcards, case_study: caseStudy, tutorial } as const
export type Block =
  | z.infer<typeof heading> | z.infer<typeof text> | z.infer<typeof list> | { type: 'table'; headers: string[]; rows: string[][]; caption?: string; id?: string; title?: string }
  | z.infer<typeof callout> | z.infer<typeof mermaid> | z.infer<typeof math> | z.infer<typeof chart> | z.infer<typeof image> | z.infer<typeof code> | z.infer<typeof algorithm>
  | z.infer<typeof quiz> | z.infer<typeof flashcard> | z.infer<typeof exercise> | z.infer<typeof citation> | z.infer<typeof flashcards> | z.infer<typeof caseStudy> | z.infer<typeof tutorial>
export type BlockType = Block['type']
export type ChartBlockData = z.infer<typeof chart>

export interface ParsedBlocks { blocks: Block[]; rejected: { index: number; reason: string }[] }
/** Validates each block independently so one malformed block cannot take down the whole lesson. */
export function parseBlocks(input: unknown): ParsedBlocks {
  const out: ParsedBlocks = { blocks: [], rejected: [] }
  if (!Array.isArray(input)) { out.rejected.push({ index: -1, reason: 'blocks is not an array' }); return out }
  input.forEach((raw, index) => {
    const type = (raw as { type?: unknown })?.type
    const v = typeof type === 'string' ? (validators as Record<string, z.ZodTypeAny>)[type] : undefined
    if (!v) { out.rejected.push({ index, reason: `unsupported block type: ${String(type)}` }); return }
    const r = v.safeParse(raw)
    if (r.success) out.blocks.push(r.data as Block)
    else out.rejected.push({ index, reason: r.error.issues[0]?.message ?? 'invalid block' })
  })
  return out
}
