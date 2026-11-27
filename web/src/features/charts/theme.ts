/** Resolves design tokens (CSS variables) to concrete colours so charts follow the light/dark theme. */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
export function chartTheme() {
  const text = cssVar('--muted-foreground'), border = cssVar('--border')
  return {
    palette: [1, 2, 3, 4, 5].map((i) => cssVar(`--chart-${i}`)),
    text, border, foreground: cssVar('--foreground'), card: cssVar('--card'),
  }
}
export function baseOption() {
  const t = chartTheme()
  return {
    color: t.palette, backgroundColor: 'transparent', textStyle: { color: t.text, fontFamily: 'inherit' },
    aria: { enabled: true, decal: { show: true } }, // patterns + generated description: not colour-only
    tooltip: { trigger: 'axis', backgroundColor: t.card, borderColor: t.border, textStyle: { color: t.foreground } },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
  }
}
