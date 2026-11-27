import { expect, test } from '@playwright/test'
import { expectNoA11yViolations, register, uniqueEmail } from './helpers'

test.describe('authentication & navigation', () => {
  test('anonymous users are redirected to sign in and returned after login', async ({ page, request }) => {
    const email = uniqueEmail()
    await request.post('http://127.0.0.1:8100/auth/register', { data: { name: 'Redirect User', email, password: 'password-123' } })
    await page.goto('/roadmap'); await expect(page).toHaveURL(/\/login\?redirect=(%2F|\/)roadmap/)
    await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill('password-123'); await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/roadmap$/); await expect(page.getByRole('heading', { name: 'Learning roadmap' })).toBeVisible()
  })
  test('wrong password shows the backend error; validation messages show for empty fields', async ({ page }) => {
    await page.goto('/login'); await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByText('Email is required')).toBeVisible()
    await page.getByLabel('Email').fill('nobody@example.com'); await page.getByLabel('Password').fill('wrong-password'); await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('Invalid email or password.')
  })
  test('register, sign out, sign back in', async ({ page }) => {
    const email = await register(page)
    await page.getByRole('button', { name: 'Account menu' }).click(); await page.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/login/)
    await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill('password-123'); await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible()
  })
  test('Ctrl+K command palette navigates by keyboard and searches real skills', async ({ page }) => {
    await register(page)
    await page.keyboard.press('Control+k'); const box = page.getByRole('combobox', { name: 'Search commands' }); await expect(box).toBeFocused()
    await box.fill('road'); await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/roadmap/)
    await page.keyboard.press('Control+k'); await page.getByRole('combobox', { name: 'Search commands' }).fill('kubern')
    await expect(page.getByRole('option', { name: 'Kubernetes' })).toBeVisible(); await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/skills\?skill=/); await expect(page.getByRole('heading', { name: 'Kubernetes' })).toBeVisible()
    await page.keyboard.press('Control+k'); await page.keyboard.press('Escape'); await expect(page.getByRole('combobox', { name: 'Search commands' })).toBeHidden()
  })
  test('dashboard for a new learner labels unavailable metrics instead of showing fake numbers', async ({ page }) => {
    await register(page)
    await expect(page.getByText('Not available: set a career goal')).toBeVisible(); await expect(page.getByText('Not available: Study time is not tracked by the backend yet.')).toBeVisible()
    await expect(page.getByText('No assessed skills yet')).toBeVisible()
  })
  test('light is the default (even when the OS prefers dark); dark is manual, persists, and is switchable from the palette', async ({ browser }) => {
    const ctx = await browser.newContext({ colorScheme: 'dark' })                 // OS says dark; app must still start light
    const page = await ctx.newPage()
    await register(page)
    const isDark = () => page.evaluate(() => document.documentElement.classList.contains('dark'))
    expect(await isDark()).toBe(false)
    // the page background is pure white (browsers serialise it as rgb(255, 255, 255) or lab(100 0 0))
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toMatch(/^(rgb\(255, 255, 255\)|lab\(100 0 0\)|oklch\(1 0 0\))$/)
    await page.getByTestId('theme-toggle').click(); await page.getByRole('menuitemradio', { name: 'Dark' }).click()
    await expect.poll(isDark).toBe(true)
    await page.reload(); expect(await isDark()).toBe(true)                         // manual choice persists
    await page.keyboard.press('Control+k'); await page.getByRole('combobox', { name: 'Search commands' }).fill('light theme'); await page.keyboard.press('Enter')
    await expect.poll(isDark).toBe(false)
    await page.getByRole('button', { name: 'Collapse sidebar' }).click(); await expect(page.getByRole('button', { name: 'Expand sidebar' })).toBeVisible()
    await ctx.close()
  })
  test('login page also offers the theme switch and starts light', async ({ page }) => {
    await page.goto('/login'); expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(false)
    await page.getByTestId('theme-toggle').click(); await page.getByRole('menuitemradio', { name: 'Dark' }).click()
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
  })
})

