import type { ConceptLevel, ConceptResult } from '@/types/api'

export const LEVEL_LABEL: Record<ConceptLevel, string> = { strong: 'Strong', good: 'Good', developing: 'Developing', needs_improvement: 'Needs improvement', unknown: 'Not enough data' }
export const LEVEL_TONE: Record<ConceptLevel, 'success' | 'info' | 'warning' | 'danger' | 'neutral'> = { strong: 'success', good: 'success', developing: 'warning', needs_improvement: 'danger', unknown: 'neutral' }
const COLOR: Record<ConceptLevel, string> = { strong: '#15803d', good: '#16a34a', developing: '#d97706', needs_improvement: '#dc2626', unknown: '#94a3b8' }

/** Horizontal bar chart of concept accuracy (0-100 %). Values are the backend's; concepts with too little evidence are drawn grey and labelled. */
export function conceptAccuracyOption(concepts: ConceptResult[]) {
  const rows = [...concepts].sort((a, b) => a.accuracy - b.accuracy)
  return {
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p: { dataIndex: number }[]) => { const c = rows[p[0].dataIndex]; return `${c.concept}<br/>${Math.round(c.accuracy * 100)}% (${c.questions_attempted} question${c.questions_attempted === 1 ? '' : 's'})<br/>${LEVEL_LABEL[c.proficiency_level]}` } },
    xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    yAxis: { type: 'category', data: rows.map((r) => r.concept) },
    series: [{ name: 'Accuracy', type: 'bar', barMaxWidth: 22, data: rows.map((r) => ({ value: Math.round(r.accuracy * 100), itemStyle: { color: COLOR[r.proficiency_level] } })),
      label: { show: true, position: 'right', formatter: '{c}%' } }],
  }
}

export function describeConcepts(concepts: ConceptResult[]): string {
  return concepts.length ? 'Accuracy by concept: ' + [...concepts].sort((a, b) => a.accuracy - b.accuracy).map((c) => `${c.concept} ${Math.round(c.accuracy * 100)}% (${LEVEL_LABEL[c.proficiency_level]})`).join('; ') + '.' : 'No concept results.'
}
export function formatDuration(seconds: number | undefined): string {
  if (!seconds && seconds !== 0) return '—'
  const m = Math.floor(seconds / 60), s = seconds % 60
  return m ? `${m} min ${s}s` : `${s}s`
}
