import { AlertTriangle, CheckCircle2, Circle, Lock, PlayCircle, RotateCcw, Timer } from 'lucide-vue-next'
import type { Component } from 'vue'
import type { SkillStatus } from '@/types/api'

export interface StatusMeta { label: string; hint: string; icon: Component; node: string; chip: string; dot: string; icontone: string }
/** One place that defines how each of the six states looks. Colours come from the theme tokens (light theme preserved). */
export const STATUS: Record<SkillStatus, StatusMeta> = {
  mastered: { label: 'Mastered', hint: 'Validated by graded assessments at or above the required level.', icon: CheckCircle2,
    node: 'border-success/40 bg-success/10', chip: 'bg-success/15 text-success', dot: 'bg-success', icontone: 'text-success' },
  in_progress: { label: 'In progress', hint: 'You have evidence or study progress, but have not reached the required level yet.', icon: Timer,
    node: 'border-primary/40 bg-accent', chip: 'bg-primary/10 text-primary', dot: 'bg-primary', icontone: 'text-primary' },
  ready: { label: 'Ready to learn', hint: 'All prerequisites are satisfied and you have not started.', icon: PlayCircle,
    node: 'border-primary bg-card ring-1 ring-primary/25', chip: 'bg-primary/10 text-primary', dot: 'bg-primary/60', icontone: 'text-primary' },
  blocked: { label: 'Blocked', hint: 'You started this skill, but prerequisites are still missing.', icon: Lock,
    node: 'border-warning/50 bg-warning/10', chip: 'bg-warning/15 text-warning', dot: 'bg-warning', icontone: 'text-warning' },
  not_started: { label: 'Not started', hint: 'Not started, and prerequisites are not satisfied yet.', icon: Circle,
    node: 'border-border bg-muted', chip: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/50', icontone: 'text-muted-foreground' },
  needs_revision: { label: 'Needs revision', hint: 'Mastery was shown before, but the evidence is out of date or has regressed.', icon: RotateCcw,
    node: 'border-revision/50 bg-revision/10', chip: 'bg-revision/15 text-revision', dot: 'bg-revision', icontone: 'text-revision' },
}
export const STATUS_ORDER: SkillStatus[] = ['mastered', 'in_progress', 'ready', 'blocked', 'not_started', 'needs_revision']
export const UNMET_ICON = AlertTriangle

const HUES = [265, 150, 30, 330, 200, 85, 10, 290]
const hash = (s: string) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h }
/** Stable colour per category across careers (hash, not position), so "ai" is the same colour in every graph. */
export const categoryColor = (c: string) => `oklch(0.58 0.15 ${HUES[hash(c) % HUES.length]})`
export const categoryLabel = (c: string) => c.charAt(0).toUpperCase() + c.slice(1)

/** Human label for a 0-5 proficiency (same bands as the backend's `band`). */
export const levelLabel = (p: number) => (p <= 0 ? 'Not assessed' : p < 2 ? 'Beginner' : p < 3.75 ? 'Intermediate' : 'Expert')
export const hoursLabel = (h: number) => (h <= 0 ? '—' : h < 1.5 ? '~1 hr' : `~${Math.round(h)} hrs`)
