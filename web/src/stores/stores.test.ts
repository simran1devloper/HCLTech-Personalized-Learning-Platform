import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from './auth'
import { useAssessmentDraft } from './assessmentDraft'
import { resolveTheme, THEME_KEY, useUiStore } from './ui'
import { nextTick } from 'vue'
import * as api from '@/services/api'
import { TOKEN_KEY, USER_KEY } from '@/services/http'

beforeEach(() => setActivePinia(createPinia()))

describe('auth store', () => {
  const resp = { access_token: 'tok', token_type: 'bearer', user: { id: 'u1', name: 'Asha K', email: 'a@e.com' } }
  it('persists the session under the legacy keys and clears it on logout', async () => {
    vi.spyOn(api.authApi, 'login').mockResolvedValue(resp)
    const s = useAuthStore(); await s.login({ email: 'a@e.com', password: 'x' })
    expect(s.isAuthenticated).toBe(true); expect(localStorage.getItem(TOKEN_KEY)).toBe('tok'); expect(JSON.parse(localStorage.getItem(USER_KEY)!).id).toBe('u1')
    s.logout(); expect(s.isAuthenticated).toBe(false); expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
  })
  it('restores a session created by the legacy frontend', () => {
    localStorage.setItem(TOKEN_KEY, 'old'); localStorage.setItem(USER_KEY, JSON.stringify(resp.user))
    setActivePinia(createPinia()); const s = useAuthStore(); expect(s.isAuthenticated).toBe(true); expect(s.user?.name).toBe('Asha K')
  })
  it('tolerates corrupt stored user JSON', () => { localStorage.setItem(USER_KEY, '{oops'); setActivePinia(createPinia()); expect(useAuthStore().user).toBeNull() })
  it('failed login leaves no session', async () => {
    vi.spyOn(api.authApi, 'login').mockRejectedValue(new Error('Invalid email or password.'))
    const s = useAuthStore(); await expect(s.login({ email: 'a', password: 'b' })).rejects.toThrow(); expect(s.isAuthenticated).toBe(false)
  })
})

describe('assessment draft autosave', () => {
  it('saves, restores per user+assessment, and never crosses accounts', () => {
    const d = useAssessmentDraft(); d.load('u1', 'a1'); d.setAnswer('q1', 2); d.setAnswer('q2', [0, 1]); d.goto(1)
    setActivePinia(createPinia()); const d2 = useAssessmentDraft(); d2.load('u1', 'a1')
    expect(d2.answers).toEqual({ q1: 2, q2: [0, 1] }); expect(d2.current).toBe(1)
    d2.load('u2', 'a1'); expect(d2.answers).toEqual({}); d2.load('u1', 'a2'); expect(d2.answers).toEqual({})
  })
  it('empty answers are removed; clear() wipes storage', () => {
    const d = useAssessmentDraft(); d.load('u', 'a'); d.setAnswer('q', 'text'); d.setAnswer('q', ''); expect(d.answers).toEqual({})
    d.setAnswer('q', 1); d.clear(); expect(localStorage.getItem('learnai-draft:u:a')).toBeNull()
  })
})

describe('theme: light by default, dark only when chosen', () => {
  const dark = () => document.documentElement.classList.contains('dark')
  it('resolveTheme: only an explicit "dark" gives dark (legacy "auto" and junk become light)', () => {
    expect(resolveTheme('dark')).toBe('dark')
    for (const v of ['light', 'auto', '', 'system', 'DARK', null, undefined]) expect(resolveTheme(v as any)).toBe('light')
  })
  it('first visit is light even when the operating system prefers dark', () => {
    ;(globalThis as any).matchMedia = (q: string) => ({ matches: q.includes('dark'), media: q, addEventListener() {}, removeEventListener() {} })
    document.documentElement.classList.remove('dark'); localStorage.clear()
    expect(useUiStore().theme).toBe('light'); expect(dark()).toBe(false)
  })
  it('migrates the old "auto" setting to light', () => {
    localStorage.setItem(THEME_KEY, 'auto'); const ui = useUiStore()
    expect(ui.theme).toBe('light'); expect(localStorage.getItem(THEME_KEY)).toBe('light')
  })
  it('manual choice is applied, persisted and restored', async () => {
    const ui = useUiStore(); ui.setTheme('dark'); await nextTick()
    expect(dark()).toBe(true); expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    setActivePinia(createPinia()); expect(useUiStore().theme).toBe('dark')       // restored on next visit
    useUiStore().toggleTheme(); await nextTick(); expect(dark()).toBe(false); expect(localStorage.getItem(THEME_KEY)).toBe('light')
  })
})
