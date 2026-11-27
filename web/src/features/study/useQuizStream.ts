import { onScopeDispose, ref } from 'vue'
import { postSse } from '@/services/sse'
import { ApiError } from '@/services/http'
import { usePipeline } from '@/features/pipeline/usePipeline'
import type { LegacyQuiz } from '@/types/api'

/** Quiz generation as an observable pipeline (plan → generate → review → sign). Returns the quiz on success. */
export function useQuizStream() {
  const pipeline = usePipeline()
  const error = ref<string | null>(null)
  let ctrl: AbortController | null = null

  function stop() { if (ctrl) { ctrl.abort(); ctrl = null; pipeline.failUnfinished('Stopped by you'); pipeline.end(true) } }
  onScopeDispose(() => ctrl?.abort())

  async function run(topic: string): Promise<LegacyQuiz | null> {
    stop(); pipeline.begin(); error.value = null; ctrl = new AbortController()
    let quiz: LegacyQuiz | null = null
    try {
      await postSse('/learning/generate-quiz/stream', { topic }, {
        signal: ctrl.signal,
        onEvent: ({ event, data }) => {
          if (event === 'meta') pipeline.setPlan(data.stages)
          else if (event === 'stage') pipeline.onStage(data)
          else if (event === 'done') quiz = data.quiz
          else if (event === 'error') error.value = data.message
        },
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') return null
      error.value = e instanceof ApiError ? e.message : 'The connection was interrupted.'
    }
    ctrl = null
    if (!quiz && !error.value) error.value = 'The connection closed before the quiz was ready.'
    if (!quiz) pipeline.failUnfinished('Did not run')
    pipeline.end()
    return quiz
  }
  return { ...pipeline, error, run, stop }
}
