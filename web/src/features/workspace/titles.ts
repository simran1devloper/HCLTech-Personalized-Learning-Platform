/** Learner-facing section titles in the workspace (same section keys as the backend; Content History keeps its own labels). */
export const WORKSPACE_TITLES: Record<string, string> = {
  summary: 'Overview', key_concepts: 'Key Concepts', deep_dive: 'Deep Dive', real_world_applications: 'Real-world', viva_voice: 'Interview Q&A',
  practical_guide: 'Implementation Guide', learning_roadmap: 'Roadmap', flashcards: 'Flashcards', recommendations: 'Recommendations', agent_intro: 'Tutor Intro',
}
export const titleOf = (key: string, fallback = key) => WORKSPACE_TITLES[key] ?? fallback
