<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { useUiStore } from '@/stores/ui'
import Skeleton from '@/components/ui/Skeleton.vue'

/** Lazy, theme-aware, responsive ECharts wrapper. `option` should be built from backend data only.
 *  `summary` is a text alternative (and optional table) for screen readers. */
const props = defineProps<{ option: Record<string, any>; label: string; summary: string; height?: number }>()
const el = shallowRef<HTMLDivElement>()
const chart = shallowRef<any>()
const loading = shallowRef(true)
const ui = useUiStore()
const themeKey = computed(() => ui.theme)
let disposed = false

async function mount() {
  const [{ init }, { baseOption }] = await Promise.all([import('./echarts'), import('./theme')])
  if (disposed || !el.value) return
  chart.value = init(el.value, undefined, { renderer: 'canvas' })
  loading.value = false
  apply()
}
async function apply() {
  if (!chart.value) return
  const { baseOption } = await import('./theme')
  // notMerge:false merges for efficient updates; theme tokens are re-resolved on each apply.
  chart.value.setOption({ ...baseOption(), ...props.option }, { notMerge: true, lazyUpdate: true })
}
onMounted(mount)
watch(() => props.option, apply, { deep: false })
watch(themeKey, () => requestAnimationFrame(apply))
useResizeObserver(el, () => chart.value?.resize())
onBeforeUnmount(() => { disposed = true; chart.value?.dispose(); chart.value = undefined })
</script>

<template>
  <!-- The figure carries the text alternative; ECharts overwrites aria attributes on its own host, so that is hidden. -->
  <figure class="m-0" role="img" :aria-label="`${label}. ${summary}`">
    <div class="relative" :style="{ height: (height ?? 260) + 'px' }">
      <Skeleton v-if="loading" class="absolute inset-0" />
      <div ref="el" aria-hidden="true" class="size-full" />
    </div>
  </figure>
</template>
