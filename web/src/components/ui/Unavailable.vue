<script setup lang="ts">
import { Info } from 'lucide-vue-next'
defineProps<{ reason: string }>()
</script>
<template><p class="flex items-start gap-1.5 text-xs text-muted-foreground"><Info class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /><span>Not available: {{ reason }}</span></p></template>
