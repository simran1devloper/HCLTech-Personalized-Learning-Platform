<script setup lang="ts">
import { ref, watch } from 'vue'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import Dialog from '@/components/ui/Dialog.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import { careerApi } from '@/services/api'
import { useRoles } from '@/composables/queries'

const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ saved: [slug: string] }>()
const qc = useQueryClient()
const roles = useRoles()
const slug = ref(''); const hours = ref('5')
watch(() => roles.data.value, (r) => { if (r?.length && !slug.value) slug.value = r[0].slug }, { immediate: true })
const hoursNum = () => Math.min(80, Math.max(1, Number(hours.value) || 5))
const save = useMutation({
  mutationFn: () => careerApi.createGoal({ role_slug: slug.value, hours_per_week: hoursNum() }),
  onSuccess: (g) => { qc.invalidateQueries({ queryKey: ['goals'] }); qc.invalidateQueries({ queryKey: ['analytics'] }); toast.success('Career goal saved'); open.value = false; emit('saved', g.role_slug) },
  onError: (e) => toast.error((e as Error).message),
})
</script>
<template>
  <Dialog v-model:open="open" title="Set your career goal" description="Your roadmap and skill gaps are calculated against this role's requirements.">
    <Skeleton v-if="roles.isPending.value" class="h-24" />
    <ErrorState v-else-if="roles.isError.value" :message="(roles.error.value as Error).message" @retry="roles.refetch()" />
    <form v-else class="space-y-4" @submit.prevent="save.mutate()">
      <fieldset class="max-h-[40vh] space-y-2 overflow-y-auto p-0.5"><legend class="mb-1 text-sm font-medium">Target role</legend>
        <label v-for="r in roles.data.value" :key="r.slug" class="flex cursor-pointer items-center gap-2 rounded-md border p-2.5 text-sm has-[:checked]:border-primary has-[:checked]:bg-accent">
          <input v-model="slug" type="radio" name="role" :value="r.slug" class="accent-[var(--primary)]" />{{ r.title }}<span class="ml-auto text-xs text-muted-foreground">{{ r.requirements.length }} skills</span>
        </label>
      </fieldset>
      <div class="space-y-1.5"><label for="hpw" class="text-sm font-medium">Hours per week</label><Input id="hpw" v-model="hours" type="number" min="1" max="80" inputmode="numeric" /></div>
      <div class="flex justify-end gap-2"><Button variant="ghost" @click="open = false">Cancel</Button><Button type="submit" :loading="save.isPending.value">Save goal</Button></div>
    </form>
  </Dialog>
</template>
