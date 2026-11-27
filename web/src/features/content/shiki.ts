import type { HighlighterCore } from 'shiki/core'

const LANGS: Record<string, () => Promise<any>> = {
  python: () => import('shiki/langs/python.mjs'), py: () => import('shiki/langs/python.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'), js: () => import('shiki/langs/javascript.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'), ts: () => import('shiki/langs/typescript.mjs'),
  json: () => import('shiki/langs/json.mjs'), bash: () => import('shiki/langs/bash.mjs'), sh: () => import('shiki/langs/bash.mjs'),
  sql: () => import('shiki/langs/sql.mjs'), html: () => import('shiki/langs/html.mjs'), css: () => import('shiki/langs/css.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'), java: () => import('shiki/langs/java.mjs'), go: () => import('shiki/langs/go.mjs'),
  rust: () => import('shiki/langs/rust.mjs'), cpp: () => import('shiki/langs/cpp.mjs'), c: () => import('shiki/langs/c.mjs'),
  dockerfile: () => import('shiki/langs/dockerfile.mjs'), markdown: () => import('shiki/langs/markdown.mjs'),
}
const canonical = (l: string) => ({ py: 'python', js: 'javascript', ts: 'typescript', sh: 'bash' } as Record<string, string>)[l] ?? l
let hl: Promise<HighlighterCore> | null = null
const cache = new Map<string, string>()

function get(): Promise<HighlighterCore> {
  return (hl ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([import('shiki/core'), import('shiki/engine/javascript')])
    return createHighlighterCore({
      themes: [import('shiki/themes/github-light-high-contrast.mjs'), import('shiki/themes/github-dark-high-contrast.mjs')], langs: [],
      engine: createJavaScriptRegexEngine(), // no WASM payload
    })
  })())
}

/** Returns highlighted HTML (shiki escapes all source text), or null for unknown languages (caller shows plain text). */
export async function highlight(code: string, lang: string): Promise<string | null> {
  const l = canonical(lang.toLowerCase())
  if (!LANGS[l]) return null
  const key = `${l}\0${code}`
  const hit = cache.get(key); if (hit) return hit
  const h = await get()
  if (!h.getLoadedLanguages().includes(l)) await h.loadLanguage(await LANGS[l]())
  const html = h.codeToHtml(code, { lang: l, themes: { light: 'github-light-high-contrast', dark: 'github-dark-high-contrast' }, defaultColor: false })
  if (cache.size > 200) cache.delete(cache.keys().next().value!)
  cache.set(key, html)
  return html
}
