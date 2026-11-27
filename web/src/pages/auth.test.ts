import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import LoginPage from './LoginPage.vue'
import RegisterPage from './RegisterPage.vue'
import { createAppRouter } from '@/app/router'
import { mountApp, settle as flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/services/http'

const user = { id: 'u1', name: 'Asha K', email: 'a@e.com' }

describe('Login', () => {
  it('shows field errors and does not call the API when invalid', async () => {
    const spy = vi.spyOn(api.authApi, 'login')
    const { wrapper } = await mountApp(LoginPage, { route: '/login' })
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('Email is required'); expect(spy).not.toHaveBeenCalled()
    await wrapper.find('input[type=email]').setValue('not-an-email'); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('valid email')
    expect(wrapper.find('input[type=email]').attributes('aria-invalid')).toBe('true')
  })
  it('signs in, stores the session and follows a safe redirect', async () => {
    vi.spyOn(api.authApi, 'login').mockResolvedValue({ access_token: 't', token_type: 'bearer', user })
    const { wrapper, router } = await mountApp(LoginPage, { route: '/login?redirect=/roadmap' })
    await wrapper.find('input[type=email]').setValue('a@e.com'); await wrapper.find('input[type=password]').setValue('secret-123')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(useAuthStore().isAuthenticated).toBe(true); await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/roadmap'), { timeout: 4000 })
  })
  it('ignores an off-site redirect (open-redirect protection)', async () => {
    vi.spyOn(api.authApi, 'login').mockResolvedValue({ access_token: 't', token_type: 'bearer', user })
    const { wrapper, router } = await mountApp(LoginPage, { route: '/login?redirect=https://evil.example/' })
    await wrapper.find('input[type=email]').setValue('a@e.com'); await wrapper.find('input[type=password]').setValue('x')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/'), { timeout: 4000 })
  })
  it('displays the backend error message on failure', async () => {
    vi.spyOn(api.authApi, 'login').mockRejectedValue(new ApiError('Invalid email or password.', 401))
    const { wrapper } = await mountApp(LoginPage, { route: '/login' })
    await wrapper.find('input[type=email]').setValue('a@e.com'); await wrapper.find('input[type=password]').setValue('x')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.find('[role=alert]').text()).toBe('Invalid email or password.'); expect(useAuthStore().isAuthenticated).toBe(false)
  })
})

describe('Register', () => {
  it('validates password length like the backend', async () => {
    const { wrapper } = await mountApp(RegisterPage, { route: '/register' })
    const inputs = wrapper.findAll('input'); await inputs[0].setValue('Asha K'); await inputs[1].setValue('a@e.com'); await inputs[2].setValue('short')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('at least 8 characters')
  })
})

describe('router guard', () => {
  it('redirects anonymous users to login (keeping the target) and signed-in users away from login', async () => {
    setActivePinia(createPinia()); const router = createAppRouter(createMemoryHistory())
    await router.push('/roadmap'); expect(router.currentRoute.value.name).toBe('login'); expect(router.currentRoute.value.query.redirect).toBe('/roadmap')
    useAuthStore().$patch({ token: 't' }); await router.push('/login'); expect(router.currentRoute.value.name).toBe('dashboard')
    await router.push('/does/not/exist'); expect(router.currentRoute.value.name).toBe('not-found')
  })
})
