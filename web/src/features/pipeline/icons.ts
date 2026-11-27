import { Bot, FileText, Globe, KeyRound, Layers, ListChecks, Map as MapIcon, Microscope, Mic, Save, ShieldCheck, Target, Wrench } from 'lucide-vue-next'
import type { Component } from 'vue'

/** Lucide tokens sent by the backend section registry -> components. Closed set: an unknown token falls back to a document icon,
 *  so a bad value can never break rendering (and the model never chooses icons). */
const BY_TOKEN: Record<string, Component> = {
  'file-text': FileText, 'key-round': KeyRound, microscope: Microscope, globe: Globe, mic: Mic, wrench: Wrench, map: MapIcon,
  layers: Layers, target: Target, bot: Bot,
}
/** Checkpoints that are not sections have fixed icons. */
const BY_STAGE: Record<string, Component> = { plan: ListChecks, review: ShieldCheck, save: Save }

export function stageIcon(stage: { id: string; icon?: string }): Component | null {
  if (stage.icon) return BY_TOKEN[stage.icon] ?? FileText
  return BY_STAGE[stage.id] ?? null
}
