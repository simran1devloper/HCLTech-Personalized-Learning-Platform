<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { MessageSquarePlus, Pencil, Pin, PinOff, Search, Trash2 } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import { useDebounced } from '@/composables/useDebounced'
import { tutorApi } from '@/services/api'
import { relativeTime } from '@/utils/format'
import { groupConversations } from './grouping'
import type { TutorConversation } from '@/types/api'

const props = defineProps<{ active: string | null }>()
const emit = defineEmits<{ select: [id: string]; fresh: []; removed: [id: string] }>()
const qc = useQueryClient()
const search = ref(''); const term = useDebounced(search, 300)
const convs = useQuery({ queryKey: computed(() => ['tutor', 'conversations', term.value.trim()]), queryFn: ({ signal }) => tutorApi.conversations({ q: term.value.trim() || undefined }, { signal }) })
const groups = computed(() => groupConversations(convs.data.value?.items ?? []))
const refresh = () => qc.invalidateQueries({ queryKey: ['tutor', 'conversations'] })

const renaming = ref<TutorConversation | null>(null); const title = ref(''); const deleting = ref<TutorConversation | null>(null); const saving = ref(false)
async function act(fn: () => Promise<unknown>, failure: string) {
  saving.value = true
  try { await fn(); await refresh(); return true } catch (e) { toast.error(`${failure}: ${(e as Error).message}`); return false } finally { saving.value = false }
}
function startRename(c: TutorConversation) { renaming.value = c; title.value = c.title }
async function rename() {
  const c = renaming.value; const t = title.value.trim()
  if (c && t && (await act(() => tutorApi.update(c.conversation_id, { title: t }), 'Rename failed'))) renaming.value = null
}
async function remove() {
  const c = deleting.value
  if (c && (await act(() => tutorApi.remove(c.conversation_id), 'Delete failed'))) { deleting.value = null; emit('removed', c.conversation_id) }
}
const pin = (c: TutorConversation) => act(() => tutorApi.update(c.conversation_id, { pinned: !c.pinned }), 'Pin failed')
</script>

<template>
  <div class="space-y-2">
    <Button class="w-full" variant="outline" @click="emit('fresh')"><MessageSquarePlus /> New conversation</Button>
    <label class="relative block"><Search class="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" aria-hidden="true" />
      <input v-model="search" type="search" class="field h-9 w-full pl-8 pr-2 text-sm" placeholder="Search conversations" aria-label="Search conversations" maxlength="80" /></label>
    <Skeleton v-if="convs.isPending.value" class="h-24" />
    <ErrorState v-else-if="convs.isError.value" :message="(convs.error.value as Error).message" @retry="convs.refetch()" />
    <p v-else-if="!groups.length" class="px-1 text-xs text-muted-foreground">{{ term.trim() ? 'No matching conversations.' : 'No conversations yet.' }}</p>
    <section v-for="g in groups" v-else :key="g.label" class="space-y-1">
      <h2 class="px-1 pt-1 text-xs font-semibold text-muted-foreground">{{ g.label }}</h2>
      <ul class="space-y-1"><li v-for="c in g.items" :key="c.conversation_id" :class="['group rounded-md hover:bg-accent', c.conversation_id === props.active && 'bg-accent']">
        <button type="button" :aria-current="c.conversation_id === props.active ? 'true' : undefined" class="w-full px-3 pt-2 text-left text-sm" @click="emit('select', c.conversation_id)">
          <span class="line-clamp-1 font-medium">{{ c.title }}</span>
          <span v-if="c.preview" class="line-clamp-1 text-xs text-muted-foreground">{{ c.preview }}</span>
          <span class="text-xs text-muted-foreground">{{ relativeTime(c.updated_at) }}</span></button>
        <div class="flex justify-end gap-0.5 px-1 pb-1 lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100">
          <button type="button" class="rounded p-1.5 text-muted-foreground hover:bg-card hover:text-foreground" :aria-label="c.pinned ? 'Unpin conversation' : 'Pin conversation'" :title="c.pinned ? 'Unpin' : 'Pin'" @click="pin(c)"><component :is="c.pinned ? PinOff : Pin" class="size-3.5" aria-hidden="true" /></button>
          <button type="button" class="rounded p-1.5 text-muted-foreground hover:bg-card hover:text-foreground" aria-label="Rename conversation" title="Rename" @click="startRename(c)"><Pencil class="size-3.5" aria-hidden="true" /></button>
          <button type="button" class="rounded p-1.5 text-muted-foreground hover:bg-card hover:text-destructive" aria-label="Delete conversation" title="Delete" @click="deleting = c"><Trash2 class="size-3.5" aria-hidden="true" /></button>
        </div></li></ul>
    </section>

    <Dialog :open="!!renaming" title="Rename conversation" @update:open="(v) => !v && (renaming = null)">
      <form class="space-y-3" @submit.prevent="rename"><input v-model="title" class="field h-9 w-full px-3 text-sm" aria-label="Conversation title" maxlength="80" required />
        <div class="flex justify-end gap-2"><Button variant="ghost" @click="renaming = null">Cancel</Button><Button type="submit" :loading="saving" :disabled="!title.trim()">Save</Button></div></form></Dialog>
    <Dialog :open="!!deleting" title="Delete conversation" :description="`“${deleting?.title}” and its messages will be permanently deleted.`" @update:open="(v) => !v && (deleting = null)">
      <div class="flex justify-end gap-2"><Button variant="ghost" @click="deleting = null">Cancel</Button><Button variant="destructive" :loading="saving" @click="remove">Delete</Button></div></Dialog>
  </div>
</template>
