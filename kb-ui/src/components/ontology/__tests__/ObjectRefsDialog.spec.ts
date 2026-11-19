import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  ontology: {
    getLibrary: vi.fn(),
    listObjectRefs: vi.fn(),
    createObjectRef: vi.fn(),
    deleteObjectRef: vi.fn(),
  },
  artifacts: {
    list: vi.fn(),
    listRows: vi.fn(),
    listWikiPages: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  router: { push: vi.fn() },
}))

vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.ontology }))
vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => state.artifacts }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => 'mock-error' }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: {
    success: state.ui.success, error: state.ui.error, warning: state.ui.warning,
  },
}))

import ObjectRefsDialog from '../ObjectRefsDialog.vue'
import type { ObjectArtifactRef } from '@/types/ontology'

interface RefsVm {
  items: ObjectArtifactRef[]
  truncated: boolean
  createVisible: boolean
  saving: boolean
  form: { artifactId: string; targetKey: string; note: string }
  artifacts: Array<{ artifact_id: string; kind: string; current_revision: number }>
  targetOptions: Array<{ value: string; label: string }>
  selectedArtifact: { artifact_id: string; kind: string; current_revision: number } | null
  reload: () => Promise<void>
  openCreate: () => Promise<void>
  onArtifactChange: (artifactId: string) => Promise<void>
  searchRows: (query: string) => Promise<void>
  submit: () => Promise<void>
  removeRef: (ref: ObjectArtifactRef) => Promise<void>
  openArtifact: (ref: ObjectArtifactRef) => void
  targetLabel: (ref: ObjectArtifactRef) => string
}

function makeRef(overrides: Partial<ObjectArtifactRef>): ObjectArtifactRef {
  return {
    id: 'sor_1', object_id: 'obj-1', artifact_id: 'art-1', target_key: '',
    note: '', ref_source: 'manual', created_by: 'u1',
    created_at: '2026-11-19T08:00:00+00:00',
    artifact_title: '核心网 MOP 表', kind: 'table', artifact_status: 'published',
    artifact_domain_id: 'cloud_core_network', current_revision: 2,
    target_title: null, degraded: null,
    ...overrides,
  }
}

function mountDialog(canWrite: boolean, modelValue = true): VueWrapper {
  return shallowMount(ObjectRefsDialog, {
    props: {
      modelValue,
      objectId: 'obj-1',
      objectName: 'AMF网元',
      libraryId: 'lib-1',
      canWrite,
    },
  })
}

beforeEach(() => {
  state.ontology.getLibrary.mockReset().mockResolvedValue({
    id: 'lib-1', domain: 'cloud_core_network', name: '核心网本体库',
  })
  state.ontology.listObjectRefs.mockReset().mockResolvedValue({ items: [], truncated: false })
  state.ontology.createObjectRef.mockReset().mockResolvedValue(makeRef({}))
  state.ontology.deleteObjectRef.mockReset().mockResolvedValue(undefined)
  state.artifacts.list.mockReset().mockResolvedValue([])
  state.artifacts.listRows.mockReset().mockResolvedValue({ rows: [], total: 0, page: 1, size: 50 })
  state.artifacts.listWikiPages.mockReset().mockResolvedValue({ revision: 0, pages: [] })
  state.ui.confirm.mockReset().mockResolvedValue('confirm')
  state.ui.success.mockReset()
  state.ui.error.mockReset()
  state.ui.warning.mockReset()
  state.router.push.mockReset()
})

