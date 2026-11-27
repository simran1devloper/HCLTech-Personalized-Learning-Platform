import { z } from 'zod'

export const loginSchema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})
// Mirrors backend RegisterRequest (name 2–80 chars of letters/space/-'. ; password 8–128).
export const registerSchema = z.object({
  name: z.string().trim().min(2, 'Name must be at least 2 characters').max(80, 'Name is too long').regex(/^[\p{L}\p{N}_\s\-'.]+$/u, 'Name contains invalid characters'),
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(8, 'Use at least 8 characters').max(128, 'Password is too long'),
})
export type LoginForm = z.infer<typeof loginSchema>
export type RegisterForm = z.infer<typeof registerSchema>
