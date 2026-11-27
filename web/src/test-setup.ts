import { afterEach, vi } from 'vitest'
// jsdom lacks these browser APIs used by the app
class RO { observe() {} unobserve() {} disconnect() {} }
;(globalThis as any).ResizeObserver ||= RO
;(globalThis as any).matchMedia ||= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })) as any
Element.prototype.scrollTo ||= (() => {}) as any
afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })
