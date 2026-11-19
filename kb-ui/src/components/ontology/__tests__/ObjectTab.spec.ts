import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    listObjects: vi.fn(), getObject: vi.fn(), getOntology: vi.fn(),
    createObject: vi.fn(), updateObject: vi.fn(), deleteObject: vi.fn(),
    getObjectNeighbors: vi.fn(), createSemanticRelation: vi.fn(),
    deleteSemanticRelation: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  router: { push: vi.fn() },
}))

vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import ObjectTab from '../ObjectTab.vue'
import type { OntologyRelation } from '@/types/ontology'

interface ObjectTabVm {
  items: Array<{ id: string; name: string }>
  total: number
  keyword: string
  filterOntologyId: string
  page: number
  form: {
    ontologyId: string
    ontologyName: string
    name: string
    attributeDefs: Array<{ id: string; name: string }>
  }
  formVisible: boolean
  editingId: string
  textSlots: Record<string, string>
  numberSlots: Record<string, number | undefined>
  boolSlots: Record<string, boolean>
  edgeForm: { direction: 'out' | 'in'; relationId: string; otherObjectId: string }
  openCreate: () => void
  openEdit: (row: { id: string }) => Promise<void>
  onFormOntologyChange: (ontologyId: string) => Promise<void>
  collectAttributes: () => Record<string, unknown>
  save: () => Promise<void>
  search: () => void
  reload: () => Promise<void>
  remove: (row: { id: string; name: string }) => Promise<void>
  openSourceDocument: (row: {
    source_document_id?: string | null
    source_document_name?: string | null
    source_kb_id?: string | null
  }) => void
  neighbors: {
    outgoing: Array<{ id: string }>
    incoming: Array<{ id: string }>
    artifact_refs?: Array<{ artifact_id: string }>
    artifact_refs_truncated?: boolean
  }
  detail: { id: string; name: string } | null
  refsDialogVisible: boolean
  refsTarget: { id: string; name: string }
  refsSummary: string
  openRefs: (row: { id: string; name: string }) => void
  openRefsFromDetail: () => void
  loadDetail: (objectId: string) => Promise<void>
}

const ontologies = [
  { id: 'onto-device', library_id: 'lib-1', name: '设备', description: null, created_at: '2026-11-17T08:00:00+00:00' },
  { id: 'onto-feature', library_id: 'lib-1', name: '特性', description: null, created_at: '2026-11-17T08:01:00+00:00' },
]
const relations = [
  {
    id: 'rel-supports', library_id: 'lib-1', name: '支持', description: null,
    source_ontology_id: 'onto-device', target_ontology_id: 'onto-feature',
    created_at: '2026-11-17T08:02:00+00:00',
    source_ontology_name: '设备', target_ontology_name: '特性', instance_count: 1,
  },
]

function mountTab() {
  return shallowMount(ObjectTab, {
    props: {
      libraryId: 'lib-1',
      canWrite: true,
      ontologies,
      relations: relations as OntologyRelation[],
    },
  })
}

