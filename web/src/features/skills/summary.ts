import type { GraphNode, RecAction } from '@/types/api'

/** Derived, deterministic summaries for the "no skill selected" panel. They only re-read the graph payload: no extra requests. */
export const ACTION_LABEL: Record<RecAction, string> = {
  revise: 'Revise', learn_prerequisites: 'Learn prerequisites first', start_learning: 'Start learning', continue_learning: 'Continue learning', take_assessment: 'Take assessment',
}

/** Critical targets that are not mastered, most foundational first. */
export function criticalGaps(nodes: GraphNode[]): GraphNode[] {
  return nodes.filter((n) => n.is_target && n.critical && n.status !== 'mastered').sort((a, b) => a.depth - b.depth || b.importance - a.importance || a.name.localeCompare(b.name))
}
/** Hours of study left to reach every career target (targets only; prerequisite-only skills are counted through `milestone`). */
export const hoursToTargets = (nodes: GraphNode[]) => Math.round(nodes.filter((n) => n.is_target).reduce((s, n) => s + n.hours_remaining, 0))

export interface Milestone { node: GraphNode; prerequisites: GraphNode[]; hours: number }
/** The next career milestone: the most foundational unmastered critical target (else any target), with the prerequisites that must come first. */
export function nextMilestone(nodes: GraphNode[]): Milestone | null {
  const open = nodes.filter((n) => n.is_target && n.status !== 'mastered')
  if (!open.length) return null
  const pick = [...open].sort((a, b) => Number(b.critical) - Number(a.critical) || a.depth - b.depth || b.importance - a.importance || a.name.localeCompare(b.name))[0]
  const byId = new Map(nodes.map((n) => [n.skill_id, n])); const prerequisites = pick.missing_prerequisites.map((id) => byId.get(id)).filter((n): n is GraphNode => !!n)
  return { node: pick, prerequisites, hours: Math.round(pick.hours_remaining + prerequisites.reduce((s, n) => s + n.hours_remaining, 0)) }
}
