<script setup lang="ts">
import { ProgressIndicator, ProgressRoot } from 'reka-ui'
import { cn } from '@/utils/cn'
withDefaults(defineProps<{ value: number; max?: number; label: string; class?: string }>(), { max: 100 })
</script>
<template>
  <ProgressRoot :model-value="value" :max="max" :aria-label="label" :class="cn('relative h-2 w-full overflow-hidden rounded-full bg-muted', $props.class)">
    <ProgressIndicator class="h-full rounded-full bg-primary transition-all" :style="{ width: `${Math.min(100, Math.max(0, (value / max) * 100))}%` }" />
  </ProgressRoot>
</template>
