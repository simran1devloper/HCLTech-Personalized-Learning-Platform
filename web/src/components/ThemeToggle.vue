<script setup lang="ts">
import { computed } from 'vue'
import { Check, Moon, Sun } from 'lucide-vue-next'
import { DropdownMenuContent, DropdownMenuPortal, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuRoot, DropdownMenuTrigger, DropdownMenuItemIndicator } from 'reka-ui'
import Button from '@/components/ui/Button.vue'
import { useUiStore, type ThemeMode } from '@/stores/ui'

const ui = useUiStore()
const model = computed({ get: () => ui.theme, set: (v: string) => ui.setTheme(v as ThemeMode) })
const options: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun }, { value: 'dark', label: 'Dark', icon: Moon },
]
</script>

<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child>
      <Button variant="ghost" size="icon" :aria-label="`Theme: ${ui.theme}. Open theme menu`" data-testid="theme-toggle">
        <Moon v-if="ui.theme === 'dark'" aria-hidden="true" /><Sun v-else aria-hidden="true" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent align="end" :side-offset="6" class="z-50 min-w-40 surface-popover p-1">
        <DropdownMenuRadioGroup v-model="model" aria-label="Theme">
          <DropdownMenuRadioItem v-for="o in options" :key="o.value" :value="o.value"
            class="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent">
            <component :is="o.icon" class="size-4" aria-hidden="true" />{{ o.label }}
            <DropdownMenuItemIndicator class="ml-auto"><Check class="size-4" aria-hidden="true" /></DropdownMenuItemIndicator>
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
