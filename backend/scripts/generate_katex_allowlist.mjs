// Generates backend/app/domains/content_engine/data/latex_allowlist.json from the INSTALLED KaTeX.
// Self-verifying: every candidate command found in the KaTeX bundle is probed against KaTeX itself, and kept only if
// KaTeX recognises it (does not report "Undefined control sequence").
//   node scripts/generate_katex_allowlist.mjs            # write
//   node scripts/generate_katex_allowlist.mjs --check    # exit 1 if the committed file is stale (used in CI)
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const katexDir = process.env.KATEX_MODULE_PATH || resolve(here, '../../web/node_modules/katex')
const require = createRequire(import.meta.url)
const katex = require(katexDir)
const out = resolve(here, '../app/domains/content_engine/data/latex_allowlist.json')

const bundle = readFileSync(resolve(katexDir, 'dist/katex.mjs'), 'utf8')
const candidates = new Set()
for (const m of bundle.matchAll(/"\\\\([A-Za-z@]+)"/g)) candidates.add(m[1])
for (const m of bundle.matchAll(/'\\\\([A-Za-z@]+)'/g)) candidates.add(m[1])

const recognised = (name) => {
  try { katex.renderToString(`\\${name}`, { throwOnError: true, strict: 'ignore', trust: false }); return true }
  catch (e) { return !/Undefined control sequence/.test(String(e.message)) }
}
// debug/side-effect macros are never probed (and are forbidden by policy): \show logs to the console
const SKIP = new Set(['show', 'message', 'errmessage'])
const commands = [...candidates].filter((c) => !c.includes('@') && !SKIP.has(c) && recognised(c)).sort()
const envCandidates = ['matrix','pmatrix','bmatrix','Bmatrix','vmatrix','Vmatrix','smallmatrix','array','subarray','cases','dcases','rcases','aligned','alignedat','align','align*','gather','gathered','equation','equation*','split','CD']
const environments = envCandidates.filter((e) => { try { katex.renderToString(`\\begin{${e}}a\\end{${e}}`, { throwOnError: true, strict: 'ignore' }); return true } catch (x) { return !/No such environment/.test(String(x.message)) } })

const doc = { generated_by: 'scripts/generate_katex_allowlist.mjs', katex_version: katex.version, commands, environments }
const text = JSON.stringify(doc, null, 1) + '\n'
if (process.argv.includes('--check')) {
  let current = ''
  try { current = readFileSync(out, 'utf8') } catch { /* missing */ }
  if (current !== text) { console.error(`latex_allowlist.json is stale for KaTeX ${katex.version}; run: node scripts/generate_katex_allowlist.mjs`); process.exit(1) }
  console.log(`allow-list is up to date (KaTeX ${katex.version}, ${commands.length} commands)`)
} else {
  writeFileSync(out, text)
  console.log(`wrote ${out}: KaTeX ${katex.version}, ${commands.length} commands, ${environments.length} environments`)
}
