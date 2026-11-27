import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('formal product routes', () => {
  it('exposes the unified retrieval records page', () => {
    const route = router.getRoutes().find(item => item.name === 'retrieval-records')
    expect(route?.path).toBe('/retrieval-records')
  })

  it('does not expose research entity, ontology, or graph pages', () => {
    const names = new Set(router.getRoutes().map(route => String(route.name ?? '')))
    for (const retired of [
      'graph', 'entities', 'ontology', 'ontology-graph',
      'ontology-review', 'mentions-review',
    ]) {
      expect(names.has(retired)).toBe(false)
    }
  })

  // BUG-09：未知路由要有 404 兜底，且挂在布局内（侧边栏可用），不能白屏
  it('catches unknown paths with a 404 route inside the layout', () => {
    const route = router.getRoutes().find(item => item.name === 'not-found')
    expect(route?.path).toBe('/:pathMatch(.*)*')
    const resolved = router.resolve('/nope')
    expect(resolved.name).toBe('not-found')
    // matched 链以 / 布局父路由开头 → 外层 AppLayout 仍渲染
    expect(resolved.matched.map(item => item.path)).toEqual(['/', '/:pathMatch(.*)*'])
  })

  it('still resolves named and resource routes ahead of the catch-all', () => {
    expect(router.resolve('/nope').name).toBe('not-found')
    expect(router.resolve('/kb/does-not-exist').name).toBe('kb-detail')
    expect(router.resolve('/kb/a/doc/b').name).toBe('kb-doc-preview')
    expect(router.resolve('/login').name).toBe('login')
  })
})
