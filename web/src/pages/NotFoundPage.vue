<script setup lang="ts">
import EmptyState from '@/components/ui/EmptyState.vue'
import Button from '@/components/ui/Button.vue'
import { FileQuestion } from 'lucide-vue-next'
</script>
<template><EmptyState :icon="FileQuestion" title="Page not found" description="The page you requested does not exist."><RouterLink :to="{ name: 'dashboard' }"><Button variant="outline">Back to dashboard</Button></RouterLink></EmptyState></template>
