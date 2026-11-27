<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { toast } from 'vue-sonner'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import FormField from '@/components/ui/FormField.vue'
import { registerSchema } from '@/schemas/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(); const router = useRouter()
const { defineField, handleSubmit, errors, isSubmitting } = useForm({ validationSchema: toTypedSchema(registerSchema), initialValues: { name: '', email: '', password: '' } })
const [name, nameAttrs] = defineField('name'); const [email, emailAttrs] = defineField('email'); const [password, pwAttrs] = defineField('password')
const formError = ref('')
const submit = handleSubmit(async (v) => {
  formError.value = ''
  try { await auth.register(v); toast.success('Account created'); router.push('/') } catch (e) { formError.value = (e as Error).message }
})
</script>

<template>
  <div class="space-y-6">
    <div><h1 class="text-2xl font-semibold">Create your account</h1><p class="mt-1 text-sm text-muted-foreground">Start building evidence of your skills.</p></div>
    <form class="space-y-4" novalidate @submit.prevent="submit">
      <FormField v-slot="{ id, describedBy, invalid }" label="Full name" :error="errors.name">
        <Input :id="id" v-model="name" v-bind="nameAttrs" autocomplete="name" :aria-invalid="invalid" :aria-describedby="describedBy" />
      </FormField>
      <FormField v-slot="{ id, describedBy, invalid }" label="Email" :error="errors.email">
        <Input :id="id" v-model="email" v-bind="emailAttrs" type="email" autocomplete="email" :aria-invalid="invalid" :aria-describedby="describedBy" />
      </FormField>
      <FormField v-slot="{ id, describedBy, invalid }" label="Password" :error="errors.password">
        <Input :id="id" v-model="password" v-bind="pwAttrs" type="password" autocomplete="new-password" :aria-invalid="invalid" :aria-describedby="describedBy" />
      </FormField>
      <p v-if="formError" role="alert" class="rounded-md bg-destructive/10 p-2.5 text-sm text-destructive">{{ formError }}</p>
      <Button type="submit" class="w-full" :loading="isSubmitting">Create account</Button>
    </form>
    <p class="text-sm text-muted-foreground">Already registered? <RouterLink class="text-primary underline" :to="{ name: 'login' }">Sign in</RouterLink></p>
  </div>
</template>
