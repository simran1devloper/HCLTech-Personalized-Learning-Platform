import { BarChart3, BookOpenCheck, Bot, ClipboardCheck, History, LayoutDashboard, Map, Network } from 'lucide-vue-next'
import type { Component } from 'vue'

export interface NavItem { name: string; label: string; icon: Component; keywords: string }
export const NAV: NavItem[] = [
  { name: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, keywords: 'home overview readiness' },
  { name: 'skills', label: 'Skill Explorer', icon: Network, keywords: 'graph prerequisites proficiency' },
  { name: 'roadmap', label: 'Roadmap', icon: Map, keywords: 'plan milestones career goal replan' },
  { name: 'assessments', label: 'Assessments', icon: ClipboardCheck, keywords: 'quiz test exam history' },
  { name: 'study', label: 'Diagnostic Study', icon: BookOpenCheck, keywords: 'topic quiz flashcards content' },
  { name: 'tutor', label: 'AI Tutor', icon: Bot, keywords: 'chat ask help' },
  { name: 'history', label: 'Content History', icon: History, keywords: 'saved sessions generated content continue resume previous study guides' },
  { name: 'analytics', label: 'Analytics', icon: BarChart3, keywords: 'charts progress trends' },
]
