import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/services/api'
import { TOKEN_KEY, USER_KEY } from '@/services/http'
import type { TokenResponse, User } from '@/types/api'

function loadUser(): User | null {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
}

/** Client-side session only. All learner data is server state and lives in TanStack Query, not here. */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<User | null>(loadUser())
  const isAuthenticated = computed(() => !!token.value)

  function setSession(r: TokenResponse) {
    token.value = r.access_token
    user.value = r.user
    localStorage.setItem(TOKEN_KEY, r.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(r.user))
  }
  function clear() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    localStorage.removeItem('learnai-last-session') // a different learner on this browser must not inherit the previous one's "continue" pointer
  }
  async function login(b: { email: string; password: string }) { setSession(await authApi.login(b)) }
  async function register(b: { name: string; email: string; password: string }) { setSession(await authApi.register(b)) }
  return { token, user, isAuthenticated, login, register, logout: clear }
})