describe('object tab — 手工实例化动态表单（67号 §5.3）', () => {
  beforeEach(() => {
    state.api.listObjects.mockReset().mockResolvedValue({
      items: [
        {
          id: 'obj-1', library_id: 'lib-1', ontology_id: 'onto-device', ontology_name: '设备',
          name: 'AMF', attributes_json: null, source: 'manual',
          created_by: 'u1', created_at: '2026-11-17T08:00:00+00:00',
          updated_at: '2026-11-17T08:00:00+00:00',
        },
      ],
      total: 1,
    })
    state.api.getOntology.mockReset().mockResolvedValue({
      id: 'onto-device', library_id: 'lib-1', name: '设备', description: null,
      created_at: '2026-11-17T08:00:00+00:00',
      attributes: [
        { id: 'attr-1', name: '厂商', description: '生产厂商', sort_order: 0 },
        { id: 'attr-2', name: '端口数', description: null, sort_order: 1 },
        { id: 'attr-3', name: '在网状态', description: null, sort_order: 2 },
      ],
      ontology_relations: [],
    })
    state.api.getObject.mockReset()
    state.api.createObject.mockReset().mockResolvedValue({ id: 'obj-new' })
    state.api.updateObject.mockReset().mockResolvedValue({ id: 'obj-1' })
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('loads the first page on mount with pagination params', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    expect(state.api.listObjects).toHaveBeenCalledWith('lib-1', {
      ontology_id: undefined, q: undefined, limit: 20, offset: 0,
    })
    expect(vm.items).toHaveLength(1)
    expect(vm.total).toBe(1)
  })

  it('jumps to the source document preview for extract objects (69号溯源)', async () => {
    state.api.listObjects.mockReset().mockResolvedValue({
      items: [
        {
          id: 'obj-amf', library_id: 'lib-1', ontology_id: 'onto-device', ontology_name: '设备',
          name: 'AMF', attributes_json: null, source: 'extract',
          created_by: 'u1', created_at: '2026-11-18T08:00:00+00:00',
          updated_at: '2026-11-18T08:00:00+00:00',
          source_document_id: 'doc-1', source_document_name: 'AMF开局指导书.md',
          source_kb_id: 'kb-9',
        },
        {
          id: 'obj-smf', library_id: 'lib-1', ontology_id: 'onto-device', ontology_name: '设备',
          name: 'SMF', attributes_json: null, source: 'extract',
          created_by: 'u1', created_at: '2026-11-18T08:01:00+00:00',
          updated_at: '2026-11-18T08:01:00+00:00',
          source_document_id: 'doc-gone', source_document_name: null, source_kb_id: null,
        },
      ],
      total: 2,
    })
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openSourceDocument(vm.items[0] as never)
    expect(state.router.push).toHaveBeenCalledWith({
      name: 'kb-doc-preview',
      params: { kbId: 'kb-9', docId: 'doc-1' },
    })

    // 文档已删（kb_id/name 降级 null）：不跳转，列表走「不可访问」纯文案
    state.router.push.mockClear()
    vm.openSourceDocument(vm.items[1] as never)
    expect(state.router.push).not.toHaveBeenCalled()
  })

  it('builds the dynamic form from the ontology attribute defs', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    expect(vm.formVisible).toBe(true)
    vm.form.ontologyId = 'onto-device'
    await vm.onFormOntologyChange('onto-device')

    expect(state.api.getOntology).toHaveBeenCalledWith('onto-device')
    expect(vm.form.attributeDefs.map(d => d.name)).toEqual(['厂商', '端口数', '在网状态'])
  })

  it('collects only filled values — empty text keys are omitted', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-device'
    await vm.onFormOntologyChange('onto-device')

    // 新建无存量值 → 全部按文本槽（数字/布尔槽由编辑路径的存量值激活，见下例）
    vm.textSlots['厂商'] = '  华为、中兴  '
    expect(vm.collectAttributes()).toEqual({ 厂商: '华为、中兴' })

    vm.textSlots['端口数'] = ' 50mg '
    expect(vm.collectAttributes()).toEqual({ 厂商: '华为、中兴', 端口数: '50mg' })

    // 空白文本 = 不写键
    vm.textSlots['厂商'] = '   '
    expect(vm.collectAttributes()).toEqual({ 端口数: '50mg' })
  })

  it('creates an object with the collected attributes', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-device'
    await vm.onFormOntologyChange('onto-device')
    vm.form.name = '  SMF  '
    vm.textSlots['厂商'] = '中兴'
    await vm.save()

    expect(state.api.createObject).toHaveBeenCalledWith('onto-device', {
      name: 'SMF', attributes: { 厂商: '中兴' },
    })
    expect(vm.formVisible).toBe(false)
    expect(wrapper.emitted('refresh')).toBeTruthy()
  })

  it('splits value slots by existing value shape when editing', async () => {
    state.api.getObject.mockResolvedValue({
      id: 'obj-1', library_id: 'lib-1', ontology_id: 'onto-device', ontology_name: '设备',
      name: 'AMF',
      attributes_json: { 厂商: '华为', 端口数: 100, 在网状态: false },
      source: 'manual', created_by: 'u1',
      created_at: '2026-11-17T08:00:00+00:00', updated_at: '2026-11-17T08:00:00+00:00',
      attribute_defs: [
        { id: 'attr-1', name: '厂商', description: null, sort_order: 0 },
        { id: 'attr-2', name: '端口数', description: null, sort_order: 1 },
        { id: 'attr-3', name: '在网状态', description: null, sort_order: 2 },
      ],
      outgoing_count: 0, incoming_count: 0,
    })
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    await vm.openEdit({ id: 'obj-1' })
    expect(state.api.getObject).toHaveBeenCalledWith('obj-1')
    expect(vm.editingId).toBe('obj-1')
    // 存量值形态决定控件槽位：文本/数字/布尔分流
    expect(vm.textSlots['厂商']).toBe('华为')
    expect(vm.numberSlots['端口数']).toBe(100)
    expect(vm.boolSlots['在网状态']).toBe(false)

    await vm.save()
    // PUT 全量替换 attributes（含布尔 false）
    expect(state.api.updateObject).toHaveBeenCalledWith('obj-1', {
      name: 'AMF',
      attributes: { 厂商: '华为', 端口数: 100, 在网状态: false },
    })
  })

  it('blocks empty names and warns before calling the api', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-device'
    await vm.onFormOntologyChange('onto-device')
    vm.form.name = '   '
    await vm.save()

    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.api.createObject).not.toHaveBeenCalled()
  })
})

