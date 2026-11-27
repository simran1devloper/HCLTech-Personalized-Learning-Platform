// Minimal Ollama-compatible server for deterministic E2E runs (/api/chat streaming + /api/tags).
import http from 'node:http'
const port = Number(process.env.PORT || 11999)
const ANSWER = [
  'Here is **async/await** in Python:\n\n', '```python\nimport asyncio\n\nasync def main():\n    await asyncio.sleep(1)\n```\n\n',
  '```mermaid\nflowchart TD\n  A[Start] --> B[Await]\n```\n\n', 'Energy: $E=mc^2$ and <script>window.__xss=1</script>\n',
]
const flakyCount = new Map()                                 // per topic: FLAKY topics fail their flashcards section on the first two attempts, independently of other tests
const badSeen = new Set()                                   // topics whose FIRST question generation is deliberately unusable
const produced = new Map()                                 // per topic: how many questions were produced so far, so batches never repeat a question
let generationCalls = 0                                    // exposed at /__stats so E2E can assert how many times the model was really used
const topicOf = (text) => (text.match(/Topic: (.+)/) || [])[1] || 'unknown'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url.startsWith('/__stats')) { res.setHeader('content-type', 'application/json'); return res.end(JSON.stringify({ questionGenerations: generationCalls })) }
  if (req.method === 'GET' && req.url.startsWith('/api/tags')) { res.setHeader('content-type', 'application/json'); return res.end(JSON.stringify({ models: [{ name: 'fake' }] })) }
  if (req.method === 'POST' && req.url.startsWith('/api/chat')) {
    let body = ''; for await (const c of req) body += c
    const j = JSON.parse(body); const last = j.messages.at(-1).content
    if (last.includes('FAILNOW')) { res.statusCode = 503; return res.end('{}') }
    // Study-guide sections are requested with stream:true by the backend (silence-based timeouts); the tutor chat also streams.
    // Section/question prompts are answered with generated content in BOTH modes; any other streamed prompt is a tutor chat.
    const wantsContent = !j.stream || /SECTION: \w+|Write exactly \d+ questions/.test(last)
    if (wantsContent) {
      res.setHeader('content-type', j.stream ? 'application/x-ndjson' : 'application/json')
      const reply = async (content) => {
        if (!j.stream) return res.end(JSON.stringify({ message: { content }, prompt_eval_count: 5, eval_count: 3, done: true, done_reason: 'stop' }))
        for (const w of content.match(/.{1,6}/gs) ?? ['']) { if (res.destroyed) return; res.write(JSON.stringify({ message: { content: w }, done: false }) + '\n'); await sleep(2) }
        res.end(JSON.stringify({ message: { content: '' }, done: true, done_reason: 'stop', prompt_eval_count: 5, eval_count: 3 }) + '\n')
      }
      const gq = last.match(/Write exactly (\d+) questions/)
      if (gq) {                                                              // Diagnostic Study / Assessments question-bank top-up
        generationCalls++; await sleep(700)
        const topic = (last.match(/questions about (.+?) for software engineers/) || [])[1] || 'topic'
        if (topic.includes('BADQUIZ') && !badSeen.has(topic)) { badSeen.add(topic); return reply(JSON.stringify([{ concept: 'x', question_text: 'A broken question with no usable options at all?', options: ['only one'], correct: [3] }])) }
        const offset = produced.get(topic) || 0; produced.set(topic, offset + Number(gq[1]))
        return reply(JSON.stringify({ questions: Array.from({ length: Number(gq[1]) }, (_, k) => k + offset).map((i) => ({ concept: `Area ${(i % 3) + 1}`, difficulty: [1, 2, 3, 4, 5][i % 5], question_type: 'single_choice', cognitive_level: 'apply',
          question_text: `Fake question ${i + 1} about ${topic}: which statement is accurate?`, code_snippet: null, options: ['Alpha', 'Beta', 'Gamma', 'Delta'], correct: [1], explanation: `Beta is the accurate statement for question ${i + 1}.` })) }))
      }
      if (last.includes('Generate exactly 10 questions')) {
        await sleep(600)                                                    // long enough for the UI to show live pipeline state
        // "BADQUIZ" topics: the first attempt is unusable, so the reviewer must reject it and trigger one revision
        if (last.includes('BADQUIZ') && !last.includes('previous attempt was rejected')) return reply(JSON.stringify({ questions: [{ question: 'broken', options: ['x'], correct_answer: 'x' }] }))
      }
      if (last.includes('Generate exactly 10 questions')) return reply(JSON.stringify({ questions: Array.from({ length: 10 }, (_, i) => ({ question: `Fake question ${i + 1}?`, options: ['Alpha', 'Beta', 'Gamma', 'Delta'], correct_answer: 'Beta' })) }))
      const m = last.match(/SECTION: (\w+)/)
      if (m) {
        const name = m[1]
        // "FLAKY" topics fail the flashcards section on its first two attempts (one full generation), then succeed
        if (last.includes('FLAKY') && name === 'flashcards' && (flakyCount.set(topicOf(last), (flakyCount.get(topicOf(last)) ?? 0) + 1), flakyCount.get(topicOf(last))) <= 2) { res.statusCode = 503; return res.end('{}') }
        await sleep(40)
        if (name === 'key_concepts') return reply(JSON.stringify({ key_concepts: [1, 2, 3, 4].map((i) => ({ term: `Concept ${i}`, definition: `Definition ${i}.` })) }))
        if (name === 'flashcards') return reply(JSON.stringify({ flashcards: [1, 2, 3, 4].map((i) => ({ question: `Card question ${i}?`, answer: `Card answer ${i}.` })) }))
        if (name === 'recommendations') return reply(JSON.stringify({ weak_areas: ['Weak area one'], next_topics: [{ title: 'Next topic', reason: 'Builds on this.' }], practice_suggestions: ['Practice daily'], resources: [] }))
        if (name === 'agent_intro') return reply('Hi! Welcome to your study session, ask me anything.')
        return reply(`## ${name} heading\n\nGenerated ${name} content that is long enough to be accepted by validation.\n\n\`\`\`python\nprint("${name}")\n\`\`\`\n`)
      }
      return reply('Short narration.')
    }
    res.setHeader('content-type', 'application/x-ndjson')
    const slow = last.includes('SLOWPLEASE')
    for (const part of ANSWER) {
      for (const w of part.match(/.{1,6}/gs)) { if (res.destroyed) return; res.write(JSON.stringify({ message: { content: w }, done: false }) + '\n'); await sleep(slow ? 250 : 4) }
    }
    res.end(JSON.stringify({ message: { content: '' }, done: true, prompt_eval_count: 10, eval_count: 20 }) + '\n')
  } else { res.statusCode = 404; res.end() }
}).listen(port, '127.0.0.1', () => console.log('fake ollama on', port))
