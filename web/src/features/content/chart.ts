import type { ChartBlockData } from '@/schemas/blocks'

/** Pure builders: validated block in, ECharts option and text alternative out. */
export function chartOption(b: ChartBlockData): Record<string, any> {
  if (b.chart_type === 'pie') {
    const s = b.series[0]
    return { tooltip: { trigger: 'item' }, legend: { top: 0 }, series: [{ type: 'pie', radius: '60%', data: b.categories.map((name, i) => ({ name, value: s.data[i] })) }] }
  }
  if (b.chart_type === 'scatter') {
    return { tooltip: { trigger: 'item' }, xAxis: { type: 'category', data: b.categories, name: b.x_label }, yAxis: { type: 'value', name: b.y_label },
      series: b.series.map((s) => ({ name: s.name, type: 'scatter', data: s.data })) }
  }
  return { tooltip: { trigger: 'axis' }, legend: b.series.length > 1 ? { top: 0 } : undefined, xAxis: { type: 'category', data: b.categories, name: b.x_label },
    yAxis: { type: 'value', name: b.y_label }, series: b.series.map((s) => ({ name: s.name, type: b.chart_type, data: s.data })) }
}
export function chartSummary(b: ChartBlockData): string {
  const parts = b.series.map((s) => `${s.name}: ${b.categories.map((c, i) => `${c} ${s.data[i]}`).join(', ')}`)
  return `${b.chart_type} chart. ${parts.join('. ')}.`
}
