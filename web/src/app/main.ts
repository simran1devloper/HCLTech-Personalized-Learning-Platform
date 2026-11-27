import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { createAppRouter } from './router'
import { queryClient } from './queryClient'
import { useAuthStore } from '@/stores/auth'
import { setUnauthorizedHandler } from '@/services/http'
import '@/styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(VueQueryPlugin, { queryClient })
const router = createAppRouter()
app.use(router)

// An expired/invalid token anywhere sends the user back to sign-in and drops cached server data.
setUnauthorizedHandler(() => {
  useAuthStore().logout()
  queryClient.clear()
  if (router.currentRoute.value.name !== 'login') router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
})
app.mount('#app')
