import type { AnalyticsSummary } from '@/types/api'
import { dateShort } from '@/utils/format'

export function progressionOption(s: AnalyticsSummary) {
  const series = s.proficiency_series.slice(0, 12) // bounded legend/series count
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 0 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', min: 0, max: 5, name: 'Proficiency' },
    dataZoom: series.some((x) => x.points.length > 40) ? [{ type: 'inside' }, { type: 'slider', height: 16 }] : undefined,
    series: series.map((x) => ({ name: x.skill, type: 'line', symbol: 'circle', sampling: 'lttb', data: x.points.map((p) => [p.at, +p.score.toFixed(2)]) })),
  }
}
export function readinessTrendOption(s: AnalyticsSummary) {
  const t = s.career?.readiness_trend ?? []
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${Math.round(v)}%` },
    legend: { top: 0 }, xAxis: { type: 'category', data: t.map((x) => dateShort(x.date)) },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ name: 'Readiness', type: 'line', symbol: 'circle', data: t.map((x) => Math.round(x.readiness * 100)) },
             { name: 'Evidence confidence', type: 'line', symbol: 'diamond', lineStyle: { type: 'dashed' }, data: t.map((x) => Math.round(x.evidence_confidence * 100)) }],
  }
}
export function retentionOption(s: AnalyticsSummary) {
  const r = [...s.retention].sort((a, b) => a.delta - b.delta)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', name: 'Change in proficiency between first and latest assessment' },
    yAxis: { type: 'category', data: r.map((x) => x.skill) },
    series: [{ name: 'Change', type: 'bar', data: r.map((x) => ({ value: x.delta, label: { show: true, formatter: `${x.delta > 0 ? '+' : ''}${x.delta.toFixed(2)}` } })) }],
  }
}
export function roadmapOption(s: AnalyticsSummary) {
  const r = s.roadmap_completion.roadmaps
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, legend: { top: 0 },
    xAxis: { type: 'category', data: r.map((x) => `${x.goal.replace(/-/g, ' ')} v${x.version}`) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ name: 'Done', type: 'bar', stack: 'm', data: r.map((x) => x.done) }, { name: 'Remaining', type: 'bar', stack: 'm', data: r.map((x) => x.total - x.done) }],
  }
}
export const describe = {
  progression: (s: AnalyticsSummary) => s.proficiency_series.map((x) => `${x.skill}: ${x.points.length} data points, latest ${x.points.at(-1)?.score.toFixed(1)}`).join('; '),
  readiness: (s: AnalyticsSummary) => (s.career?.readiness_trend ?? []).map((x) => `${x.date}: ${Math.round(x.readiness * 100)}%`).join('; '),
  retention: (s: AnalyticsSummary) => s.retention.map((x) => `${x.skill} ${x.delta > 0 ? '+' : ''}${x.delta.toFixed(2)}`).join('; '),
  roadmap: (s: AnalyticsSummary) => s.roadmap_completion.roadmaps.map((x) => `${x.goal} v${x.version}: ${x.done} of ${x.total}`).join('; '),
}
