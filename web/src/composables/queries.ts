import { keepPreviousData, useQuery } from '@tanstack/vue-query'
import { computed, type Ref } from 'vue'
import { analyticsApi, assessmentApi, careerApi, explorerApi, proficiencyApi, roadmapApi, skillsApi } from '@/services/api'

/** One cache key per resource: the same data is shared by every page (no duplicated server state in Pinia). */
export const keys = {
  summary: ['analytics', 'summary'] as const,
  goals: ['goals'] as const,
  roles: ['careers'] as const,
  skills: ['skills', 'all'] as const,
  assessments: (p: object) => ['assessments', p] as const,
  roadmaps: ['roadmap', 'list'] as const,
  roadmap: (id: string) => ['roadmap', id] as const,
  versions: (id: string) => ['roadmap', id, 'versions'] as const,
  gaps: (role: string) => ['gaps', role] as const,
  skillHistory: (id: string) => ['skill-history', id] as const,
  careerGraph: (role: string) => ['career-graph', role] as const,
  skillDetail: (id: string, role: string) => ['skill-detail', id, role] as const,
}
export const useSummary = () => useQuery({ queryKey: keys.summary, queryFn: ({ signal }) => analyticsApi.summary({ signal }) })
export const useGoals = () => useQuery({ queryKey: keys.goals, queryFn: ({ signal }) => careerApi.goals({ signal }) })
export const useRoles = () => useQuery({ queryKey: keys.roles, queryFn: ({ signal }) => careerApi.roles({ signal }), staleTime: 10 * 60_000 })
export const useAllSkills = () => useQuery({ queryKey: keys.skills, queryFn: ({ signal }) => skillsApi.list({}, { signal }), staleTime: 5 * 60_000 })
export const useRoadmapList = () => useQuery({ queryKey: keys.roadmaps, queryFn: ({ signal }) => roadmapApi.list({ signal }) })
export const useProficiencies = () => useQuery({ queryKey: ['proficiency'], queryFn: ({ signal }) => proficiencyApi.list({ signal }) })
export const useAssessmentList = (limit = 20) => useQuery({ queryKey: keys.assessments({ limit }), queryFn: ({ signal }) => assessmentApi.list({ limit }, { signal }) })
export const useGaps = (role: Ref<string | undefined>) =>
  useQuery({ queryKey: computed(() => keys.gaps(role.value ?? '')), queryFn: ({ signal }) => proficiencyApi.gaps(role.value!, { signal }), enabled: computed(() => !!role.value) })
export const useSkillHistory = (id: Ref<string | undefined>) =>
  useQuery({ queryKey: computed(() => keys.skillHistory(id.value ?? '')), queryFn: ({ signal }) => skillsApi.history(id.value!, { signal }), enabled: computed(() => !!id.value) })

/** The whole career graph in one request, cached per career: switching back to a career you already viewed is instant,
 *  and the previous career stays on screen (no flash of an empty graph) while the next one loads. */
export const useCareerGraph = (role: Ref<string | undefined>) =>
  useQuery({ queryKey: computed(() => keys.careerGraph(role.value ?? '')), queryFn: ({ signal }) => explorerApi.graph(role.value!, { signal }),
    enabled: computed(() => !!role.value), staleTime: 60_000, placeholderData: keepPreviousData })
/** Detail is loaded on demand (only for the selected skill) and cached per (skill, career). */
export const useSkillDetail = (id: Ref<string | undefined>, role: Ref<string | undefined>) =>
  useQuery({ queryKey: computed(() => keys.skillDetail(id.value ?? '', role.value ?? '')), queryFn: ({ signal }) => explorerApi.detail(id.value!, role.value, { signal }),
    enabled: computed(() => !!id.value), staleTime: 60_000 })
