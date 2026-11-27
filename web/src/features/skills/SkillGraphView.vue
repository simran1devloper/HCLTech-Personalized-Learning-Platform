<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue'
import { MarkerType, VueFlow, useVueFlow, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/minimap/dist/style.css'
import { Loader2, Maximize, Minimize, ScanSearch, ZoomIn, ZoomOut } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import type { GraphEdge, GraphNode } from '@/types/api'
import { edgeKind, roleOf, type EdgeKind, type Highlight } from './graph'
import { NODE_H, NODE_W, computeLayout, type Positions } from './layout'
import SkillNode, { type SkillNodeData } from './SkillNode.vue'

const props = defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; highlight: Highlight; matches: Set<string> | null; fullscreen: boolean }>()
const emit = defineEmits<{ select: [id: string | null]; 'toggle-fullscreen': [] }>()
const ID = 'skill-explorer'
const { zoomIn, zoomOut, setCenter, setViewport, dimensions, onNodeClick, onPaneClick } = useVueFlow(ID)
onNodeClick(({ node }) => emit('select', node.id))
onPaneClick(() => emit('select', null))

// ---- layout: depends on topology only (see graph.topologyKey), so status/selection changes never re-run ELK
const positions = shallowRef<Positions>(new Map()); const laying = ref(false); let seq = 0
watch(() => [props.nodes, props.edges] as const, async ([ns, es]) => {
  const mine = ++seq; laying.value = true
  const pos = await computeLayout({ ids: ns.map((n) => n.skill_id), edges: es })
  if (mine !== seq) return // a newer topology superseded this one
  positions.value = pos; laying.value = false; needFit = true
  await nextTick(); fit(250, 0.06, READABLE)
}, { immediate: true })

/** Fit the whole layout in the viewport. Computed from the layout's own bounds (node size is fixed), NOT from rendered nodes: with
 *  virtualised rendering, off-screen nodes are never measured, so Vue Flow's fitView cannot see them. */
const READABLE = 0.6 // smallest zoom at which node text is still legible for the initial view
let needFit = false
function fit(duration = 250, pad = 0.1, minZoom = 0.15) {
  const ps = [...positions.value.values()]; const { width, height } = dimensions.value
  if (!ps.length || !width || !height) return // not laid out / not measured yet: the dimensions watcher below retries
  const minX = Math.min(...ps.map((p) => p.x)), maxX = Math.max(...ps.map((p) => p.x)) + NODE_W, minY = Math.min(...ps.map((p) => p.y)), maxY = Math.max(...ps.map((p) => p.y)) + NODE_H
  const bw = maxX - minX, bh = maxY - minY
  const fitZoom = Math.min(width / (bw * (1 + 2 * pad)), height / (bh * (1 + 2 * pad)))
  const zoom = Math.max(minZoom, Math.min(1.1, fitZoom))
  // When the whole graph cannot be shown at a readable size, anchor its left edge (the foundations) and let the learner pan right.
  const x = fitZoom < minZoom ? width * pad - minX * zoom : width / 2 - (minX + bw / 2) * zoom
  setViewport({ x, y: height / 2 - (minY + bh / 2) * zoom, zoom }, { duration })
  needFit = false
}
watch(dimensions, () => { if (needFit) fit(0, 0.06, READABLE) }) // the container was measured after the first layout
watch(() => props.fullscreen, async () => { await nextTick(); setTimeout(() => fit(150, 0.06, READABLE), 120) })
const virtualise = computed(() => props.nodes.length > 150) // small graphs render fully (and are measured, so the minimap is exact)

