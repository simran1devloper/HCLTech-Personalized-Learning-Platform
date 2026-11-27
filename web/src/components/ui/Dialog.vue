<script setup lang="ts">
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'
import { X } from 'lucide-vue-next'
// reka-ui Dialog provides focus trap, Escape to close, aria-modal and focus restore on close.
defineProps<{ title: string; description?: string; wide?: boolean }>()
const open = defineModel<boolean>('open', { default: false })
</script>
<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-slate-900/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <DialogContent :class="['fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] -translate-x-1/2 surface surface-hero p-6', wide ? 'max-w-2xl' : 'max-w-md']">
        <DialogTitle class="text-base font-semibold">{{ title }}</DialogTitle>
        <DialogDescription :class="description ? 'mt-1 text-sm text-muted-foreground' : 'sr-only'">{{ description ?? title }}</DialogDescription>
        <div class="mt-4"><slot /></div>
        <DialogClose class="absolute right-3 top-3 rounded-md p-1.5 text-muted-foreground hover:bg-accent" aria-label="Close dialog"><X class="size-4" aria-hidden="true" /></DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