test.describe('learning journey (real backend)', () => {
  test('goal → roadmap → assessment (autosave, reload recovery, submit) → dashboard & analytics reflect it', async ({ page }) => {
    await register(page)
    await page.getByRole('button', { name: 'Set career goal' }).first().click()
    await page.locator('input[name=role][value=ai-engineer]').check(); await page.getByRole('button', { name: 'Save goal' }).click()
    await expect(page.getByText('AI Engineer').first()).toBeVisible()

    await page.goto('/roadmap'); await page.getByRole('button', { name: 'Generate roadmap' }).click()
    await expect(page.getByRole('heading', { name: 'Weeks 1–4 (estimated)' })).toBeVisible()
    await expect(page.getByText('Completion: proficiency ≥').first()).toBeVisible()
    await page.getByRole('button', { name: 'Re-plan' }).click(); await expect(page.getByText(/No changes needed/)).toBeVisible()

    await page.goto('/assessments?skill=Python'); await page.getByLabel('Questions').selectOption('3')
    await page.getByRole('button', { name: 'Start', exact: true }).click(); await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await expect(page.getByRole('heading', { name: 'Python' })).toBeVisible(); await expect(page.getByText('0 of 3 answered')).toBeVisible()
    // answer the first question, then reload: the draft must survive
    await page.getByRole('radio').first().check(); await expect(page.getByText(/Answers saved|Saved on this device/)).toBeVisible()
    await page.reload(); await expect(page.getByText('1 of 3 answered')).toBeVisible(); await expect(page.getByRole('radio').first()).toBeChecked()
    // answer the rest by whatever input type each question uses
    for (let i = 0; i < 3; i++) {
      await page.getByRole('navigation', { name: 'Question navigation' }).getByRole('button').nth(i).click()
      const radios = page.getByRole('radio'), boxes = page.getByRole('checkbox'), ta = page.locator('textarea')
      if (await radios.count()) await radios.first().check(); else if (await boxes.count()) await boxes.first().check()
      else if (await page.locator('.monaco-editor').count()) {   // coding question: real Monaco editor, loaded on demand
        await page.locator('.monaco-editor').first().click(); await page.keyboard.type('def solve(nums): return 0'); await expect(page.getByText(/Answers saved|Saved on this device/)).toBeVisible()
      } else if (await ta.count()) await ta.fill('Use caching, sharding and a queue')
    }
    await page.getByRole('button', { name: /Submit/ }).first().click()
    const dlg = page.getByRole('dialog'); await expect(dlg).toBeVisible()                       // a submission is final: always confirmed
    await dlg.getByRole('button', { name: /Submit anyway|Submit assessment/ }).click()
    await expect(page.getByText('Score (computed by the backend)')).toBeVisible(); await expect(page.getByText(/Current proficiency:/)).toBeVisible()
    await expect(page.getByRole('region', { name: 'Review answers' }).locator('li').first()).toContainText(/Correct answer|Your answer|Not answered/)

    await page.goto('/'); await expect(page.getByText('Skills assessed').locator('xpath=following::p[1]')).toHaveText('1')
    await expect(page.locator('figure[aria-label^="Weekly activity chart"]')).toBeVisible()
    await expect(page.locator('figure[aria-label^="Skill proficiency chart"] canvas')).toBeVisible()
    await page.goto('/analytics'); await expect(page.locator('figure[aria-label^="Skill progression chart"] canvas')).toBeVisible()
    await expect(page.getByText('Not enough history for a trend')).toBeVisible()
    await page.goto('/assessments'); await expect(page.getByRole('row', { name: /Python.*Completed/ })).toBeVisible()
  })

  test('submitting while offline keeps answers and recovers when the network returns', async ({ page }) => {
    await register(page)
    await page.goto('/assessments?skill=SQL'); await page.getByRole('button', { name: 'Start', exact: true }).click()
    await expect(page).toHaveURL(/\/assessments\/[0-9a-f-]+$/)
    await page.getByRole('radio').first().check()
    await page.route('**/api/v1/assessments/*/submit', (r) => r.abort('connectionfailed'))     // network drops
    await page.getByRole('button', { name: 'Submit now' }).or(page.getByRole('button', { name: 'Submit', exact: true })).first().click()
    await page.getByRole('button', { name: 'Submit anyway' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'connection was lost' })).toBeVisible()
    await expect(page.getByText('In progress').first()).toBeVisible()                           // nothing was marked complete
    await page.unroute('**/api/v1/assessments/*/submit')                                         // network returns
    await page.getByRole('button', { name: 'Retry submit' }).click()
    await expect(page.getByText('Score (computed by the backend)')).toBeVisible()
    expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('learnai-draft:')))).toEqual([])  // draft cleared only after success
  })
})

