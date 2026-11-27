/// <reference types="vite/client" />
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}
interface ImportMetaEnv { readonly VITE_API_BASE_URL?: string }
declare module 'monaco-editor/editor/editor.api' { export * from 'monaco-editor' }
declare module 'monaco-editor/languages/definitions/python/register'
declare module 'monaco-editor/editor/editor.worker?worker' { const W: new () => Worker; export default W }
