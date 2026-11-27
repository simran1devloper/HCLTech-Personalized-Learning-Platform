<script setup lang="ts">
import { computed } from 'vue'
import { Loader2 } from 'lucide-vue-next'
import { cn } from '@/utils/cn'
import { buttonVariants, type ButtonVariants } from './button-variants'

const props = withDefaults(defineProps<{ variant?: ButtonVariants['variant']; size?: ButtonVariants['size']; loading?: boolean; type?: 'button' | 'submit'; disabled?: boolean; class?: string }>(), { type: 'button' })
const cls = computed(() => cn(buttonVariants({ variant: props.variant, size: props.size }), props.class))
</script>
<template>
  <button :type="type" :class="cls" :disabled="disabled || loading" :aria-busy="loading || undefined">
    <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
    <slot />
  </button>
</template>
