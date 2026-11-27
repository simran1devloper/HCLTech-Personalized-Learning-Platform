<script setup lang="ts">
import { cn } from '@/utils/cn'
defineProps<{ class?: string }>()
const model = defineModel<string>()
</script>
<template>
  <textarea v-model="model" :class="cn('field flex min-h-20 w-full px-3 py-2 text-sm placeholder:text-muted-foreground disabled:opacity-50', $props.class)" />
</template>
