import type { HistoryFilter, HistorySort, SectionStatus, SessionStatus } from '@/types/api'

export const CONTENT_TYPES: { value: string; label: string }[] = [
  { value: 'study_guide', label: 'Study Guide' }, { value: 'course', label: 'Course' }, { value: 'technical_deep_dive', label: 'Technical Deep Dive' },
  { value: 'practical_implementation', label: 'Practical Implementation' }, { value: 'roadmap', label: 'Roadmap' }, { value: 'assessment', label: 'Assessment' },
  { value: 'interview_preparation', label: 'Interview Preparation' }, { value: 'other', label: 'Other generated content' },
]
export const FILTERS: { value: HistoryFilter; label: string }[] = [
  { value: 'all', label: 'All content' }, { value: 'completed', label: 'Completed' }, { value: 'in_progress', label: 'In progress' }, { value: 'failed', label: 'Failed' }, { value: 'archived', label: 'Archived' },
]
export const SORTS: { value: HistorySort; label: string }[] = [
  { value: 'updated', label: 'Recently updated' }, { value: 'created', label: 'Recently created' }, { value: 'accessed', label: 'Recently accessed' }, { value: 'alphabetical', label: 'Alphabetical' },
]
export const contentTypeLabel = (v: string) => CONTENT_TYPES.find((c) => c.value === v)?.label ?? 'Other generated content'

type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'
const STATUS: Record<SessionStatus, { label: string; tone: Tone }> = {
  completed: { label: 'Completed', tone: 'success' }, active: { label: 'In progress', tone: 'info' }, generating: { label: 'Generating', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' }, archived: { label: 'Archived', tone: 'neutral' },
}
export const sessionStatus = (s: SessionStatus) => STATUS[s] ?? STATUS.active
const SECTION: Record<SectionStatus, { label: string; tone: Tone }> = {
  COMPLETED: { label: 'Ready', tone: 'success' }, GENERATING: { label: 'Generating', tone: 'warning' }, QUEUED: { label: 'Queued', tone: 'warning' },
  FAILED: { label: 'Failed', tone: 'danger' }, NOT_STARTED: { label: 'Not generated', tone: 'neutral' },
}
export const sectionStatus = (s: SectionStatus) => SECTION[s] ?? SECTION.NOT_STARTED

/** "8/9 sections completed" — the progress line shown on every history card. */
export const progressText = (done: number, total: number) => `${done}/${total} sections completed`

/** Backend section keys → the study tabs they appear under (follow-up sections are grouped under one tab). */
export const SECTION_TABS: { key: string; tab: string; label: string }[] = [
  { key: 'summary', tab: 'summary', label: 'Summary' }, { key: 'key_concepts', tab: 'concepts', label: 'Key concepts' }, { key: 'deep_dive', tab: 'deep', label: 'Deep dive' },
  { key: 'real_world_applications', tab: 'apps', label: 'Real-world' }, { key: 'viva_voice', tab: 'viva', label: 'Interview Q&A' },
  { key: 'practical_guide', tab: 'practice', label: 'Practical Implementation Guide' }, { key: 'learning_roadmap', tab: 'road', label: 'Roadmap' },
  { key: 'flashcards', tab: 'cards', label: 'Flashcards' }, { key: 'recommendations', tab: 'recs', label: 'Recommendations' },
]
export const isExtra = (key: string) => key.startsWith('extra-')
