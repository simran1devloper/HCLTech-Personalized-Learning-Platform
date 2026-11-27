<script setup lang="ts">
import { GraduationCap } from 'lucide-vue-next'
import ThemeToggle from '@/components/ThemeToggle.vue'
</script>
<template>
  <div class="grid min-h-screen lg:grid-cols-2">
    <div class="hidden flex-col justify-between border-r bg-accent/60 p-10 lg:flex">
      <div class="flex items-center gap-2 text-lg font-semibold text-primary"><GraduationCap class="size-6" aria-hidden="true" /> LearnAI</div>
      <div class="max-w-md space-y-3">
        <h2 class="text-3xl font-semibold leading-tight">Personalized learning, grounded in evidence.</h2>
        <p class="text-sm text-muted-foreground">Skills, prerequisites, adaptive assessments and a roadmap that updates as you learn. Proficiency is computed from your results, never guessed.</p>
      </div>
      <p class="text-xs text-muted-foreground">Readiness reflects assessed competency, not a hiring prediction.</p>
    </div>
    <main id="main" class="relative flex items-center justify-center p-6"><div class="absolute right-4 top-4"><ThemeToggle /></div><div class="w-full max-w-sm"><slot /></div></main>
  </div>
</template>
