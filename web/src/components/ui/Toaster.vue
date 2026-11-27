<script setup lang="ts">
import { Toaster } from 'vue-sonner'
import { useUiStore } from '@/stores/ui'
import { computed } from 'vue'
const ui = useUiStore()
const theme = computed(() => ui.theme)
</script>
<template><Toaster :theme="theme" position="bottom-right" rich-colors close-button /></template>