describe('object tab — 新建后立即可见（回归：留存筛选曾把新对象滤到 F5 才可见）', () => {
  /** 带状态的后端镜像：listObjects 尊重 q/ontology_id 过滤 + offset 分页。 */
  let db: Array<{ id: string; ontology_id: string; name: string }>

  beforeEach(() => {
    db = [{ id: 'obj-1', ontology_id: 'onto-device', name: 'AMF' }]
    state.api.listObjects.mockReset().mockImplementation(
      async (_lib: string, params: { ontology_id?: string; q?: string; limit?: number; offset?: number }) => {
        let rows = db
        if (params?.ontology_id) rows = rows.filter(r => r.ontology_id === params.ontology_id)
        if (params?.q) rows = rows.filter(r => r.name.includes(params.q!))
        const offset = params?.offset ?? 0
        return { items: rows.slice(offset, offset + (params?.limit ?? 20)), total: rows.length }
      })
    state.api.createObject.mockReset().mockImplementation(
      async (ontologyId: string, payload: { name: string }) => {
        const row = { id: `obj-${db.length + 1}`, ontology_id: ontologyId, name: payload.name }
        db.push(row)
        return row
      })
    state.api.getOntology.mockReset().mockResolvedValue({
      id: 'onto-device', library_id: 'lib-1', name: '设备', description: null,
      created_at: '2026-11-17T08:00:00+00:00', attributes: [], ontology_relations: [],
    })
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  async function createNamed(wrapper: { vm: unknown }, ontologyId: string, name: string) {
    const vm = wrapper.vm as unknown as ObjectTabVm
    vm.openCreate()
    vm.form.ontologyId = ontologyId
    vm.form.name = name
    await vm.save()
    await flushPromises()
  }

  it('新建后清掉留存搜索词，新对象立即出现在列表里', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm
    vm.keyword = 'AMF'

    await createNamed(wrapper, 'onto-device', 'UPF')

    expect(vm.keyword).toBe('')
    expect(vm.items.map(r => r.name)).toContain('UPF')
  })

  it('本体过滤指向别的本体时新建后清掉，新对象可见', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm
    vm.filterOntologyId = 'onto-device'

    await createNamed(wrapper, 'onto-feature', '网络切片')

    expect(vm.filterOntologyId).toBe('')
    expect(vm.items.map(r => r.name)).toContain('网络切片')
  })

  it('本体过滤与新对象同本体时保留，新对象同样可见', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm
    vm.filterOntologyId = 'onto-device'

    await createNamed(wrapper, 'onto-device', 'SMF')

    expect(vm.filterOntologyId).toBe('onto-device')
    expect(vm.items.map(r => r.name)).toContain('SMF')
  })

  it('筛选/搜索变化回第一页（残留页码的 offset 会落在空页上）', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm
    vm.page = 3
    vm.keyword = 'AMF'

    vm.search()
    await flushPromises()

    expect(vm.page).toBe(1)
    const lastCall = state.api.listObjects.mock.calls.at(-1)
    expect(lastCall?.[1]).toMatchObject({ q: 'AMF', offset: 0 })
  })

  it('删除页内最后一条时回退一页，不停留在空页', async () => {
    for (let i = 0; i < 20; i++) db.push({ id: `obj-b${i}`, ontology_id: 'onto-device', name: `网元${i}` })
    state.api.deleteObject.mockReset().mockImplementation(async (id: string) => {
      db = db.filter(r => r.id !== id)
    })
    state.ui.confirm.mockReset().mockResolvedValue(undefined)
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.page = 2
    await vm.reload()
    expect(vm.items).toHaveLength(1)   // 21 条 → 第 2 页只剩 1 条

    await vm.remove(vm.items[0])
    await flushPromises()

    expect(vm.page).toBe(1)
    expect(vm.items).toHaveLength(20)
  })

  it('建边对话框切换方向后清掉已选的关系类型与对端', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.edgeForm = { direction: 'out', relationId: 'rel-supports', otherObjectId: 'obj-1' }
    vm.edgeForm.direction = 'in'
    await flushPromises()

    expect(vm.edgeForm.relationId).toBe('')
    expect(vm.edgeForm.otherObjectId).toBe('')
  })
})