const rfNodes = computed<Node<SkillNodeData>[]>(() => props.nodes.flatMap((n) => {
  const p = positions.value.get(n.skill_id); if (!p) return []
  return [{ id: n.skill_id, type: 'skill', position: p, draggable: false, selectable: false, connectable: false, data: { skill: n, role: roleOf(n.skill_id, props.highlight, props.matches), unmet: props.highlight.unmet.has(n.skill_id) } }]
}))
const STYLE: Record<EdgeKind, { stroke: string; width: number; dash?: string; opacity: number }> = {
  normal: { stroke: 'var(--border)', width: 1.5, opacity: 1 }, dimmed: { stroke: 'var(--border)', width: 1, opacity: 0.25 },
  'path-up': { stroke: 'var(--primary)', width: 2.5, opacity: 1 }, 'path-down': { stroke: 'var(--success)', width: 2.5, opacity: 1 },
  unmet: { stroke: 'var(--warning)', width: 2, dash: '6 4', opacity: 1 },
}
const rfEdges = computed<Edge[]>(() => props.edges.flatMap((e) => {
  if (!positions.value.has(e.source) || !positions.value.has(e.target)) return []
  const k = edgeKind(e, props.highlight), st = STYLE[k]
  return [{ id: `${e.source}>${e.target}`, source: e.source, target: e.target, animated: k === 'path-up' && props.highlight.up.size < 25, selectable: false, focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: st.stroke, width: 14, height: 14 }, style: { stroke: st.stroke, strokeWidth: st.width, strokeDasharray: st.dash, opacity: st.opacity }, class: `edge-${k}` }]
}))
const miniColor = (n: Node) => { const d = (n.data as SkillNodeData | undefined)?.skill; return d ? `var(${{ mastered: '--success', in_progress: '--primary', ready: '--primary', blocked: '--warning', not_started: '--muted-foreground', needs_revision: '--revision' }[d.status]})` : 'var(--border)' }

/** Centre the view on a skill (used by search Enter and by selecting from a list). */
function focusNode(id: string) {
  const p = positions.value.get(id); if (p) setCenter(p.x + NODE_W / 2, p.y + NODE_H / 2, { zoom: 1, duration: 300 })
}
defineExpose({ focusNode, fit })
</script>

<template>
  <div class="relative size-full" role="application" aria-label="Skill dependency graph. Arrows point from a prerequisite to the skill that needs it. Use the list view for a keyboard-first alternative.">
    <VueFlow :id="ID" :nodes="rfNodes" :edges="rfEdges" :min-zoom="0.15" :max-zoom="1.8" :nodes-draggable="false" :nodes-connectable="false" :elements-selectable="false"
      :zoom-on-double-click="false" :only-render-visible-elements="virtualise" :fit-view-on-init="false" :delete-key-code="null">
      <template #node-skill="p"><SkillNode :data="p.data" @select="emit('select', $event)" /></template>
      <Background :gap="22" pattern-color="var(--border)" />
      <MiniMap class="!hidden md:!block !rounded-md !border" :width="150" :height="96" :node-color="miniColor" :node-stroke-width="0" pannable zoomable aria-label="Graph minimap" />
    </VueFlow>
    <div v-if="laying" class="absolute inset-0 grid place-items-center bg-background/60 text-sm text-muted-foreground" role="status"><span class="inline-flex items-center gap-2"><Loader2 class="size-4 animate-spin" aria-hidden="true" />Laying out skills…</span></div>
    <div class="absolute bottom-3 left-3 flex flex-col gap-1" role="toolbar" aria-label="Graph controls">
      <Button size="icon" variant="outline" aria-label="Zoom in" @click="zoomIn()"><ZoomIn /></Button>
      <Button size="icon" variant="outline" aria-label="Zoom out" @click="zoomOut()"><ZoomOut /></Button>
      <Button size="icon" variant="outline" aria-label="Fit graph to view" @click="fit(250, 0.06)"><ScanSearch /></Button>
      <Button size="icon" variant="outline" :aria-label="fullscreen ? 'Exit full screen' : 'Full screen'" @click="emit('toggle-fullscreen')"><component :is="fullscreen ? Minimize : Maximize" /></Button>
    </div>
  </div>
</template>