test.describe('diagnostic study (sectioned generation)', () => {
  test('quiz → results → study material streams in section by section; failed sections regenerate on demand', async ({ page }) => {
    await register(page); await page.goto('/study')
    await page.getByLabel('Topic').fill('Python FLAKY decorators'); await page.getByRole('button', { name: 'Generate quiz' }).click()
    // an unknown topic: the progress text reflects the real backend decision (bank miss -> generate -> validate)
    const prog = page.getByRole('status', { name: 'Quiz preparation progress' })
    await expect(prog).toContainText('Topic not found in question bank.'); await expect(prog).toContainText('Generating and validating questions…')
    await expect(page.getByText('Fake question 1 about')).toBeVisible()
    for (let i = 0; i < 10; i++) await page.locator('fieldset').nth(i).getByRole('radio', { name: 'Beta' }).check()    // options are shuffled per session: pick by text
    await page.getByRole('button', { name: /Submit quiz/ }).click()
    await expect(page.getByText('Result (graded by the server)')).toBeVisible(); await expect(page.getByText('10 / 10')).toBeVisible()
    await page.getByRole('button', { name: 'Generate study material' }).click()
    await expect(page).toHaveURL(/\/study\/learn\/[0-9a-f-]+/)                              // the workspace moves to its saved-session URL as soon as generation starts
    const pipe = page.getByRole('region', { name: 'Study material pipeline' })
    await expect(pipe).toBeVisible(); await expect(pipe.getByText('Study Material Generation')).toBeVisible()
    await expect(page.getByText('Summary', { exact: false }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()                  // first section shown while the others are written
    await expect(pipe.getByText('Needs attention', { exact: true })).toBeVisible({ timeout: 30_000 })    // flashcards fail twice for FLAKY topics
    await pipe.getByRole('button', { name: 'Expand details' }).click()
    const flash = pipe.locator('[data-task="flashcards"]')                                       // the monitor shows exactly what failed
    await expect(flash).toHaveAttribute('data-status', 'failed'); await expect(pipe.locator('[data-task="summary"]')).toHaveAttribute('data-status', 'completed')
    await page.getByRole('tab', { name: 'Flashcards' }).click(); await expect(page.getByText('This section could not be generated')).toBeVisible()
    await page.getByRole('button', { name: 'Retry this section' }).click()                       // targeted retry: only flashcards are generated again
    await expect(pipe.getByText('Complete', { exact: true })).toBeVisible({ timeout: 30_000 }); await expect(page.getByText('Card question 1?')).toBeVisible()
    await page.getByRole('tab', { name: 'Deep Dive' }).click(); await expect(page.locator('.shiki').first()).toContainText('deep_dive')
    await expect(page.getByRole('tab', { name: 'Best practices' })).toHaveCount(0)
    await page.getByRole('tab', { name: 'Implementation Guide' }).click(); await expect(page.getByRole('heading', { name: 'practical_guide heading' })).toBeVisible()
    await page.goto('/study'); await expect(page.getByRole('list', { name: 'Previous diagnostic sessions' }).getByRole('button', { name: 'Python FLAKY decorators', exact: true })).toBeVisible()   // saved to history
  })
})

test.describe('diagnostic quiz generation safety', () => {
  test('an unusable generated question is rejected and never shown or saved; generation recovers and the quiz still starts', async ({ page }) => {
    await register(page); await page.goto('/study')
    await page.getByLabel('Topic').fill('BADQUIZ pipelines'); await page.getByRole('button', { name: 'Generate quiz' }).click()
    await expect(page.getByText('Fake question 1 about')).toBeVisible({ timeout: 30_000 })      // the fake model's first output is broken; the next batch is fine
    await expect(page.locator('fieldset')).toHaveCount(10)
    await expect(page.getByText('A broken question')).toHaveCount(0)                           // the invalid question never reached the learner
    await expect(page.getByRole('alert')).toHaveCount(0)
  })
})

test.describe('skill explorer', () => {
  test('interactive graph: nodes render, search finds, selection shows prerequisites, filters narrow, actions route', async ({ page }) => {
    await register(page); await page.goto('/skills')
    await expect(page.locator('.vue-flow__node').first()).toBeVisible()
    const all = await page.locator('.vue-flow__node').count(); expect(all).toBeGreaterThan(10)
    await expect(page.getByText('Choose a career above to see your readiness')).toBeVisible()      // new learner: all-skills view, no invented readiness
    await page.getByLabel('Search skills').fill('fastapi'); await page.getByLabel('Search skills').press('Enter')
    await expect(page.getByRole('heading', { name: 'FastAPI' })).toBeVisible(); await expect(page.getByRole('heading', { name: 'Prerequisites' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'API Development' }).first()).toBeVisible()
    await expect(page.locator('.vue-flow__node [data-role=dimmed]').first()).toBeVisible()             // unrelated skills are dimmed while one is selected
    await page.getByLabel('Search skills').fill(''); await page.getByLabel('Filter by category').selectOption('ai')
    await expect.poll(() => page.locator('.vue-flow__node').count()).toBeLessThan(all)
    await page.getByRole('button', { name: 'List' }).click(); await expect(page.getByRole('list', { name: 'Skills' })).toBeVisible()
    await page.getByLabel('Target career').selectOption('ai-engineer'); await expect(page.getByText('Career readiness')).toBeVisible(); await expect(page.getByText('Recommended next skills')).toBeVisible()
    await page.getByRole('button', { name: 'Graph', exact: true }).click(); await page.getByLabel('Filter by category').selectOption('')
    await page.goto('/skills?skill=fastapi'); await page.getByRole('button', { name: 'Take assessment' }).click(); await expect(page).toHaveURL(/\/assessments\?skill=FastAPI/)
  })
  test('Start learning hands the skill and career to the Diagnostic Study', async ({ page }) => {
    await register(page); await page.goto('/skills?role=ai-engineer&skill=rag')
    await page.getByRole('button', { name: /^(Start|Continue) learning$/ }).click()
    await expect(page).toHaveURL(/\/study\?.*skill=.*topic=RAG.*career=ai-engineer/); await expect(page.getByLabel('Topic')).toHaveValue('RAG')
    await expect(page.getByText('Learning a skill from the Skill Explorer')).toBeVisible()
  })
})

test.describe('AI tutor streaming', () => {
  test('streams, renders markdown/code/diagram/math safely, persists history', async ({ page }) => {
    await register(page); await page.goto('/tutor')
    await page.getByLabel('Message the tutor').fill('Explain async/await'); await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Stop generating' })).toBeVisible()               // streaming in progress
    await expect(page.getByText('async/await', { exact: false }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible({ timeout: 20_000 }) // finished
    await expect(page.locator('pre code, .shiki').first()).toBeVisible()
    await expect(page.locator('.shiki').first()).toContainText('asyncio')                            // shiki highlighted
    await expect(page.locator('figure svg').first()).toBeVisible({ timeout: 15_000 })               // mermaid rendered
    await expect(page.locator('.katex').first()).toBeVisible()                                       // KaTeX rendered
    expect(await page.evaluate(() => (window as any).__xss)).toBeUndefined()                         // generated <script> never ran
    expect(await page.locator('script:not([src])', { hasText: '__xss' }).count()).toBe(0)
    await page.getByRole('button', { name: 'Copy explanation' }).first().click()
    const convs = page.getByRole('complementary', { name: 'Conversations' })
    await page.reload(); await expect(convs.getByRole('button', { name: /Explain async\/await/ })).toBeVisible()
    await convs.getByRole('button', { name: /Explain async\/await/ }).click(); await expect(page.getByText('Copy explanation').first()).toBeVisible()
  })
  test('Stop cancels a stream; a provider failure shows a recoverable error with Retry', async ({ page }) => {
    await register(page); await page.goto('/tutor')
    await page.getByLabel('Message the tutor').fill('SLOWPLEASE explain'); await page.keyboard.press('Enter')
    await page.getByRole('button', { name: 'Stop generating' }).click(); await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible()
    await page.getByRole('button', { name: 'New conversation' }).click()
    await page.getByLabel('Message the tutor').fill('FAILNOW please'); await page.keyboard.press('Enter')
    await expect(page.getByRole('alert')).toContainText(/unavailable|try/i); await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  })
})

test.describe('AI tutor assistant', () => {
  const box = (page: any) => page.getByLabel('Message the tutor')
  const idle = (page: any) => expect(page.getByRole('button', { name: 'Send message' })).toBeVisible({ timeout: 20_000 })
  test('welcome screen, follow-up in the same conversation, rename, search and delete', async ({ page }) => {
    await register(page); await page.goto('/tutor')
    await expect(page.getByRole('heading', { name: 'What would you like to learn today?' })).toBeVisible()
    await box(page).fill('Explain Redis'); await page.keyboard.press('Enter'); await idle(page)
    await box(page).fill('How does it work internally?'); await page.keyboard.press('Enter'); await idle(page)
    await expect(page.getByRole('button', { name: 'Copy explanation' })).toHaveCount(2)
    await page.getByRole('button', { name: 'Good response' }).last().click()
    await page.getByRole('button', { name: 'Regenerate' }).click(); await idle(page)
    await expect(page.getByRole('button', { name: 'Copy explanation' })).toHaveCount(2)
    const convs = page.getByRole('complementary', { name: 'Conversations' })
    await convs.getByRole('button', { name: 'Rename conversation' }).click()
    await page.getByLabel('Conversation title').fill('Caching notes'); await page.getByRole('button', { name: 'Save' }).click()
    await expect(convs.getByRole('button', { name: /Caching notes/ })).toBeVisible()
    await convs.getByLabel('Search conversations').fill('nothing-matches-this'); await expect(convs.getByText('No matching conversations.')).toBeVisible()
    await convs.getByLabel('Search conversations').fill('')
    await convs.getByRole('button', { name: 'Delete conversation' }).click(); await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(convs.getByText('No conversations yet.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'What would you like to learn today?' })).toBeVisible()
  })
  test('a quiz is created only after confirmation and opens in Assessments; a stopped answer is kept', async ({ page }) => {
    await register(page); await page.goto('/tutor')
    await box(page).fill('Generate a Python quiz'); await page.keyboard.press('Enter'); await idle(page)
    await page.getByTestId('tool-card').getByRole('button', { name: 'Confirm' }).click()
    await expect(page.getByRole('link', { name: 'Start quiz' })).toBeVisible()
    await page.getByRole('link', { name: 'Start quiz' }).click(); await expect(page).toHaveURL(/\/assessments\/.+/)
    await page.goto('/tutor'); await box(page).fill('SLOWPLEASE explain'); await page.keyboard.press('Enter')
    await expect(page.locator('.tutor-answer')).toBeVisible()
    await page.getByRole('button', { name: 'Stop generating' }).click()
    await expect(page.getByTestId('reply-note')).toContainText('Stopped')
    await page.reload(); await page.getByRole('complementary', { name: 'Conversations' }).getByRole('button', { name: /SLOWPLEASE/ }).click()
    await expect(page.getByTestId('reply-note')).toContainText('Stopped')
  })
  test('skill gaps come from stored data, and the page is accessible with content and on mobile', async ({ page }) => {
    await register(page); await page.goto('/tutor')
    await page.getByLabel('Career goal').selectOption('ai-engineer')
    await box(page).fill('Analyze my skill gaps'); await page.keyboard.press('Enter'); await idle(page)
    await expect(page.getByTestId('tool-card').getByRole('table')).toContainText('Python')
    await expectNoA11yViolations(page, 'tutor with a tool card')
    await page.setViewportSize({ width: 390, height: 780 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
    await page.getByRole('button', { name: 'History' }).click(); await expect(page.getByRole('button', { name: 'New conversation' })).toBeVisible()
  })
})

test.describe('responsive & accessibility', () => {
  test('mobile layout: drawer navigation, no horizontal overflow, touch-sized targets', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 }); await register(page)
    await expect(page.getByRole('complementary', { name: 'Primary' })).toBeHidden()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.getByRole('button', { name: 'Open menu' }).click(); const nav = page.getByRole('navigation', { name: 'Mobile navigation' }); await expect(nav).toBeVisible()
    const h = (await nav.getByRole('link', { name: 'Roadmap' }).boundingBox())!.height; expect(h).toBeGreaterThanOrEqual(40)
    await nav.getByRole('link', { name: 'Roadmap' }).click(); await expect(page).toHaveURL(/\/roadmap/); await expect(nav).toBeHidden()
    for (const p of ['/skills', '/assessments', '/tutor', '/analytics']) { await page.goto(p); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), p).toBe(true) }
  })
  for (const [name, path] of [['login', '/login'], ['register', '/register']] as const) {
    test(`axe: ${name} has no serious/critical WCAG violations`, async ({ page }) => { await page.goto(path); await expect(page.locator('h1')).toBeVisible(); await expectNoA11yViolations(page, name) })
  }
  test('axe: authenticated pages (light + dark) have no serious/critical WCAG violations', async ({ page }) => {
    await register(page)
    for (const mode of ['light', 'dark']) {
      await page.evaluate((m) => { localStorage.setItem('learnai-theme', m); document.documentElement.classList.toggle('dark', m === 'dark') }, mode)
      for (const [name, path] of [['dashboard', '/'], ['skills', '/skills'], ['roadmap', '/roadmap'], ['assessments', '/assessments'], ['analytics', '/analytics'], ['tutor', '/tutor'], ['study', '/study']]) {
        await page.goto(path); await expect(page.locator('main h1, main h2').first()).toBeVisible(); await page.waitForTimeout(400)
        await expectNoA11yViolations(page, `${name} (${mode})`)
      }
    }
  })
  test('keyboard: skip link and focus-visible navigation', async ({ page }) => {
    await register(page); await page.keyboard.press('Tab'); await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused()
    await page.keyboard.press('Enter'); await expect(page.locator('#main')).toBeFocused()
  })
})
