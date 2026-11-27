<script setup lang="ts">
import { cn } from '@/utils/cn'
defineProps<{ title?: string; description?: string; class?: string }>()
</script>
<template>
  <div :class="cn('flex items-start justify-between gap-3 p-5 pb-3', $props.class)">
    <div class="min-w-0">
      <h2 v-if="title" class="text-sm font-semibold leading-none tracking-tight">{{ title }}</h2>
      <p v-if="description" class="mt-1.5 text-xs text-muted-foreground">{{ description }}</p>
      <slot />
    </div>
    <slot name="action" />
  </div>
</template>
