import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta { public?: boolean; title?: string; layout?: 'auth' | 'app' }
}

// Route-level code splitting: each page is its own chunk.
export const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/pages/LoginPage.vue'), meta: { public: true, layout: 'auth', title: 'Sign in' } },
  { path: '/register', name: 'register', component: () => import('@/pages/RegisterPage.vue'), meta: { public: true, layout: 'auth', title: 'Create account' } },
  { path: '/', name: 'dashboard', component: () => import('@/pages/DashboardPage.vue'), meta: { title: 'Dashboard' } },
  { path: '/skills', name: 'skills', component: () => import('@/pages/SkillsPage.vue'), meta: { title: 'Skill Explorer' } },
  { path: '/roadmap', name: 'roadmap', component: () => import('@/pages/RoadmapPage.vue'), meta: { title: 'Roadmap' } },
  { path: '/assessments', name: 'assessments', component: () => import('@/pages/AssessmentsPage.vue'), meta: { title: 'Assessments' } },
  { path: '/assessments/:id', name: 'assessment-run', component: () => import('@/pages/AssessmentRunPage.vue'), meta: { title: 'Assessment' } },
  { path: '/study', name: 'study', component: () => import('@/pages/StudyPage.vue'), meta: { title: 'Diagnostic Study' } },
  { path: '/study/learn/:id?', name: 'study-learn', component: () => import('@/pages/StudyWorkspacePage.vue'), meta: { title: 'Study workspace' } },
  { path: '/tutor', name: 'tutor', component: () => import('@/pages/TutorPage.vue'), meta: { title: 'AI Tutor' } },
  { path: '/history', name: 'history', component: () => import('@/pages/HistoryPage.vue'), meta: { title: 'Content History' } },
  { path: '/history/:id', name: 'history-session', component: () => import('@/pages/HistorySessionPage.vue'), meta: { title: 'Saved content' } },
  { path: '/analytics', name: 'analytics', component: () => import('@/pages/AnalyticsPage.vue'), meta: { title: 'Analytics' } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/pages/NotFoundPage.vue'), meta: { title: 'Not found' } },
]

export function createAppRouter(history = createWebHistory()) {
  const router = createRouter({ history, routes, scrollBehavior: () => ({ top: 0 }) })
  router.beforeEach((to) => {
    const auth = useAuthStore()
    if (!to.meta.public && !auth.isAuthenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.public && auth.isAuthenticated) return { name: 'dashboard' }
  })
  router.afterEach((to) => { document.title = `${to.meta.title ?? 'LearnAI'} · LearnAI` })
  return router
}
