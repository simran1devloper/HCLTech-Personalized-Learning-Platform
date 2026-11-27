<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useOnline } from '@vueuse/core'
import { toast } from 'vue-sonner'
import { FolderOpen, History, MoreHorizontal, Play, Plus, Search, Trash2, WifiOff } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Dialog from '@/components/ui/Dialog.vue'
import DropdownMenu, { type MenuItem } from '@/components/ui/DropdownMenu.vue'
import { useDebounced } from '@/composables/useDebounced'
import { historyApi } from '@/services/api'
import { useContentHistoryStore } from '@/stores/contentHistory'
import { CONTENT_TYPES, FILTERS, SORTS, contentTypeLabel, progressText, sessionStatus } from '@/features/history/historyUi'
import type { ContentSessionSummary, HistoryFilter, HistorySort } from '@/types/api'
import { relativeTime } from '@/utils/format'

const router = useRouter(); const qc = useQueryClient(); const pointer = useContentHistoryStore(); const online = useOnline()
const search = ref(''); const q = useDebounced(search, 300)
const status = ref<HistoryFilter>('all'); const contentType = ref(''); const sort = ref<HistorySort>('updated')
const PAGE = 12; const offset = ref(0)
watch([q, status, contentType, sort], () => (offset.value = 0)) // a new query always starts from page 1

const params = computed(() => ({ q: q.value.trim() || undefined, status: status.value, content_type: contentType.value || undefined, sort: sort.value, offset: offset.value, limit: PAGE }))
const list = useQuery({ queryKey: computed(() => ['content-sessions', params.value]), queryFn: ({ signal }) => historyApi.list(params.value, { signal }), placeholderData: (p) => p })
const items = computed(() => list.data.value?.items ?? [])
const total = computed(() => list.data.value?.total ?? 0)
const filtering = computed(() => !!q.value.trim() || status.value !== 'all' || !!contentType.value)
const refresh = () => qc.invalidateQueries({ queryKey: ['content-sessions'] })

const renaming = ref<ContentSessionSummary | null>(null); const newTitle = ref('')
const deleting = ref<ContentSessionSummary | null>(null)
const dialogOpen = (r: { value: unknown }) => computed({ get: () => !!r.value, set: (v: boolean) => { if (!v) r.value = null } })
const renameOpen = dialogOpen(renaming); const deleteOpen = dialogOpen(deleting)
const fail = (e: unknown) => toast.error((e as Error).message)
const rename = useMutation({ mutationFn: () => historyApi.rename(renaming.value!.id, newTitle.value.trim()), onSuccess: () => { renaming.value = null; refresh(); toast.success('Renamed') }, onError: fail })
const archive = useMutation({ mutationFn: (s: ContentSessionSummary) => (s.archived_at ? historyApi.restore(s.id) : historyApi.archive(s.id)), onSuccess: (_, s) => { refresh(); toast.success(s.archived_at ? 'Restored' : 'Archived') }, onError: fail })
const duplicate = useMutation({ mutationFn: (s: ContentSessionSummary) => historyApi.duplicate(s.id), onSuccess: () => { refresh(); toast.success('Copy created') }, onError: fail })
const remove = useMutation({ mutationFn: () => historyApi.remove(deleting.value!.id), onSuccess: () => { pointer.forget(deleting.value!.id); deleting.value = null; refresh(); toast.success('Deleted') }, onError: fail })

