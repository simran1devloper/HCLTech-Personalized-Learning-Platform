import { expect, test, type Page } from '@playwright/test'
import { expectNoA11yViolations, register } from './helpers'

const startBtn = (page: Page) => page.getByRole('button', { name: 'Start', exact: true })

test.describe('assessment: Start button → quiz → results → skill gaps → personalized plan', () => {
  test('Start creates a persisted attempt with the chosen skill and question count and opens the quiz with its questions', async ({ page }) => {
    await register(page); await page.goto('/assessments')
    await expect(page.getByText(/Ready: \d+ validated questions/)).toBeVisible()          // availability indicator from the bank
    await expect(page.getByText('Not assessed yet')).toBeVisible()
    const sent = page.waitForRequest((r) => r.method() === 'POST' && /\/api\/v1\/assessments$/.test(r.url()))
    await startBtn(page).click()
    const req = await sent
    expect(req.postDataJSON()).toEqual({ skill: 'Python', n_questions: 10 })
    expect(req.headers()['x-idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await expect(page.getByRole('heading', { name: 'Python' })).toBeVisible()
    await expect(page.getByText('0 of 10 answered')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Question navigation' }).getByRole('button')).toHaveCount(10)
    await expect(page.locator('fieldset input').first()).toBeVisible()                              // questions render immediately
    await expectNoA11yViolations(page, 'quiz')
  })

  test('the skill search, recommendations and question count feed the request; double-click starts exactly one assessment', async ({ page }) => {
    await register(page); await page.goto('/assessments')
    await expect(page.getByText('Recommended', { exact: true })).toBeVisible()                // recommended skills are offered as one-click chips
    await page.getByPlaceholder('Search skills…').fill('postgres')
    const skillSelect = page.getByRole('combobox', { name: /^Skill/ })
    await expect(skillSelect.locator('option')).toHaveCount(1)                                // the search narrows the list
    await page.getByLabel('Questions').selectOption('5')
    const posts: string[] = []
    page.on('request', (r) => { if (r.method() === 'POST' && /\/api\/v1\/assessments$/.test(r.url())) posts.push(r.postData() ?? '') })
    await startBtn(page).dblclick()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await expect(page.getByRole('heading', { name: 'PostgreSQL' })).toBeVisible(); await expect(page.getByText('0 of 5 answered')).toBeVisible()
    expect(posts.length).toBe(1); expect(JSON.parse(posts[0])).toEqual({ skill: 'PostgreSQL', n_questions: 5 })
    await page.goto('/assessments'); await expect(page.getByRole('row', { name: /PostgreSQL/ })).toHaveCount(1)   // one attempt, not two
  })

  test('a failing Start is never silent: an inline message appears, the page stays usable, and Try again works', async ({ page }) => {
    await register(page); await page.goto('/assessments')
    let calls = 0
    await page.route('**/api/v1/assessments', async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      calls++
      if (calls === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'questions_unavailable', message: 'There are no assessment questions for Python yet, and they could not be prepared right now. Please try again in a moment or pick another skill.', details: {} } }) })
      return route.continue()
    })
    await startBtn(page).click()
    const alert = page.getByRole('alert').filter({ hasText: 'could not be prepared right now' })
    await expect(alert).toBeVisible(); await expect(page).toHaveURL(/\/assessments$/)
    await alert.getByRole('button', { name: 'Try again' }).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await page.goto('/assessments')                                                          // network failure message
    await page.route('**/api/v1/assessments', (r) => (r.request().method() === 'POST' ? r.abort('connectionfailed') : r.continue()))
    await startBtn(page).click(); await expect(page.getByRole('alert').filter({ hasText: 'Could not reach the server' })).toBeVisible()
  })

  test('answers autosave to the server and survive a refresh even when this browser has no local copy', async ({ page }) => {
    await register(page); await page.goto('/assessments'); await page.getByLabel('Questions').selectOption('5'); await startBtn(page).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    const saved = () => page.waitForResponse((r) => r.request().method() === 'PUT' && /\/answers$/.test(r.url()) && r.ok())   // wait for the server to confirm each save
    let wait = saved(); await page.locator('fieldset input').nth(2).check(); await wait
    await page.getByRole('button', { name: 'Next' }).click()
    wait = saved(); await page.locator('fieldset input').nth(1).check(); await wait; await expect(page.getByText(/Answers saved/)).toBeVisible()
    await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('learnai-draft:')).forEach((k) => localStorage.removeItem(k)))   // like opening it on another device
    await page.reload()
    await expect(page.getByText('2 of 5 answered')).toBeVisible()
    await expect(page.locator('fieldset input').nth(2)).toBeChecked()                              // question 1 (shown first after a reload) restored from the server
    await page.getByRole('navigation', { name: 'Question navigation' }).getByRole('button').nth(1).click(); await expect(page.locator('fieldset input').nth(1)).toBeChecked()   // and question 2
  })

  test('submit asks for confirmation, then shows score, concept chart, skill gaps and the plan CTA; the CTA creates a linked plan and generates it', async ({ page }) => {
    await register(page); await page.goto('/assessments'); await startBtn(page).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    const attemptUrl = page.url()
    await page.getByRole('button', { name: 'Submit now' }).click()
    const dlg = page.getByRole('dialog'); await expect(dlg).toContainText('10 question(s) are unanswered')
    await dlg.getByRole('button', { name: 'Keep working' }).click(); await expect(page.getByText('Score (computed by the backend)')).toHaveCount(0)   // nothing submitted yet
    await page.getByRole('button', { name: 'Submit now' }).click(); await page.getByRole('dialog').getByRole('button', { name: 'Submit anyway' }).click()

    await expect(page.getByText('Score (computed by the backend)')).toBeVisible()
    await expect(page.getByText('0%').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Concept performance' })).toBeVisible()
    await expect(page.locator('figure[aria-label^="Accuracy by concept"] canvas')).toBeVisible()
    await expect(page.getByRole('table', { name: 'Accuracy by concept' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Skill-gap analysis' })).toBeVisible()
    await expect(page.getByText('Critical gaps')).toBeVisible(); await expect(page.getByText('Recommended learning order')).toBeVisible()
    await expect(page.getByText(/short assessment samples only part of a skill|short assessment/).first()).toBeVisible()
    await expect(page.getByRole('region', { name: 'Review answers' }).locator('li').first()).toContainText('Not answered')
    await expectNoA11yViolations(page, 'assessment results')

    await page.getByRole('button', { name: 'Generate My Personalized Learning Plan' }).click()
    await expect(page).toHaveURL(/\/history\/[0-9a-f-]+/)
    await expect(page.getByText(/Built from your Python assessment/)).toBeVisible()
    await expect(page.getByRole('link', { name: 'View assessment results' })).toBeVisible()
    await expect(page.getByLabel('Section status').getByText('Ready').first()).toBeVisible({ timeout: 30_000 })   // sections stream in and are persisted
    await expect(page.getByRole('tab', { name: 'Your learning plan' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Practice and knowledge check' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Key concepts' })).toHaveCount(0)             // not the generic course tabs
    await expect(page.getByText('10/10 sections completed')).toHaveCount(0)

    await page.goto(attemptUrl)                                                              // the attempt now points at its plan; CTA becomes "Open"
    await expect(page.getByRole('button', { name: 'Open my learning plan' })).toBeVisible(); await expect(page.getByRole('button', { name: 'Generate My Personalized Learning Plan' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Retake assessment' }).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/); await expect(page).not.toHaveURL(attemptUrl); await expect(page.getByText('0 of 10 answered')).toBeVisible()
  })

  test('history shows Resume for an open attempt and Review for a submitted one', async ({ page }) => {
    await register(page); await page.goto('/assessments'); await page.getByLabel('Questions').selectOption('3'); await startBtn(page).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await page.goto('/assessments')
    const row = page.getByRole('row', { name: /Python/ }); await expect(row).toContainText('In progress'); await row.getByRole('link', { name: /Resume/ }).click()
    await expect(page.getByText('0 of 3 answered')).toBeVisible()
    await page.getByRole('button', { name: 'Submit now' }).click(); await page.getByRole('dialog').getByRole('button', { name: 'Submit anyway' }).click()
    await expect(page.getByText('Score (computed by the backend)')).toBeVisible()
    await page.goto('/assessments'); const done = page.getByRole('row', { name: /Python.*Completed/ }); await expect(done).toBeVisible()
    await done.getByRole('link', { name: /Review/ }).click(); await expect(page.getByRole('heading', { name: 'Review answers' })).toBeVisible()
  })

  test("another learner cannot open someone else's attempt", async ({ page, browser }) => {
    await register(page); await page.goto('/assessments'); await startBtn(page).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/); const url = page.url()
    const other = await browser.newContext(); const p2 = await other.newPage(); await register(p2); await p2.goto(url)
    await expect(p2.getByRole('alert')).toContainText(/not found/i); await other.close()
  })
})
