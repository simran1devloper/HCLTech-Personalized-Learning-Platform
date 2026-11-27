import { expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

export const uniqueEmail = () => `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`
export async function register(page: Page, name = 'E2E Learner') {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Full name').fill(name); await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill('password-123')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: /Welcome, E2E/ })).toBeVisible()
  return email
}
/** Fails on serious/critical WCAG A/AA violations. */
export async function expectNoA11yViolations(page: Page, label: string) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  const bad = r.violations.filter((v) => ['serious', 'critical'].includes(v.impact ?? ''))
  expect(bad.map((v) => `${label}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' | ')}`), `axe violations on ${label}`).toEqual([])
}
