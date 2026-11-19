import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: { listArtifactRefs: vi.fn() },
  router: { push: vi.fn() },
}))

vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))

import ArtifactRefsChips from '../ArtifactRefsChips.vue'
import type { ArtifactObjectRef } from '@/types/ontology'

interface ChipsVm {
  items: ArtifactObjectRef[]
  truncated: boolean
  jump: (ref: ArtifactObjectRef) => void
}

function makeRef(overrides: Partial<ArtifactObjectRef>): ArtifactObjectRef {
  return {
    id: 'sor_1', object_id: 'obj-amf', target_key: '', note: '',
    ref_source: 'manual', created_by: 'u1',
    created_at: '2026-11-19T08:00:00+00:00',
    object_name: 'AMF网元', ontology_name: '网元',
    library_id: 'lib-1', library_domain: 'cloud_core_network',
    degraded: null,
    ...overrides,
  }
}

function mountChips(targetKey: string): VueWrapper {
  return shallowMount(ArtifactRefsChips, {
    props: { artifactId: 'art-1', targetKey },
  })
}

beforeEach(() => {
  state.api.listArtifactRefs.mockReset().mockResolvedValue({ items: [], truncated: false })
  state.router.push.mockReset()
})

describe('ArtifactRefsChips — 制品侧反查 chips（70号 §六）', () => {
  it('按 artifact_id + target_key 反查并渲染对象名 chip', async () => {
    state.api.listArtifactRefs.mockResolvedValue({
      items: [
        makeRef({ id: 'sor_1', object_name: 'AMF网元', ontology_name: '网元' }),
        makeRef({ id: 'sor_2', object_id: 'obj-smf', object_name: 'SMF网元', ontology_name: '网元' }),
      ],
      truncated: false,
    })
    const wrapper = mountChips('cmd-amf-001')
    await flushPromises()

    expect(state.api.listArtifactRefs).toHaveBeenCalledWith('art-1', 'cmd-amf-001', 20)
    expect(wrapper.findAll('[data-test="artifact-refs-chip"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('AMF网元')
    expect(wrapper.text()).toContain('SMF网元')
  })

  it('target_key=""（制品整体级）显式下发——不能按 falsy 丢弃', async () => {
    mountChips('')
    await flushPromises()

    expect(state.api.listArtifactRefs).toHaveBeenCalledWith('art-1', '', 20)
  })

  it('chip 点击跳库图谱聚焦（69号 focus 链路复用）', async () => {
    state.api.listArtifactRefs.mockResolvedValue({
      items: [makeRef({ id: 'sor_1', object_id: 'obj-amf', library_id: 'lib-1' })],
      truncated: false,
    })
    const wrapper = mountChips('amf-init')
    await flushPromises()

    await (wrapper.vm as unknown as ChipsVm).jump(
      (wrapper.vm as unknown as ChipsVm).items[0])
    expect(state.router.push).toHaveBeenCalledWith({
      name: 'ontology-library-detail',
      params: { libraryId: 'lib-1' },
      query: { tab: 'graph', focus: 'obj-amf' },
    })
  })

  it('object_invisible（防御占位）：灰显「对象不可见」且不跳转', async () => {
    state.api.listArtifactRefs.mockResolvedValue({
      items: [makeRef({ id: 'sor_inv', object_name: null, ontology_name: null })],
      truncated: false,
    })
    const wrapper = mountChips('cmd-amf-001')
    await flushPromises()

    expect(wrapper.find('[data-test="artifact-refs-invisible"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('对象不可见')
    expect(state.router.push).not.toHaveBeenCalled()
  })

  it('无引用时整行隐藏（增强信息不留空白占位）', async () => {
    const wrapper = mountChips('cmd-none')
    await flushPromises()

    expect(wrapper.find('[data-test="artifact-refs"]').exists()).toBe(false)
  })

  it('反查失败静默隐藏（不弹错不打断宿主面板）', async () => {
    state.api.listArtifactRefs.mockRejectedValue(new Error('boom'))
    const wrapper = mountChips('cmd-amf-001')
    await flushPromises()

    expect(wrapper.find('[data-test="artifact-refs"]').exists()).toBe(false)
  })

  it('truncated 显示截断提示', async () => {
    state.api.listArtifactRefs.mockResolvedValue({
      items: [makeRef({ id: 'sor_1' })],
      truncated: true,
    })
    const wrapper = mountChips('cmd-amf-001')
    await flushPromises()

    expect(wrapper.text()).toContain('等 1+ 个')
  })
})
