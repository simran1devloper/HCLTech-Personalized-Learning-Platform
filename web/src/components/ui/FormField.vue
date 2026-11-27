<script setup lang="ts">
import { useId } from 'vue'
defineProps<{ label: string; error?: string }>()
const id = useId()
defineExpose({ id })
</script>
<template>
  <div class="space-y-1.5">
    <label :for="id" class="text-sm font-medium">{{ label }}</label>
    <slot :id="id" :described-by="error ? id + '-err' : undefined" :invalid="!!error" />
    <p v-if="error" :id="id + '-err'" role="alert" class="text-xs text-destructive">{{ error }}</p>
  </div>
</template>
