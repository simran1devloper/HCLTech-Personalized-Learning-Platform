import type { AnalyticsSummary } from '@/types/api'
import { dateShort } from '@/utils/format'

/** Pure option builders: backend data in, ECharts option out (unit-tested; no invented values). */
export function weeklyActivityOption(s: AnalyticsSummary) {
  const w = s.weekly_activity
  return {
    xAxis: { type: 'category', data: w.map((x) => dateShort(x.week_start)), axisTick: { alignWithLabel: true } },
    yAxis: [{ type: 'value', minInterval: 1, name: 'Assessments' }, { type: 'value', minInterval: 1, name: 'Questions', splitLine: { show: false } }],
    legend: { top: 0 },
    series: [{ name: 'Assessments', type: 'bar', data: w.map((x) => x.assessments) }, { name: 'Questions answered', type: 'line', yAxisIndex: 1, smooth: false, symbol: 'circle', data: w.map((x) => x.questions) }],
  }
}
export function accuracyOption(s: AnalyticsSummary) {
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${Math.round(v)}%` },
    xAxis: { type: 'category', data: s.accuracy_trend.map((x) => dateShort(x.at)) },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ name: 'Score', type: 'line', symbol: 'circle', data: s.accuracy_trend.map((x) => Math.round(x.score * 100)) }],
    dataZoom: s.accuracy_trend.length > 30 ? [{ type: 'inside' }, { type: 'slider', height: 16 }] : undefined,
  }
}
export function proficiencyBarsOption(s: AnalyticsSummary) {
  const rows = s.proficiency_series.map((x) => ({ name: x.skill, v: x.points.at(-1)?.score ?? 0 })).sort((a, b) => a.v - b.v)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', min: 0, max: 5, name: 'Proficiency (0–5)' },
    yAxis: { type: 'category', data: rows.map((r) => r.name) },
    series: [{ name: 'Proficiency', type: 'bar', data: rows.map((r) => +r.v.toFixed(2)), label: { show: true, position: 'right', formatter: '{c}' } }],
  }
}
export function describeWeekly(s: AnalyticsSummary): string {
  const total = s.weekly_activity.reduce((a, x) => a + x.assessments, 0)
  return `Assessments completed per week over the last ${s.weekly_activity.length} weeks: ${total} in total.`
}
export function describeAccuracy(s: AnalyticsSummary): string {
  const t = s.accuracy_trend
  return t.length ? `${t.length} assessments; latest score ${Math.round(t.at(-1)!.score * 100)}%.` : 'No completed assessments.'
}
export function describeProficiency(s: AnalyticsSummary): string {
  return s.proficiency_series.map((x) => `${x.skill} ${(x.points.at(-1)?.score ?? 0).toFixed(1)} of 5`).join('; ') || 'No assessed skills.'
}
