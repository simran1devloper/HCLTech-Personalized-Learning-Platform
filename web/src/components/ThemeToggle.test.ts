import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ThemeToggle from './ThemeToggle.vue'
import { useUiStore } from '@/stores/ui'

describe('ThemeToggle', () => {
  it('reflects the current theme in its accessible name and opens a Light/Dark menu', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const w = mount(ThemeToggle, { global: { plugins: [pinia] }, attachTo: document.body })
    const btn = w.get('[data-testid=theme-toggle]')
    expect(btn.attributes('aria-label')).toBe('Theme: light. Open theme menu')
    await btn.trigger('pointerdown', { button: 0, ctrlKey: false }); await btn.trigger('click')
    await new Promise((r) => setTimeout(r, 20))
    const items = [...document.querySelectorAll('[role=menuitemradio]')].map((e) => e.textContent!.trim())
    expect(items).toEqual(['Light', 'Dark'])
    ;(document.querySelectorAll('[role=menuitemradio]')[1] as HTMLElement).click()
    await new Promise((r) => setTimeout(r, 20))
    expect(useUiStore().theme).toBe('dark')
    w.unmount()
  })
})
