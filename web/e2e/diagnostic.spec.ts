import { expect, test, type Page } from '@playwright/test'
import { register } from './helpers'

const STATS = 'http://127.0.0.1:11999/__stats'
const generations = async (page: Page) => (await (await page.request.get(STATS)).json()).questionGenerations as number
const generate = (page: Page) => page.getByRole('button', { name: 'Generate quiz' })
const answerAll = async (page: Page, n: number) => { for (let i = 0; i < n; i++) await page.locator('fieldset').nth(i).locator('input').first().check() }

test.describe('Diagnostic Study: database first, AI only as a fallback', () => {
  test('an existing topic (Python) starts from the question bank: bank-only messages and NO model call', async ({ page }) => {
    await register(page); await page.goto('/study')
    const before = await generations(page)
    await page.getByLabel('Topic').fill('  python   programming '); await generate(page).click()
    await expect(page.getByText('Question 1 of 10')).toBeVisible()
    await expect(page.getByText('Question bank', { exact: true })).toBeVisible()
    await expect(page.locator('fieldset')).toHaveCount(10)
    expect(await generations(page)).toBe(before)                                          // zero model calls
    expect(await page.locator('body').innerText()).not.toMatch(/Generating and validating|Topic not found/)
    const html = await page.content(); expect(html).not.toMatch(/correct_options|correct_answer|"correct"/)   // answers never reach the page before submission
    await expect(page).toHaveURL(/\/study\?session=[0-9a-f-]+/)
  })

  test('a new topic (Rust Programming) is generated, validated and saved; the next learner gets it with no further model call', async ({ page, browser }) => {
    await register(page); await page.goto('/study')
    const before = await generations(page)
    await page.getByLabel('Topic').fill('Rust Programming'); await generate(page).click()
    const prog = page.getByRole('status', { name: 'Quiz preparation progress' })
    await expect(prog).toContainText('Topic not found in question bank.'); await expect(prog).toContainText('Generating and validating questions…')
    await expect(page.getByText('Fake question 1 about Rust Programming')).toBeVisible(); await expect(page.getByText('Newly generated')).toBeVisible()
    const after = await generations(page)
    expect(after - before).toBeGreaterThanOrEqual(1); expect(after - before).toBeLessThanOrEqual(2)   // adaptive batches: one question to time the model, then the rest

    const other = await browser.newContext(); const p2 = await other.newPage(); await register(p2); await p2.goto('/study')
    await p2.getByLabel('Topic').fill('rust'); await p2.getByRole('button', { name: 'Generate quiz' }).click()          // alias of the saved topic
    await expect(p2.getByText('Fake question 1 about Rust Programming')).toBeVisible(); await expect(p2.getByText('Question bank', { exact: true })).toBeVisible()
    expect(await generations(p2)).toBe(after)                                              // reused: the second learner caused NO further model call
    await other.close()
  })

  test('answers autosave; a refresh resumes the same quiz; submit grades on the server; Previous sessions can resume and review', async ({ page }) => {
    await register(page); await page.goto('/study')
    await page.getByLabel('Topic').fill('Python'); await generate(page).click(); await expect(page.locator('fieldset')).toHaveCount(10)
    const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && /\/answers$/.test(r.url()) && r.ok())
    await page.locator('fieldset').nth(0).locator('input').nth(1).check(); await saved
    const sessionUrl = page.url()
    await page.reload()                                                                    // refresh: same session, not a new one
    await expect(page).toHaveURL(sessionUrl); await expect(page.locator('fieldset')).toHaveCount(10)
    await expect(page.locator('fieldset').nth(0).locator('input').nth(1)).toBeChecked()
    await expect(page.getByRole('button', { name: /Submit quiz \(1\/10\)/ })).toBeVisible()

    await page.getByRole('button', { name: 'Leave quiz' }).click()                         // leave, then resume from Previous sessions
    const row = page.getByRole('list', { name: 'Previous diagnostic sessions' }).getByRole('listitem').filter({ hasText: 'Python' })
    await expect(row).toContainText('In progress'); await expect(row).toContainText('1/10 answered'); await row.getByRole('button', { name: /Resume/ }).click()
    await expect(page.locator('fieldset').nth(0).locator('input').nth(1)).toBeChecked()
    await answerAll(page, 10)
    await page.getByRole('button', { name: /Submit quiz/ }).click()
    await expect(page.getByText('Result (graded by the server)')).toBeVisible(); await expect(page.getByText(/\d+ \/ 10/)).toBeVisible()
    await expect(page.getByText(/Correct|Incorrect/).first()).toBeVisible()

    await page.goto('/study'); const done = page.getByRole('list', { name: 'Previous diagnostic sessions' }).getByRole('listitem').filter({ hasText: 'Python' })
    await expect(done).toContainText('Completed'); await done.getByRole('button', { name: /Review/ }).click()
    await expect(page.getByText('Result (graded by the server)')).toBeVisible()
  })

  test('the Assessments page and its history are unaffected by diagnostic sessions', async ({ page }) => {
    await register(page); await page.goto('/study')
    await page.getByLabel('Topic').fill('Python'); await generate(page).click(); await expect(page.locator('fieldset')).toHaveCount(10)
    await page.goto('/assessments'); await expect(page.getByText('No assessments yet')).toBeVisible()
  })

  test('input validation: empty or one-character topics cannot be submitted', async ({ page }) => {
    await register(page); await page.goto('/study')
    await expect(generate(page)).toBeDisabled(); await page.getByLabel('Topic').fill('x'); await expect(generate(page)).toBeDisabled(); await expect(page.getByText('at least two characters')).toBeVisible()
    await page.getByLabel('Topic').fill('Go'); await expect(generate(page)).toBeEnabled()
  })
})
