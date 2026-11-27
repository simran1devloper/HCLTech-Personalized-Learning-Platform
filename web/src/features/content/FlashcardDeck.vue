<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, RotateCw } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
const props = defineProps<{ cards: { front: string; back: string }[] }>()
const i = ref(0); const flipped = ref(false)
const card = computed(() => props.cards[i.value])
watch(i, () => (flipped.value = false))
const go = (d: number) => (i.value = (i.value + d + props.cards.length) % props.cards.length)
</script>
<template>
  <div class="my-3 space-y-2" role="group" aria-label="Flashcards">
    <button type="button" class="block min-h-32 w-full surface surface-raised surface-lift p-6 text-left" :aria-label="flipped ? 'Answer. Activate to show question' : 'Question. Activate to show answer'" @click="flipped = !flipped" @keydown.left.prevent="go(-1)" @keydown.right.prevent="go(1)">
      <span class="text-xs uppercase text-muted-foreground">{{ flipped ? 'Answer' : 'Question' }}</span>
      <span class="mt-1 block text-base" aria-live="polite">{{ flipped ? card.back : card.front }}</span>
    </button>
    <div class="flex items-center justify-between"><span class="text-xs text-muted-foreground">Card {{ i + 1 }} of {{ cards.length }}</span>
      <div class="flex gap-1"><Button size="sm" variant="outline" aria-label="Previous card" @click="go(-1)"><ChevronLeft /></Button><Button size="sm" variant="outline" @click="flipped = !flipped"><RotateCw /> Flip</Button><Button size="sm" variant="outline" aria-label="Next card" @click="go(1)"><ChevronRight /></Button></div></div>
  </div>
</template>
