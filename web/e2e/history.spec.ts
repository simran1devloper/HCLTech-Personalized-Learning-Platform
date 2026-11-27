import { expect, test, type Page } from '@playwright/test'
import { expectNoA11yViolations, register } from './helpers'

/** Generates a study guide through the real diagnostic flow (real backend, fake model server). */
async function generateGuide(page: Page, topic: string) {
  await page.goto('/study')
  await page.getByLabel('Topic').fill(topic); await page.getByRole('button', { name: 'Generate quiz' }).click()
  await expect(page.getByText('Fake question 1 about')).toBeVisible()
  for (let i = 0; i < 10; i++) await page.locator('fieldset').nth(i).getByRole('radio', { name: 'Beta' }).check()
  await page.getByRole('button', { name: /Submit quiz/ }).click()
  await page.getByRole('button', { name: 'Generate study material' }).click()
  await expect(page).toHaveURL(/\/study\/learn\/[0-9a-f-]+/)
  await expect(page.getByRole('region', { name: 'Study material pipeline' }).getByText('Complete', { exact: true })).toBeVisible({ timeout: 40_000 })
}

test.describe('content history (persistent sessions)', () => {
  test('generated content is saved, survives reload / sign-out / sign-in, and reopens without regenerating', async ({ page }) => {
    const email = await register(page)
    await generateGuide(page, 'Python history decorators')

    await page.getByRole('link', { name: 'Content History' }).first().click()
    await expect(page).toHaveURL(/\/history$/)
    await expect(page.getByRole('heading', { name: 'My Learning History' })).toBeVisible()
    const card = page.getByRole('list', { name: 'Saved content' }).getByRole('listitem').filter({ hasText: 'Python history decorators' })
    await expect(card).toBeVisible(); await expect(card).toContainText('10/10 sections completed'); await expect(card).toContainText('Completed')
    await expectNoA11yViolations(page, 'content history list')

    await page.reload()                                                                  // browser refresh
    await expect(card).toBeVisible()

    await page.getByRole('button', { name: 'Account menu' }).click(); await page.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/login/)                                              // logout, then back in
    await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill('password-123'); await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible()          // signed in (new token) before navigating
    await page.goto('/history'); await expect(card).toBeVisible()

    let streams = 0
    page.on('request', (r) => { if (/generate-content|content-sessions\/.+\/(generate|regenerate|continue)|generate-quiz/.test(r.url())) streams++ })
    await card.getByRole('button', { name: 'Open' }).first().click()
    await expect(page).toHaveURL(/\/history\/[0-9a-f-]+$/)
    await expect(page.getByRole('heading', { name: 'Python history decorators' })).toBeVisible()
    await expect(page.getByLabel('Section status').getByText('Ready').first()).toBeVisible()
    await expect(page.getByText('Version 1')).toBeVisible()                              // persisted content rendered immediately
    await expectNoA11yViolations(page, 'saved session')
    await page.getByRole('tab', { name: 'Flashcards' }).click(); await expect(page.getByRole('group', { name: 'Flashcards' })).toBeVisible()
    expect(streams).toBe(0)                                                              // reopening made no generation request
  })

  test('search, rename, archive/restore and delete (with confirmation) work against the real API', async ({ page }) => {
    await register(page)
    await generateGuide(page, 'Rust ownership history')
    await page.goto('/history')
    await page.getByLabel('Search history').fill('ownership')
    await expect(page.getByRole('list', { name: 'Saved content' }).getByRole('listitem')).toHaveCount(1)
    await page.getByLabel('Search history').fill('zzzz-nothing'); await expect(page.getByText('Nothing matches')).toBeVisible()
    await page.getByLabel('Search history').fill('')

    await page.getByRole('button', { name: /Actions for Rust ownership history/ }).click(); await page.getByRole('menuitem', { name: 'Rename' }).click()
    await page.getByLabel('Title').fill('Rust notes'); await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('heading', { name: 'Rust notes' })).toBeVisible()

    await page.getByRole('button', { name: /Actions for Rust notes/ }).click(); await page.getByRole('menuitem', { name: 'Archive' }).click()
    await expect(page.getByText('No saved content yet').or(page.getByText('Nothing matches'))).toBeVisible()
    await page.getByLabel('Filter by status').selectOption('archived'); await expect(page.getByRole('heading', { name: 'Rust notes' })).toBeVisible()
    await page.getByRole('button', { name: /Actions for Rust notes/ }).click(); await page.getByRole('menuitem', { name: 'Restore' }).click()
    await page.getByLabel('Filter by status').selectOption('all'); await expect(page.getByRole('heading', { name: 'Rust notes' })).toBeVisible()

    await page.getByRole('button', { name: /Actions for Rust notes/ }).click(); await page.getByRole('menuitem', { name: 'Delete' }).click()
    await expect(page.getByText('This cannot be undone.')).toBeVisible()                 // confirmation first
    await page.getByRole('button', { name: 'Cancel' }).click(); await expect(page.getByRole('heading', { name: 'Rust notes' })).toBeVisible()
    await page.getByRole('button', { name: /Actions for Rust notes/ }).click(); await page.getByRole('menuitem', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Delete permanently' }).click()
    await expect(page.getByText('No saved content yet')).toBeVisible()
  })

  test("another learner cannot open someone else's saved session", async ({ page, browser }) => {
    await register(page); await generateGuide(page, 'Private topic history')
    await page.goto('/history'); await page.getByRole('list', { name: 'Saved content' }).getByRole('button', { name: 'Open' }).first().click()
    await expect(page).toHaveURL(/\/history\/[0-9a-f-]+$/)
    const url = page.url()
    const other = await browser.newContext(); const p2 = await other.newPage()
    await register(p2); await p2.goto(url)
    await expect(p2.getByText('This content is no longer available')).toBeVisible()
    await expect(p2.getByText('Private topic history')).toHaveCount(0)
    await other.close()
  })

  test('mobile: history is usable without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 700 })
    await register(page); await page.goto('/history')
    await expect(page.getByRole('heading', { name: 'My Learning History' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
})
