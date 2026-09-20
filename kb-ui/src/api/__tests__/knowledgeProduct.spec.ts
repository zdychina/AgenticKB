import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  domain: { currentDomain: 'cloud_core_network' },
  requestInterceptor: undefined as ((config: Record<string, unknown>) => Record<string, unknown>) | undefined,
  requests: [] as Array<{ method: string; url: string; config: Record<string, unknown>; body?: unknown }>,
  responses: {} as Record<string, unknown>,
}))

vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('axios', () => ({
  default: {
    create: () => {
      const dispatch = async (
        method: string, url: string, body?: unknown,
        initial: Record<string, unknown> = {},
      ) => {
        const config = state.requestInterceptor?.({ ...initial, url, method })
          ?? { ...initial, url, method }
        state.requests.push({ method, url, config, body })
        const key = `${method} ${url}`
        if (key in state.responses) return { data: state.responses[key] }
        return { data: {} }
      }
      return {
        interceptors: {
          request: { use: (fn: typeof state.requestInterceptor) => { state.requestInterceptor = fn } },
        },
        get: (url: string, config?: Record<string, unknown>) => dispatch('get', url, undefined, config),
        post: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('post', url, body, config),
        put: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('put', url, body, config),
        patch: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('patch', url, body, config),
        delete: (url: string, config?: Record<string, unknown>) => dispatch('delete', url, undefined, config),
      }
    },
  },
}))

import { useKnowledgeProductApi } from '@/api/knowledgeProduct'

beforeEach(() => {
  state.requests = []
  state.responses = {}
})

describe('knowledge product api client', () => {
  it('lists products from an items envelope', async () => {
    state.responses['get /api/knowledge-products'] = {
      items: [{ id: 'spec-ne8000', name: '规格矩阵' }],
    }
    const rows = await useKnowledgeProductApi().list()
    expect(rows).toHaveLength(1)
    expect(rows[0].id).toBe('spec-ne8000')
  })

  it('encodes object ids that contain spaces and @', async () => {
    const objectId = 'spec-ne8000@DomainFactSet@NE8000-M8 V300R022'
    state.responses[
      `get /api/knowledge-products/spec-ne8000/objects/${encodeURIComponent(objectId)}/md`
    ] = { md: '---\nid: x\n---\n' }

    const md = await useKnowledgeProductApi().getObjectMd('spec-ne8000', objectId)
    expect(md).toContain('id: x')
    // 逻辑 ID 保留空格，URL 里必须编码，否则路由匹配不到
    expect(state.requests[0].url).toContain('NE8000-M8%20V300R022')
  })

  it('omits the revision param when not given', async () => {
    await useKnowledgeProductApi().listObjects('p')
    const params = state.requests[0].config.params as Record<string, unknown>
    expect(params.revision).toBeUndefined()
  })

  it('passes the revision param through when given', async () => {
    await useKnowledgeProductApi().listObjects('p', 3)
    const params = state.requests[0].config.params as Record<string, unknown>
    expect(params.revision).toBe(3)
  })

  it('lets the proxy interceptor inject the current domain', async () => {
    // 制品不跨知识域：请求必须带上当前域，否则会打到别的域的库
    await useKnowledgeProductApi().listObjects('p')
    const params = state.requests[0].config.params as Record<string, unknown>
    expect(params.domain).toBe('cloud_core_network')
  })

  it('sends review decisions as an object map', async () => {
    await useKnowledgeProductApi().review('p', { 'p@T@a': 'approve' }, '看过了')
    expect(state.requests[0].method).toBe('post')
    expect(state.requests[0].body).toEqual({
      decisions: { 'p@T@a': 'approve' }, notes: '看过了',
    })
  })

  it('requests the diff with from/to query params', async () => {
    state.responses['get /api/knowledge-products/p/diff'] = {
      added: [], modified: ['x'], removed: [], total: 1, changes: [],
    }
    const diff = await useKnowledgeProductApi().diff('p', 2, 3)
    const params = state.requests[0].config.params as Record<string, unknown>
    expect(params.from).toBe(2)
    expect(params.to).toBe(3)
    expect(diff.modified).toEqual(['x'])
  })

  it('returns the one-time ticket from starting an instance', async () => {
    state.responses['post /api/knowledge-products/p/creation-instances'] = {
      instance: { id: 'kpi_1' },
      ticket: { ticket_id: 't1', token: 'kpt_secret', expires_at: '2026-09-20T12:00:00Z' },
    }
    const started = await useKnowledgeProductApi().startInstance('p')
    expect(started.ticket.token).toBe('kpt_secret')
    expect(started.instance.id).toBe('kpi_1')
  })

  it('omits ttl when not specified', async () => {
    await useKnowledgeProductApi().startInstance('p')
    expect(state.requests[0].body).toEqual({})
  })

  it('unwraps the publish gate envelope', async () => {
    state.responses['get /api/knowledge-products/p/publish-gate'] = {
      data: { passed: false, checks: [{ code: 'trial_passed', passed: false, detail: '', offenders: [] }] },
    }
    const gate = await useKnowledgeProductApi().publishGate('p')
    expect(gate.passed).toBe(false)
    expect(gate.checks[0].code).toBe('trial_passed')
  })

  it('posts force flag on publish', async () => {
    await useKnowledgeProductApi().publish('p', { force: true })
    expect(state.requests[0].body).toEqual({ force: true })
  })
})

describe('knowledge product ops api (P7)', () => {
  it('surfaces how many tickets a definition change revoked', async () => {
    // 不显示这个数，「为什么 Agent 突然交不进来了」没人说得清
    state.responses['patch /api/knowledge-products/p/definition'] = {
      definition: { definition_revision: 2 }, revoked_tickets: 3,
    }
    const result = await useKnowledgeProductApi().updateDefinition('p', { fields: {} })
    expect(result.revoked_tickets).toBe(3)
    expect(result.definition.definition_revision).toBe(2)
  })

  it('omits untouched definition parts so the backend inherits them', async () => {
    await useKnowledgeProductApi().updateDefinition('p', { fields: { a: {} } })
    expect(state.requests[0].body).toEqual({ fields: { a: {} } })
  })

  it('filters issues by status when asked', async () => {
    await useKnowledgeProductApi().listIssues('p', 'open')
    expect((state.requests[0].config.params as Record<string, unknown>).status).toBe('open')
  })

  it('does not send a status filter when listing everything', async () => {
    await useKnowledgeProductApi().listIssues('p')
    expect((state.requests[0].config.params as Record<string, unknown>).status).toBeUndefined()
  })

  it('sends the resolution direction, not just a done flag', async () => {
    await useKnowledgeProductApi().resolveIssue('p', 'kpis_1', {
      status: 'resolved', resolution_kind: 'definition', resolution_note: '口径写进对象规则',
    })
    expect(state.requests[0].method).toBe('patch')
    expect(state.requests[0].body).toEqual({
      status: 'resolved', resolution_kind: 'definition', resolution_note: '口径写进对象规则',
    })
  })

  it('encodes issue ids in the path', async () => {
    await useKnowledgeProductApi().resolveIssue('p', 'kpis/1', { status: 'rejected' })
    expect(state.requests[0].url).toContain('kpis%2F1')
  })

  it('reads source alerts with their blocking flag', async () => {
    state.responses['get /api/knowledge-products/p/source-alerts'] = {
      alerts: [{ kind: 'revoked', blocking: true, affected_count: 4 }], blocking: true,
    }
    const report = await useKnowledgeProductApi().sourceAlerts('p')
    expect(report.blocking).toBe(true)
    expect(report.alerts[0].kind).toBe('revoked')
  })
})