describe('ObjectRefsDialog — 列表加载与降级渲染（70号）', () => {
  it('打开即按对象拉引用列表', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [makeRef({})], truncated: false,
    })
    const wrapper = mountDialog(true)
    await flushPromises()

    expect(state.ontology.listObjectRefs).toHaveBeenCalledWith('obj-1', 200)
    const vm = wrapper.vm as unknown as RefsVm
    expect(vm.items).toHaveLength(1)
    // 整体级引用：目标显示「制品整体」
    expect(vm.targetLabel(vm.items[0])).toBe('制品整体')
    expect(wrapper.text()).toContain('核心网 MOP 表')
    expect(wrapper.text()).toContain('制品整体')
  })

  it('degraded 条目灰显「目标已不存在」并保留可删（§五：页/行已被移除）', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [
        makeRef({ id: 'sor_page', artifact_id: 'art-wiki', kind: 'wiki', target_key: '故障处理', target_title: null, degraded: 'target_missing' }),
        makeRef({ id: 'sor_ok', kind: 'wiki', target_key: '开局指导', target_title: 'AMF 开局指导', degraded: null }),
      ],
      truncated: false,
    })
    const wrapper = mountDialog(true)
    await flushPromises()

    const rows = wrapper.findAll('[data-test="ref-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].classes()).toContain('is-degraded')
    expect(rows[0].text()).toContain('目标已不存在')
    // 降级显示 target_key 原文（title 已无从解析）
    expect(rows[0].text()).toContain('故障处理')
    expect(rows[1].classes()).not.toContain('is-degraded')
    expect(rows[1].text()).toContain('AMF 开局指导')
    // 状态徽标：deprecated=已停服
    expect(rows[0].text()).toContain('已发布')
  })

  it('truncated 时提示截断', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: Array.from({ length: 5 }, (_, i) => makeRef({ id: `sor_${i}` })),
      truncated: true,
    })
    const wrapper = mountDialog(true)
    await flushPromises()

    expect(wrapper.text()).toContain('仅显示前 5 条')
  })
})

describe('ObjectRefsDialog — 权限显隐（canWrite=只读域成员）', () => {
  it('只读面：无「新建引用」入口、无删除按钮', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [makeRef({})], truncated: false,
    })
    const wrapper = mountDialog(false)
    await flushPromises()

    expect(wrapper.find('[data-test="open-ref-create"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('删除')
  })

  it('写者面：有新建入口与删除按钮', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [makeRef({})], truncated: false,
    })
    const wrapper = mountDialog(true)
    await flushPromises()

    expect(wrapper.find('[data-test="open-ref-create"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('删除')
  })
})

