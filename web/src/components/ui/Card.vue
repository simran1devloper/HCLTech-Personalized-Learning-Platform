<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'
// elevation: 'raised' (default, primary content) | 'flat' (secondary/nested containers) | 'hero' (focal cards).
// interactive lifts on hover (translateY(-2px) + stronger shadow); pass false for large, non-clickable containers.
const props = withDefaults(defineProps<{ class?: string; elevation?: 'flat' | 'raised' | 'hero'; interactive?: boolean }>(), { elevation: 'raised', interactive: true })
const cls = computed(() => cn('surface', props.elevation === 'raised' && 'surface-raised', props.elevation === 'hero' && 'surface-hero', props.interactive && 'surface-lift', props.class))
</script>
<template><div :class="cls"><slot /></div></template>
