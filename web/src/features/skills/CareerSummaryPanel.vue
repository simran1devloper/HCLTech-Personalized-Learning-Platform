<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, ArrowRight, Flag, Lightbulb, Target } from 'lucide-vue-next'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import CardContent from '@/components/ui/CardContent.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import type { CareerSkillGraph } from '@/types/api'
import { ACTION_LABEL, criticalGaps, hoursToTargets, nextMilestone } from './summary'

const props = defineProps<{ graph: CareerSkillGraph }>()
const emit = defineEmits<{ select: [id: string]; act: [id: string, action: string] }>()
const names = computed(() => new Map(props.graph.nodes.map((n) => [n.skill_id, n.name])))
const gaps = computed(() => criticalGaps(props.graph.nodes))
const milestone = computed(() => nextMilestone(props.graph.nodes))
const hours = computed(() => hoursToTargets(props.graph.nodes))
const tone = { high: 'danger', medium: 'warning', low: 'neutral' } as const
</script>
<template>
  <Card class="flex flex-col" data-test="career-summary">
    <CardHeader :title="graph.role?.title ?? 'All skills'" :description="graph.role ? 'Career summary' : 'Every skill in the catalogue'" />
    <CardContent class="space-y-5 text-sm">
      <p v-if="graph.role?.description" class="text-xs text-muted-foreground">{{ graph.role.description }}</p>
      <p class="text-xs text-muted-foreground">
        <strong class="text-foreground">{{ graph.metrics.mastered }}</strong> of {{ graph.metrics.skills_total }} skills mastered ·
        <strong class="text-foreground">{{ graph.metrics.skills_covered }}</strong> assessed<template v-if="hours"> · about <strong class="text-foreground">{{ hours }} hrs</strong> of study left to reach every target</template>.
        Select a skill to see its prerequisites and what it unlocks.</p>

      <p v-if="!graph.role" class="rounded-lg border bg-accent/50 p-3 text-xs">Pick a career in the selector above to see readiness, critical skill gaps and recommended next skills for that role.</p>
      <section v-if="graph.role && milestone" aria-labelledby="ms">
        <h3 id="ms" class="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground"><Flag class="size-3.5" aria-hidden="true" />Next milestone</h3>
        <button type="button" class="w-full rounded-lg border bg-accent/50 p-3 text-left hover:bg-accent" @click="emit('select', milestone.node.skill_id)">
          <span class="flex items-center justify-between font-medium">{{ milestone.node.name }}<ArrowRight class="size-4 text-muted-foreground" aria-hidden="true" /></span>
          <span class="mt-0.5 block text-xs text-muted-foreground">{{ milestone.node.critical ? 'Critical requirement' : 'Career requirement' }} · about {{ milestone.hours }} hrs
            <template v-if="milestone.prerequisites.length"> · first: {{ milestone.prerequisites.map((p) => p.name).slice(0, 2).join(', ') }}</template></span>
        </button>
      </section>
      <p v-else-if="graph.role" class="flex items-center gap-2 rounded-lg border bg-success/10 p-3 text-xs text-success"><Target class="size-4" aria-hidden="true" />Every requirement of this career is mastered.</p>

      <section v-if="graph.role" aria-labelledby="cg">
        <h3 id="cg" class="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground"><AlertTriangle class="size-3.5" aria-hidden="true" />Critical skill gaps</h3>
        <p v-if="!gaps.length" class="text-xs text-muted-foreground">No critical gaps.</p>
        <ul v-else class="space-y-1">
          <li v-for="g in gaps" :key="g.skill_id"><button type="button" class="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left hover:bg-accent" @click="emit('select', g.skill_id)">
            <span>{{ g.name }}</span><span class="text-xs tabular-nums text-muted-foreground">{{ g.proficiency.toFixed(1) }} / {{ g.target }}</span></button></li>
        </ul>
      </section>

      <section v-if="graph.role" aria-labelledby="rn">
        <h3 id="rn" class="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground"><Lightbulb class="size-3.5" aria-hidden="true" />Recommended next skills</h3>
        <p v-if="!graph.recommendations.length" class="text-xs text-muted-foreground">Nothing to recommend: every skill is mastered.</p>
        <ol v-else class="space-y-2">
          <li v-for="r in graph.recommendations.slice(0, 4)" :key="r.skill_id" class="rounded-lg border p-3" :data-rec="r.skill_id">
            <div class="flex items-start justify-between gap-2">
              <button type="button" class="text-left font-medium hover:underline" @click="emit('select', r.skill_id)">{{ r.rank }}. {{ r.name }}</button>
              <Badge :tone="tone[r.priority]" class="capitalize">{{ r.priority }}</Badge>
            </div>
            <p class="mt-1 text-xs text-muted-foreground">{{ r.explanation }}</p>
            <p v-if="r.missing_prerequisites.length" class="mt-1 text-xs text-warning">Missing: {{ r.missing_prerequisites.map((m) => names.get(m) ?? m).join(', ') }}</p>
            <Button size="sm" :variant="r.suggested_action === 'learn_prerequisites' ? 'outline' : 'default'" class="mt-2" @click="emit('act', r.next_skill_id ?? r.skill_id, r.suggested_action)">
              {{ ACTION_LABEL[r.suggested_action] }}<template v-if="r.next_skill_id">: {{ names.get(r.next_skill_id) }}</template></Button>
          </li>
        </ol>
      </section>
    </CardContent>
  </Card>
</template>
