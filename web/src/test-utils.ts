import { mount, flushPromises, type MountingOptions } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory } from 'vue-router'
import { createAppRouter } from '@/app/router'
import type { Component } from 'vue'

/** Mounts a component with Pinia, a fresh Query cache (no retries) and the real router in memory mode. */
export async function mountApp(component: Component, opts: MountingOptions<any> & { route?: string; queryClient?: QueryClient } = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  const queryClient = opts.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  router.push(opts.route ?? '/'); await router.isReady().catch(() => {})
  const wrapper = mount(component, { ...opts, global: { plugins: [pinia, router, [VueQueryPlugin, { queryClient }]], stubs: { EChart: { props: ['option', 'label', 'summary'], template: '<div data-test="chart" :data-label="label" :data-summary="summary" />' }, ...(opts.global?.stubs as object) }, ...opts.global } })
  await flushPromises()
  return { wrapper, router, pinia, queryClient }
}
export { flushPromises }

/** vee-validate validates/submits over several microtask + timer hops; flush until the DOM settles. */
export async function settle(n = 6) { for (let i = 0; i < n; i++) { await flushPromises(); await new Promise((r) => setTimeout(r, 0)) } }
