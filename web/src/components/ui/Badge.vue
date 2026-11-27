<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'
const props = defineProps<{ tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'; class?: string }>()
const tones = {
  neutral: 'bg-muted text-foreground', info: 'bg-accent text-accent-foreground',
  success: 'bg-success/15 text-success', warning: 'bg-warning/15 text-warning', danger: 'bg-destructive/15 text-destructive',
}
const cls = computed(() => cn('inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium', tones[props.tone ?? 'neutral'], props.class))
</script>
<template><span :class="cls"><slot /></span></template>
