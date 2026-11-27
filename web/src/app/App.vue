<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import AuthLayout from '@/layouts/AuthLayout.vue'
import Toaster from '@/components/ui/Toaster.vue'

const route = useRoute()
const Layout = computed(() => (route.meta.layout === 'auth' ? AuthLayout : AppShell))
</script>

<template>
  <component :is="Layout"><RouterView /></component>
  <Toaster />
</template>
