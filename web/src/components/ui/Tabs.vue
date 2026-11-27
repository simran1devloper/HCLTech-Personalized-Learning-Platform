<script setup lang="ts">
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'
defineProps<{ tabs: { value: string; label: string }[]; label: string }>()
const model = defineModel<string>({ required: true })
</script>
<template>
  <TabsRoot v-model="model">
    <TabsList :aria-label="label" class="inline-flex flex-wrap gap-1 surface-inset p-1">
      <TabsTrigger v-for="t in tabs" :key="t.value" :value="t.value" class="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground transition-[box-shadow,background] data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-[var(--shadow-1)] cursor-pointer">{{ t.label }}</TabsTrigger>
    </TabsList>
    <TabsContent v-for="t in tabs" :key="t.value" :value="t.value" class="mt-4"><slot :name="t.value" /></TabsContent>
  </TabsRoot>
</template>
