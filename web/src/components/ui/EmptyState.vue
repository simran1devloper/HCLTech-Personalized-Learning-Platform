<script setup lang="ts">
import type { Component } from 'vue'
defineProps<{ icon?: Component; title: string; description?: string }>()
</script>
<template>
  <div class="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center">
    <component :is="icon" v-if="icon" class="size-8 text-muted-foreground" aria-hidden="true" />
    <p class="text-sm font-medium">{{ title }}</p>
    <p v-if="description" class="max-w-md text-xs text-muted-foreground">{{ description }}</p>
    <div class="mt-2"><slot /></div>
  </div>
</template>
