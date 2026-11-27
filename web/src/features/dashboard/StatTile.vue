<script setup lang="ts">
import type { Component } from 'vue'
import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
defineProps<{ label: string; value?: string; hint?: string; icon: Component; loading?: boolean; unavailable?: string }>()
</script>
<template>
  <Card class="p-4">
    <div class="flex items-center justify-between text-xs text-muted-foreground"><span>{{ label }}</span><component :is="icon" class="size-4" aria-hidden="true" /></div>
    <Skeleton v-if="loading" class="mt-2 h-7 w-20" />
    <template v-else>
      <p class="mt-1.5 text-2xl font-semibold tabular-nums" :aria-label="unavailable ? `${label}: not available` : undefined">{{ unavailable ? '—' : value }}</p>
      <p v-if="unavailable" class="mt-1 text-xs text-muted-foreground">Not available: {{ unavailable }}</p>
      <p v-else-if="hint" class="mt-1 text-xs text-muted-foreground">{{ hint }}</p>
    </template>
  </Card>
</template>
