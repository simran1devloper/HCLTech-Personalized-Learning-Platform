import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSkillExplorerStore } from './skillExplorer'

beforeEach(() => setActivePinia(createPinia()))
describe('skill explorer store', () => {
  it('selecting and clearing a skill; clearing also leaves focus mode', () => {
    const s = useSkillExplorerStore(); s.select('a'); s.focusMode = true; expect(s.selectedId).toBe('a')
    s.select(null); expect(s.selectedId).toBeNull(); expect(s.focusMode).toBe(false)
  })
  it('changing career resets selection and focus but keeps filters and view', () => {
    const s = useSkillExplorerStore(); s.select('a'); s.focusMode = true; s.category = 'ai'; s.view = 'list'
    s.setRole('devops-engineer'); expect(s.role).toBe('devops-engineer'); expect(s.selectedId).toBeNull(); expect(s.focusMode).toBe(false); expect(s.category).toBe('ai'); expect(s.view).toBe('list')
    s.select('b'); s.setRole('devops-engineer'); expect(s.selectedId).toBe('b')   // same career: nothing is reset
  })
  it('toggles statuses and resets filters', () => {
    const s = useSkillExplorerStore(); s.toggleStatus('ready'); s.toggleStatus('blocked'); expect(s.statuses).toEqual(['ready', 'blocked'])
    s.toggleStatus('ready'); expect(s.statuses).toEqual(['blocked']); s.search = 'x'; s.category = 'ai'; s.resetFilters()
    expect([s.search, s.category, s.statuses]).toEqual(['', '', []])
  })
})