describe('ObjectRefsDialog — 新建流（同域制品 → 目标 → 备注）', () => {
  it('新建入口按库域过滤制品（跨域必败选项不出现在选择器里）', async () => {
    state.artifacts.list.mockResolvedValue([
      { artifact_id: 'art-t1', kind: 'table', current_revision: 2 },
      { artifact_id: 'art-w1', kind: 'wiki', current_revision: 1 },
    ])
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.openCreate()
    await flushPromises()

    expect(state.ontology.getLibrary).toHaveBeenCalledWith('lib-1')
    expect(state.artifacts.list).toHaveBeenCalledWith({ domain_id: 'cloud_core_network' })
    expect(vm.artifacts.map(a => a.artifact_id)).toEqual(['art-t1', 'art-w1'])
    expect(vm.createVisible).toBe(true)
  })

  it('选表格制品：行搜索走 current_revision 口径 + 关键字（评审⑦）', async () => {
    state.artifacts.list.mockResolvedValue([
      { artifact_id: 'art-t1', kind: 'table', current_revision: 2 },
    ])
    state.artifacts.listRows.mockResolvedValue({
      rows: [{ row_key: 'cmd-amf-001', data: {}, provenance: null, conflicts: null, ordinal: 1 }],
      total: 1, page: 1, size: 50,
    })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.openCreate()
    await flushPromises()
    vm.form.artifactId = 'art-t1'
    await vm.onArtifactChange('art-t1')
    await vm.searchRows('amf')

    expect(state.artifacts.listRows).toHaveBeenCalledWith('art-t1', {
      revision: 2, q: 'amf', size: 50,
    })
    expect(vm.targetOptions).toEqual([{ value: 'cmd-amf-001', label: 'cmd-amf-001' }])
  })

  it('选 wiki 制品：页清单读 current revision，label 取 title 回落 page_key', async () => {
    state.artifacts.list.mockResolvedValue([
      { artifact_id: 'art-w1', kind: 'wiki', current_revision: 3 },
    ])
    state.artifacts.listWikiPages.mockResolvedValue({
      revision: 3,
      pages: [
        { page_key: 'amf-init', title: 'AMF 开局指导' },
        { page_key: 'amf-key-only', title: '' },
      ],
    })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.openCreate()
    await flushPromises()
    vm.form.artifactId = 'art-w1'
    await vm.onArtifactChange('art-w1')

    expect(state.artifacts.listWikiPages).toHaveBeenCalledWith('art-w1', { revision: 3 })
    expect(vm.targetOptions).toEqual([
      { value: 'amf-init', label: 'AMF 开局指导' },
      { value: 'amf-key-only', label: 'amf-key-only' },
    ])
  })

  it('整体级创建：target_key 缺省下发，成功后重置表单并刷新列表', async () => {
    state.artifacts.list.mockResolvedValue([
      { artifact_id: 'art-t1', kind: 'table', current_revision: 2 },
    ])
    state.ontology.listObjectRefs.mockResolvedValue({ items: [], truncated: false })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.openCreate()
    await flushPromises()
    vm.form.artifactId = 'art-t1'
    vm.form.targetKey = ''
    vm.form.note = '承载 AMF 全部运维口径'
    await vm.submit()
    await flushPromises()

    expect(state.ontology.createObjectRef).toHaveBeenCalledWith('obj-1', {
      artifact_id: 'art-t1', target_key: undefined, note: '承载 AMF 全部运维口径',
    })
    // 成功路径重置视图状态：表单清空、收起、列表重查、changed 冒泡
    expect(vm.form).toEqual({ artifactId: '', targetKey: '', note: '' })
    expect(vm.createVisible).toBe(false)
    expect(state.ontology.listObjectRefs).toHaveBeenCalledTimes(2)  // 挂载即拉 + 建后重查
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('行级创建：target_key=row_key 原样下发', async () => {
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    vm.createVisible = true
    vm.form.artifactId = 'art-t1'
    vm.form.targetKey = 'cmd-amf-001'
    await vm.submit()
    await flushPromises()

    expect(state.ontology.createObjectRef).toHaveBeenCalledWith('obj-1', {
      artifact_id: 'art-t1', target_key: 'cmd-amf-001', note: undefined,
    })
  })

  it('422 目标不存在：错误文案拼上后端 valid_keys 样例', async () => {
    state.ontology.createObjectRef.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: { message: '目标不存在于当前版本', valid_keys: ['cmd-amf-001', 'cmd-amf-002'] } },
      },
    })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    vm.createVisible = true
    vm.form.artifactId = 'art-t1'
    vm.form.targetKey = 'cmd-gone'
    await vm.submit()
    await flushPromises()

    expect(state.ui.error).toHaveBeenCalledWith(
      '创建失败：mock-error（可引用键样例：cmd-amf-001、cmd-amf-002）')
    // 失败保留表单（用户改选目标重试）
    expect(vm.createVisible).toBe(true)
  })

  it('未选制品提交：警告且不调 API', async () => {
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    vm.createVisible = true
    await vm.submit()

    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.ontology.createObjectRef).not.toHaveBeenCalled()
  })
})

describe('ObjectRefsDialog — 删除与跳转互通', () => {
  it('删除走确认 → DELETE → 刷新 + changed 冒泡', async () => {
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [makeRef({ id: 'sor_del' })], truncated: false,
    })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.removeRef(vm.items[0])
    await flushPromises()

    expect(state.ontology.deleteObjectRef).toHaveBeenCalledWith('obj-1', 'sor_del')
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('确认被取消则不删除', async () => {
    state.ui.confirm.mockReset().mockRejectedValue('cancel')
    state.ontology.listObjectRefs.mockResolvedValue({
      items: [makeRef({ id: 'sor_del' })], truncated: false,
    })
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    await vm.removeRef(vm.items[0])

    expect(state.ontology.deleteObjectRef).not.toHaveBeenCalled()
  })

  it('refs 列表项点击跳制品页（对象→制品最小互通）', async () => {
    const wrapper = mountDialog(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as RefsVm

    vm.openArtifact(makeRef({ artifact_id: 'art-9' }))
    expect(state.router.push).toHaveBeenCalledWith({
      name: 'artifact-detail',
      params: { artifactId: 'art-9' },
    })
  })
})
