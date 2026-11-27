<script setup lang="ts">
import { AlertTriangle } from 'lucide-vue-next'
import Button from './Button.vue'
withDefaults(defineProps<{ message?: string; retryable?: boolean }>(), { retryable: true })
defineEmits<{ retry: [] }>()
</script>
<template>
  <div role="alert" class="flex flex-col items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-center">
    <AlertTriangle class="size-6 text-destructive" aria-hidden="true" />
    <p class="text-sm font-medium">Something went wrong</p>
    <p class="max-w-md text-xs text-muted-foreground">{{ message || 'The data could not be loaded.' }}</p>
    <Button v-if="retryable !== false" variant="outline" size="sm" @click="$emit('retry')">Try again</Button>
  </div>
</template>
