import { onScopeDispose, ref, watch, type Ref } from 'vue'

/** Returns a ref that follows `source` after `ms` of quiet. Timer is cleared with the owning scope. */
export function useDebounced<T>(source: Ref<T>, ms = 250): Ref<T> {
  const out = ref(source.value) as Ref<T>
  let t: ReturnType<typeof setTimeout> | undefined
  watch(source, (v) => { clearTimeout(t); t = setTimeout(() => (out.value = v), ms) })
  onScopeDispose(() => clearTimeout(t))
  return out
}
