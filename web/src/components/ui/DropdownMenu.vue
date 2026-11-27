<script setup lang="ts">
import { DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuPortal, DropdownMenuRoot, DropdownMenuSeparator, DropdownMenuTrigger } from 'reka-ui'
export interface MenuItem { label: string; onSelect: () => void; destructive?: boolean; separatorBefore?: boolean }
defineProps<{ items: MenuItem[]; heading?: string }>() // the slotted trigger supplies its own accessible name
</script>
<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child><slot /></DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent align="end" :side-offset="6" class="z-50 min-w-48 surface-popover p-1">
        <DropdownMenuLabel v-if="heading" class="px-2 py-1.5 text-xs text-muted-foreground">{{ heading }}</DropdownMenuLabel>
        <template v-for="it in items" :key="it.label">
          <DropdownMenuSeparator v-if="it.separatorBefore" class="my-1 h-px bg-border" />
          <DropdownMenuItem :class="['cursor-pointer rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent', it.destructive && 'text-destructive']" @select="it.onSelect()">{{ it.label }}</DropdownMenuItem>
        </template>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
