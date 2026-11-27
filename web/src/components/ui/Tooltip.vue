<script setup lang="ts">
import { TooltipArrow, TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from 'reka-ui'
defineProps<{ text: string }>()
</script>
<template>
  <TooltipProvider :delay-duration="300">
    <TooltipRoot>
      <TooltipTrigger as-child><slot /></TooltipTrigger>
      <TooltipPortal>
        <TooltipContent side="bottom" :side-offset="6" class="z-50 rounded-md bg-foreground px-2.5 py-1.5 text-xs text-background">{{ text }}<TooltipArrow class="fill-foreground" /></TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
