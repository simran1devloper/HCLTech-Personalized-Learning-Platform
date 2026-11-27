// Persistent KaTeX validation worker (ADR 0007, Layer 2). One JSON object per line in, one per line out.
//   in : {"id": 1, "tex": "\\frac{a}{b}", "display": true}
//   out: {"id": 1, "ok": true} | {"id": 1, "ok": false, "error": "KaTeX parse error: ..."}
// Usage: node --max-old-space-size=128 tools/katex_worker.mjs /path/to/node_modules/katex
import { createRequire } from 'node:module'
import { createInterface } from 'node:readline'

const katexPath = process.argv[2]
if (!katexPath) { console.error('usage: katex_worker.mjs <path-to-katex>'); process.exit(2) }
const katex = createRequire(import.meta.url)(katexPath)

createInterface({ input: process.stdin }).on('line', (line) => {
  let req
  try { req = JSON.parse(line) } catch { console.log(JSON.stringify({ id: null, ok: false, error: 'bad request' })); return }
  try {
    katex.renderToString(String(req.tex ?? ''), { displayMode: !!req.display, throwOnError: true, strict: 'error', trust: false, maxExpand: 200, maxSize: 20, output: 'html' })
    console.log(JSON.stringify({ id: req.id, ok: true }))
  } catch (e) {
    console.log(JSON.stringify({ id: req.id, ok: false, error: String(e.message).split('\n')[0].slice(0, 200) }))
  }
})
console.log(JSON.stringify({ ready: true, katex: katex.version }))
