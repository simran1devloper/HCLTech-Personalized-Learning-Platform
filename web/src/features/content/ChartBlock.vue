<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import type { ChartBlockData } from '@/schemas/blocks'
import { chartOption, chartSummary } from './chart'

const EChart = defineAsyncComponent(() => import('@/features/charts/EChart.vue'))   // chart library loads on demand
const props = defineProps<{ block: ChartBlockData }>()
const option = computed(() => chartOption(props.block))
const summary = computed(() => chartSummary(props.block))
</script>
<template>
  <figure class="my-3 surface p-3">
    <EChart :option="option" :label="block.title || block.caption || 'Chart'" :summary="summary" :height="240" />
    <figcaption v-if="block.caption || block.source" class="mt-1 text-xs text-muted-foreground">{{ block.caption }}<template v-if="block.source"> · Source: {{ block.source }}</template></figcaption>
    <details class="mt-2 text-xs"><summary class="cursor-pointer text-primary">View data as a table</summary>
      <table class="prose-lite mt-1 w-full"><thead><tr><th scope="col">{{ block.x_label || 'Category' }}</th><th v-for="s in block.series" :key="s.name" scope="col">{{ s.name }}</th></tr></thead>
        <tbody><tr v-for="(c, i) in block.categories" :key="c + i"><th scope="row">{{ c }}</th><td v-for="s in block.series" :key="s.name" class="tabular-nums">{{ s.data[i] }}</td></tr></tbody></table></details>
  </figure>
</template>