describe('object tab — 制品引用（70号：行操作入口 + 对象卡可见计数）', () => {
  beforeEach(() => {
    state.api.listObjects.mockReset().mockResolvedValue({ items: [], total: 0 })
    state.api.getObject.mockReset().mockResolvedValue({
      id: 'obj-1', library_id: 'lib-1', ontology_id: 'onto-device', ontology_name: '设备',
      name: 'AMF', attributes_json: null, source: 'manual', created_by: 'u1',
      created_at: '2026-11-19T08:00:00+00:00', updated_at: '2026-11-19T08:00:00+00:00',
      attribute_defs: [], outgoing_count: 0, incoming_count: 0,
    })
    state.api.getObjectNeighbors.mockReset().mockResolvedValue({
      outgoing: [], incoming: [],
      artifact_refs: [
        { artifact_id: 'art-1', artifact_title: 'MOP 表', kind: 'table', target_key: 'cmd-amf-001' },
        { artifact_id: 'art-w1', artifact_title: '运维 wiki', kind: 'wiki', target_key: '' },
      ],
      artifact_refs_truncated: false,
    })
    state.ui.error.mockReset()
  })

  it('行操作「引用」定位对象并打开引用对话框', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openRefs({ id: 'obj-2', name: 'SMF' })

    expect(vm.refsTarget).toEqual({ id: 'obj-2', name: 'SMF' })
    expect(vm.refsDialogVisible).toBe(true)
  })

  it('对象卡摘要计数=可见引用数，截断显示 N+', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    await vm.loadDetail('obj-1')
    await flushPromises()

    expect(vm.refsSummary).toBe('2')

    vm.neighbors.artifact_refs_truncated = true
    await flushPromises()
    expect(vm.refsSummary).toBe('2+')
  })

  it('neighbors 响应缺 artifact_refs（旧口径/异常）时计数兜底 0', async () => {
    state.api.getObjectNeighbors.mockResolvedValue({ outgoing: [], incoming: [] })
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    await vm.loadDetail('obj-1')
    await flushPromises()

    expect(vm.refsSummary).toBe('0')
  })

  it('对象卡「管理」入口从当前详情对象打开对话框', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    await vm.loadDetail('obj-1')
    await flushPromises()
    vm.openRefsFromDetail()

    expect(vm.refsTarget).toEqual({ id: 'obj-1', name: 'AMF' })
    expect(vm.refsDialogVisible).toBe(true)
  })
})
