<script setup lang="ts">
import { computed } from 'vue'
import { parseBlocks, type Block } from '@/schemas/blocks'
import ContentBlockRenderer from './ContentBlockRenderer.vue'
import FlashcardDeck from './FlashcardDeck.vue'

/** Renders a list of blocks: validates every block first, hides invalid ones with a notice, and groups consecutive
 *  v2 flashcard blocks into one deck. */
const props = defineProps<{ blocks: unknown }>()
const parsed = computed(() => parseBlocks(props.blocks))
type Item = { kind: 'block'; key: string; block: Block } | { kind: 'deck'; key: string; cards: { front: string; back: string }[] }
const items = computed<Item[]>(() => {
  const out: Item[] = []
  parsed.value.blocks.forEach((b, i) => {
    const key = (b as { id?: string }).id ?? `i${i}`
    const last = out.at(-1)
    if (b.type === 'flashcard') {
      if (last?.kind === 'deck') last.cards.push({ front: b.front, back: b.back })
      else out.push({ kind: 'deck', key: `deck-${key}`, cards: [{ front: b.front, back: b.back }] })
    } else out.push({ kind: 'block', key, block: b })
  })
  return out
})
</script>
<template>
  <div class="space-y-3">
    <template v-for="it in items" :key="it.key">
      <FlashcardDeck v-if="it.kind === 'deck'" :cards="it.cards" />
      <ContentBlockRenderer v-else :block="it.block" />
    </template>
    <p v-if="parsed.rejected.length" class="text-xs text-muted-foreground" role="status">{{ parsed.rejected.length }} content block(s) were hidden because they failed validation.</p>
  </div>
</template>
