<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { toast } from 'vue-sonner'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import FormField from '@/components/ui/FormField.vue'
import { loginSchema } from '@/schemas/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(); const router = useRouter(); const route = useRoute()
const { defineField, handleSubmit, errors, isSubmitting } = useForm({ validationSchema: toTypedSchema(loginSchema), initialValues: { email: '', password: '' } })
const [email, emailAttrs] = defineField('email'); const [password, pwAttrs] = defineField('password')
const formError = ref('')

const submit = handleSubmit(async (v) => {
  formError.value = ''
  try {
    await auth.login(v)
    toast.success(`Welcome back, ${auth.user?.name.split(' ')[0]}!`)
    const to = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/'
    router.push(to)
  } catch (e) { formError.value = (e as Error).message }
})
</script>

<template>
  <div class="space-y-6">
    <div><h1 class="text-2xl font-semibold">Sign in</h1><p class="mt-1 text-sm text-muted-foreground">Continue your learning journey.</p></div>
    <form class="space-y-4" novalidate @submit.prevent="submit">
      <FormField v-slot="{ id, describedBy, invalid }" label="Email" :error="errors.email">
        <Input :id="id" v-model="email" v-bind="emailAttrs" type="email" autocomplete="email" :aria-invalid="invalid" :aria-describedby="describedBy" />
      </FormField>
      <FormField v-slot="{ id, describedBy, invalid }" label="Password" :error="errors.password">
        <Input :id="id" v-model="password" v-bind="pwAttrs" type="password" autocomplete="current-password" :aria-invalid="invalid" :aria-describedby="describedBy" />
      </FormField>
      <p v-if="formError" role="alert" class="rounded-md bg-destructive/10 p-2.5 text-sm text-destructive">{{ formError }}</p>
      <Button type="submit" class="w-full" :loading="isSubmitting">Sign in</Button>
    </form>
    <p class="text-sm text-muted-foreground">New here? <RouterLink class="text-primary underline" :to="{ name: 'register' }">Create an account</RouterLink></p>
  </div>
</template>
