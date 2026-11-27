# ADR 0012: ECharts for chart blocks (no Chart.js)

**Status:** accepted (decision N2/D8) · **Context:** the master prompt suggests Chart.js; the web app already bundles ECharts (lazy-loaded, themed, accessible wrapper).

## Decision
Chart blocks render with ECharts. Adding Chart.js would duplicate a dependency and a theme/accessibility layer for no new capability.

## Consequences
One charting stack to maintain; chart blocks reuse the existing lazy loading and text-alternative pattern; the choice can be revisited if a needed chart type is unsupported.
