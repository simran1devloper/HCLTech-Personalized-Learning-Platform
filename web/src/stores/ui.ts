import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { TutorLevel, TutorMode } from '@/types/api'

export type ThemeMode = 'light' | 'dark'
export const THEME_KEY = 'learnai-theme'

/** Light is the default. Dark is used only when the user chose it explicitly (the system setting is ignored).
 *  Any other stored value, including the legacy "auto", resolves to light. */
export function resolveTheme(stored: string | null | undefined): ThemeMode {
  return stored === 'dark' ? 'dark' : 'light'
}
export function applyTheme(mode: ThemeMode) {
  document.documentElement.classList.toggle('dark', mode === 'dark')
}

export const useUiStore = defineStore('ui', () => {
  const theme = ref<ThemeMode>(resolveTheme(localStorage.getItem(THEME_KEY)))
  if (localStorage.getItem(THEME_KEY) !== theme.value) localStorage.setItem(THEME_KEY, theme.value) // migrate old values
  const sidebarCollapsed = ref(localStorage.getItem('learnai-sidebar') === '1')
  const mobileNavOpen = ref(false)
  const paletteOpen = ref(false)
  const tutorLanguage = ref<'en'>('en')

  const tutorLevel = ref<TutorLevel>((localStorage.getItem('learnai-tutor-level') as TutorLevel) || 'auto')
  const tutorMode = ref<TutorMode>((localStorage.getItem('learnai-tutor-mode') as TutorMode) || 'auto')
  watch(tutorLevel, (v) => localStorage.setItem('learnai-tutor-level', v))
  watch(tutorMode, (v) => localStorage.setItem('learnai-tutor-mode', v))

  watch(theme, (m) => { localStorage.setItem(THEME_KEY, m); applyTheme(m) })
  watch(sidebarCollapsed, (v) => localStorage.setItem('learnai-sidebar', v ? '1' : '0'))
  const setTheme = (m: ThemeMode) => { theme.value = m }
  const toggleTheme = () => { theme.value = theme.value === 'dark' ? 'light' : 'dark' }
  return { theme, sidebarCollapsed, mobileNavOpen, paletteOpen, tutorLanguage, tutorLevel, tutorMode, setTheme, toggleTheme }
})
