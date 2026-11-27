/** Tree-shaken ECharts. Imported only through dynamic import() from EChart.vue, so it never enters the entry chunk. */
import { use, init, type EChartsType } from 'echarts/core'
import { BarChart, LineChart, RadarChart, GaugeChart } from 'echarts/charts'
import { AriaComponent, DatasetComponent, GridComponent, LegendComponent, MarkLineComponent, TitleComponent, TooltipComponent, DataZoomComponent, RadarComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([BarChart, LineChart, RadarChart, GaugeChart, AriaComponent, DatasetComponent, GridComponent, LegendComponent, MarkLineComponent, TitleComponent, TooltipComponent, DataZoomComponent, RadarComponent, CanvasRenderer])
export { init }
export type { EChartsType }