const open = (s: ContentSessionSummary, resume = false) => router.push({ name: 'history-session', params: { id: s.id }, query: resume ? { resume: '1' } : undefined })
const startRename = (s: ContentSessionSummary) => { renaming.value = s; newTitle.value = s.title }
const menu = (s: ContentSessionSummary): MenuItem[] => [
  { label: 'Open', onSelect: () => open(s) },
  { label: 'Continue learning', onSelect: () => open(s, true) },
  { label: 'Rename', onSelect: () => startRename(s) },
  { label: 'Duplicate', onSelect: () => duplicate.mutate(s) },
  { label: s.archived_at ? 'Restore' : 'Archive', onSelect: () => archive.mutate(s) },
  { label: 'Delete', destructive: true, separatorBefore: true, onSelect: () => (deleting.value = s) },
]
const needsResume = (s: ContentSessionSummary) => s.status !== 'completed' && s.status !== 'archived'
const selectCls = 'h-9 field px-2 text-sm'
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">My Learning History</h1><p class="text-sm text-muted-foreground">Access and continue your previously generated learning content.</p></div>
      <Button @click="router.push({ name: 'study' })"><Plus /> New Content</Button>
    </div>

    <div v-if="!online" role="status" class="flex items-center gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm"><WifiOff class="size-4" aria-hidden="true" />You are offline. Showing what was loaded earlier; changes will not be saved until you reconnect.</div>

    <Card v-if="pointer.last && !filtering" class="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
      <span class="text-muted-foreground">Pick up where you left off: <span class="font-medium text-foreground">{{ pointer.last.title }}</span></span>
      <Button size="sm" variant="outline" @click="router.push({ name: 'history-session', params: { id: pointer.last.id } })"><Play /> Continue</Button>
    </Card>

    <div class="flex flex-wrap items-center gap-2" role="search">
      <div class="relative min-w-56 flex-1"><Search class="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden="true" />
        <Input v-model="search" type="search" aria-label="Search history" placeholder="Search topics, tags, concepts…" maxlength="80" class="pl-8" /></div>
      <select v-model="status" aria-label="Filter by status" :class="selectCls"><option v-for="f in FILTERS" :key="f.value" :value="f.value">{{ f.label }}</option></select>
      <select v-model="contentType" aria-label="Filter by content type" :class="selectCls"><option value="">All types</option><option v-for="c in CONTENT_TYPES" :key="c.value" :value="c.value">{{ c.label }}</option></select>
      <select v-model="sort" aria-label="Sort by" :class="selectCls"><option v-for="s in SORTS" :key="s.value" :value="s.value">{{ s.label }}</option></select>
    </div>

    <div v-if="list.isPending.value" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" role="status" aria-label="Loading history"><Skeleton v-for="i in 6" :key="i" class="h-36" /></div>
    <ErrorState v-else-if="list.isError.value && !items.length" :message="(list.error.value as Error)?.message" @retry="list.refetch()" />
    <EmptyState v-else-if="!items.length && !filtering" :icon="History" title="No saved content yet" description="Everything you generate is saved here automatically, so you can reopen and continue it any time.">
      <Button @click="router.push({ name: 'study' })"><Plus /> Generate your first study guide</Button></EmptyState>
    <EmptyState v-else-if="!items.length" :icon="Search" title="Nothing matches" description="Try a different search or clear the filters." />

    <template v-else>
      <ul class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Saved content">
        <li v-for="s in items" :key="s.id">
          <Card class="flex h-full flex-col gap-3 p-4">
            <div class="flex items-start justify-between gap-2">
              <button type="button" class="min-w-0 text-left" @click="open(s)"><h2 class="truncate text-base font-semibold hover:underline">{{ s.title }}</h2>
                <p class="line-clamp-2 text-xs text-muted-foreground">{{ s.description }}</p></button>
              <DropdownMenu :items="menu(s)"><Button variant="ghost" size="icon" :aria-label="`Actions for ${s.title}`"><MoreHorizontal /></Button></DropdownMenu>
            </div>
            <p class="text-xs text-muted-foreground">{{ s.learning_level }} · {{ contentTypeLabel(s.content_type) }}</p>
            <div class="flex flex-wrap gap-1"><Badge :tone="sessionStatus(s.status).tone">{{ sessionStatus(s.status).label }}</Badge><Badge v-for="t in s.tags" :key="t">{{ t }}</Badge></div>
            <div class="mt-auto space-y-1"><Progress :value="s.completed_sections" :max="Math.max(1, s.total_sections)" :label="`${s.title} progress`" />
              <p class="text-xs">{{ progressText(s.completed_sections, s.total_sections) }}</p>
              <p class="text-xs text-muted-foreground">Updated {{ relativeTime(s.updated_at) }}<template v-if="s.last_accessed_at"> · opened {{ relativeTime(s.last_accessed_at) }}</template> · created {{ relativeTime(s.created_at) }}</p></div>
            <div class="flex gap-2"><Button size="sm" variant="outline" @click="open(s)"><FolderOpen /> Open</Button><Button v-if="needsResume(s)" size="sm" @click="open(s, true)"><Play /> Continue learning</Button></div>
          </Card>
        </li>
      </ul>
      <nav class="flex items-center justify-between text-sm" aria-label="Pagination">
        <span class="text-muted-foreground" role="status">{{ offset + 1 }}–{{ Math.min(offset + PAGE, total) }} of {{ total }}</span>
        <div class="flex gap-2"><Button size="sm" variant="outline" :disabled="offset === 0" @click="offset = Math.max(0, offset - PAGE)">Previous</Button><Button size="sm" variant="outline" :disabled="offset + PAGE >= total" @click="offset += PAGE">Next</Button></div>
      </nav>
    </template>

    <Dialog v-model:open="renameOpen" title="Rename"><form class="space-y-3" @submit.prevent="newTitle.trim() && rename.mutate()">
      <Input v-model="newTitle" aria-label="Title" maxlength="200" /><div class="flex justify-end gap-2"><Button type="button" variant="ghost" @click="renaming = null">Cancel</Button><Button type="submit" :loading="rename.isPending.value" :disabled="!newTitle.trim()">Save</Button></div></form></Dialog>
    <Dialog v-model:open="deleteOpen" title="Delete this content?" description="This removes the session and all of its sections and versions. This cannot be undone.">
      <p class="text-sm font-medium">{{ deleting?.title }}</p><div class="mt-4 flex justify-end gap-2"><Button variant="ghost" @click="deleting = null">Cancel</Button><Button variant="destructive" :loading="remove.isPending.value" @click="remove.mutate()"><Trash2 /> Delete permanently</Button></div></Dialog>
  </div>
</template>
