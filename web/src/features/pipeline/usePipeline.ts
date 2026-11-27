import { computed, onScopeDispose, ref } from 'vue'
import { applyStage, stateOf, type PipelineStage } from './pipeline'

/** Reactive pipeline run state shared by the streaming hooks: ordered stages, elapsed timer, cancellation flag. */
export function usePipeline() {
  const stages = ref<PipelineStage[]>([])
  const running = ref(false)
  const cancelled = ref(false)
  const elapsedMs = ref(0)
  let started = 0
  let timer: ReturnType<typeof setInterval> | undefined

  const tick = () => { elapsedMs.value = Date.now() - started }
  function begin() {
    stages.value = []; cancelled.value = false; running.value = true; started = Date.now(); elapsedMs.value = 0
    clearInterval(timer); timer = setInterval(tick, 250)
  }
  function end(wasCancelled = false) {
    running.value = false; cancelled.value = wasCancelled; tick(); clearInterval(timer)
  }
  const setPlan = (planned: PipelineStage[]) => { stages.value = planned.map((s) => ({ ...s })) }
  const onStage = (ev: PipelineStage) => { stages.value = applyStage(stages.value, ev) }
  /** Anything still pending/running when a run ends abnormally is marked failed so the UI never lies. */
  function failUnfinished(detail: string) {
    stages.value = stages.value.map((s) => (['pending', 'running', 'retrying'].includes(s.status) ? { ...s, status: 'failed', detail } : s))
  }
  onScopeDispose(() => clearInterval(timer))
  const state = computed(() => stateOf(running.value, stages.value, cancelled.value))
  return { stages, running, cancelled, elapsedMs, state, begin, end, setPlan, onStage, failUnfinished }
}
