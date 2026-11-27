<script setup lang="ts">
import { cn } from '@/utils/cn'
defineProps<{ class?: string }>()
const model = defineModel<string>()
</script>
<template>
  <input v-model="model" :class="cn('field flex h-9 w-full px-3 py-1 text-sm placeholder:text-muted-foreground disabled:opacity-50', $props.class)" />
</template>
