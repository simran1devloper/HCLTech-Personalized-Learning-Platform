import type { TutorConversation } from '@/types/api'

export interface ConversationGroup { label: string; items: TutorConversation[] }
const ORDER = ['Pinned', 'Today', 'Yesterday', 'Previous 7 days', 'Older']

/** Pinned first, then by calendar day of the last activity (local time). Input order is preserved inside each group. */
export function groupConversations(items: TutorConversation[], now = new Date()): ConversationGroup[] {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 86_400_000
  const label = (c: TutorConversation) => {
    if (c.pinned) return 'Pinned'
    const t = new Date(c.updated_at).getTime()
    if (t >= midnight) return 'Today'
    if (t >= midnight - day) return 'Yesterday'
    return t >= midnight - 7 * day ? 'Previous 7 days' : 'Older'
  }
  return ORDER.map((l) => ({ label: l, items: items.filter((c) => label(c) === l) })).filter((g) => g.items.length)
}
