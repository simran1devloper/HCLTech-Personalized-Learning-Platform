<script setup lang="ts">
import { cn } from '@/utils/cn'
defineProps<{ class?: string }>()
</script>
<template><div aria-hidden="true" :class="cn('animate-pulse rounded-md bg-muted', $props.class)" /></template>
