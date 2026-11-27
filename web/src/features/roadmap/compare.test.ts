import { describe, expect, it } from 'vitest'
import { compareVersions, weekBlocks } from './compare'
import type { Milestone, Roadmap } from '@/types/api'

const m = (skill: string, status: Milestone['status'] = 'pending', hours = 10, order = 1, kind = 'learn'): Milestone => ({ item_id: skill + kind, skill_id: skill, skill_name: skill, order, kind, from_level: 0, target_level: 3, prerequisites: [], estimated_hours: hours, objectives: [], resource_ids: [], checkpoint: '', completion_criteria: { min_proficiency: 3, min_confidence: 0.5 }, status, blocked_by: [], completed_at: null })
const rm = (ms: Milestone[], v = 1, per = 5): Roadmap => ({ roadmap_id: 'r', learner_id: 'u', goal: 'g', version: v, hours_per_week: per, milestones: ms, total_hours: ms.filter((x) => !['completed', 'skipped_mastered', 'superseded'].includes(x.status)).reduce((a, x) => a + x.estimated_hours, 0), estimated_weeks: 0, created_at: '', change_summary: [] })

describe('roadmap compare', () => {
  it('reports added, removed, status changes and hours delta', () => {
    const a = rm([m('py'), m('sql'), m('redis')]); const b = rm([m('py', 'completed'), m('sql'), m('docker')], 2)
    const d = compareVersions(a, b)
    expect(d.added.map((x) => x.skill_name)).toEqual(['docker']); expect(d.removed.map((x) => x.skill_name)).toEqual(['redis'])
    expect(d.statusChanged).toEqual([{ skill: 'py', from: 'pending', to: 'completed' }]); expect(d.hoursDelta).toBe(-10)
  })
  it('ignores superseded milestones', () => { expect(compareVersions(rm([m('a')]), rm([m('a'), m('b', 'superseded')])).added).toEqual([]) })
  it('groups milestones into estimated 4-week blocks from backend hours', () => {
    const blocks = weekBlocks(rm([m('a', 'pending', 12, 1), m('b', 'pending', 12, 2), m('c', 'pending', 12, 3)], 1, 5)) // 20h per 4 weeks
    expect(blocks.map((b) => [b.label, b.items.length])).toEqual([['Weeks 1–4', 2], ['Weeks 5–8', 1]])
  })
  it('completed work does not consume future schedule time', () => {
    expect(weekBlocks(rm([m('a', 'completed', 100, 1), m('b', 'pending', 10, 2)], 1, 5)).length).toBe(1)
  })
})
