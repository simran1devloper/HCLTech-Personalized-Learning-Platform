import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parseBlocks, SCHEMA_VERSION, V2_TYPES } from './blocks'

// Shared with backend/tests/unit/test_block_vectors.py: both schemas must agree on every case.
const doc = JSON.parse(readFileSync(resolve(__dirname, '../../../contracts/block-vectors.json'), 'utf8')) as { schema_version: string; vectors: { name: string; valid: boolean; block: Record<string, unknown> }[] }

describe('TypeScript schema agrees with the shared contract vectors', () => {
  it('uses the same schema version and covers all 15 block types', () => {
    expect(doc.schema_version).toBe(SCHEMA_VERSION)
    const validTypes = new Set(doc.vectors.filter((v) => v.valid).map((v) => v.block.type))
    for (const t of V2_TYPES) expect(validTypes.has(t), `no valid vector for ${t}`).toBe(true)
  })
  it.each(doc.vectors.map((v) => [v.name, v] as const))('%s', (_n, v) => {
    const r = parseBlocks([v.block])
    expect(r.blocks.length === 1, `${v.name}: ${JSON.stringify(r.rejected)}`).toBe(v.valid)
  })
})
