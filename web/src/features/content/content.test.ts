import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import BlockRenderer from './BlockRenderer.vue'
import FlashcardDeck from './FlashcardDeck.vue'
import QuestionInput from '@/features/assessment/QuestionInput.vue'
import type { AssessmentQuestion } from '@/types/api'

describe('BlockRenderer', () => {
  it('renders valid blocks and hides invalid ones with a notice', () => {
    const w = mount(BlockRenderer, { props: { blocks: [{ type: 'text', markdown: '# Title\n\nHello' }, { type: 'table', headers: ['A', 'B'], rows: [['1', '2']] }, { type: 'iframe', src: 'x' }, { type: 'exercise', prompt: 'Do it', hints: ['h1'], solution_outline: 'SECRET-OUTLINE' }] }, global: { stubs: { RouterLink: true } } })
    expect(w.text()).toContain('Title'); expect(w.find('table th').text()).toBe('A'); expect(w.text()).toContain('1 content block(s) were hidden')
    expect(w.text()).not.toContain('SECRET-OUTLINE'); expect(w.text()).toContain('Hint 1')
  })
  it('never injects HTML or scripts from generated text, table cells or exercises', () => {
    const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'
    const w = mount(BlockRenderer, { props: { blocks: [{ type: 'text', markdown: evil }, { type: 'table', headers: [evil], rows: [[evil]] }, { type: 'case_study', title: evil, scenario: evil, questions: [evil] }, { type: 'tutorial', steps: [evil] }] }, global: { stubs: { RouterLink: true } } })
    expect(w.element.querySelector('script')).toBeNull(); expect(w.element.querySelector('img')).toBeNull(); expect((window as any).__pwned).toBeUndefined()
  })
})

describe('FlashcardDeck', () => {
  it('flips and wraps around with keyboard-accessible controls', async () => {
    const w = mount(FlashcardDeck, { props: { cards: [{ front: 'Q1', back: 'A1' }, { front: 'Q2', back: 'A2' }] } })
    expect(w.text()).toContain('Q1'); await w.find('button[aria-label^="Question"]').trigger('click'); expect(w.text()).toContain('A1')
    await w.find('button[aria-label="Next card"]').trigger('click'); expect(w.text()).toContain('Q2')
    await w.find('button[aria-label="Next card"]').trigger('click'); expect(w.text()).toContain('Q1')
  })
})

const q = (type: AssessmentQuestion['type'], options: string[] = []): AssessmentQuestion => ({ question_id: 'q', type, prompt: 'Pick', options, difficulty: 2, objective: '' })
describe('QuestionInput', () => {
  it('single choice emits the option index', async () => {
    const w = mount(QuestionInput, { props: { q: q('multiple_choice', ['a', 'b']), modelValue: undefined } })
    await w.findAll('input[type=radio]')[1].setValue(true); expect(w.emitted('update:modelValue')![0]).toEqual([1])
  })
  it('true/false renders two options', () => { expect(mount(QuestionInput, { props: { q: q('true_false', ['True', 'False']), modelValue: 0 } }).findAll('input[type=radio]')).toHaveLength(2) })
  it('multiple select emits a sorted list of indices', async () => {
    const w = mount(QuestionInput, { props: { q: q('multiple_select', ['a', 'b', 'c']), modelValue: [2] } })
    await w.findAll('input[type=checkbox]')[0].setValue(true); expect(w.emitted('update:modelValue')![0]).toEqual([[0, 2]])
  })
  it.each(['short_answer', 'scenario', 'system_design', 'practical'] as const)('%s uses a labelled textarea', async (t) => {
    const w = mount(QuestionInput, { props: { q: q(t), modelValue: '' } })
    const ta = w.find('textarea'); expect(ta.attributes('aria-label')).toBe('Pick'); await ta.setValue('my answer'); expect(w.emitted('update:modelValue')![0]).toEqual(['my answer'])
  })
  it('disables inputs when read-only', () => { expect(mount(QuestionInput, { props: { q: q('multiple_choice', ['a']), modelValue: 0, disabled: true } }).find('fieldset').attributes('disabled')).toBeDefined() })
})
