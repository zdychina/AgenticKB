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
}))

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
  openCreate: () => void
  openEdit: (row: { id: string }) => Promise<void>
  onFormOntologyChange: (ontologyId: string) => Promise<void>
  collectAttributes: () => Record<string, unknown>
  save: () => Promise<void>
}

const ontologies = [
  { id: 'onto-drug', library_id: 'lib-1', name: '药品', description: null, created_at: '2026-11-17T08:00:00+00:00' },
  { id: 'onto-disease', library_id: 'lib-1', name: '疾病', description: null, created_at: '2026-11-17T08:01:00+00:00' },
]
const relations = [
  {
    id: 'rel-treats', library_id: 'lib-1', name: '治疗', description: null,
    source_ontology_id: 'onto-drug', target_ontology_id: 'onto-disease',
    created_at: '2026-11-17T08:02:00+00:00',
    source_ontology_name: '药品', target_ontology_name: '疾病', instance_count: 1,
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
          id: 'obj-1', library_id: 'lib-1', ontology_id: 'onto-drug', ontology_name: '药品',
          name: '阿司匹林', attributes_json: null, source: 'manual',
          created_by: 'u1', created_at: '2026-11-17T08:00:00+00:00',
          updated_at: '2026-11-17T08:00:00+00:00',
        },
      ],
      total: 1,
    })
    state.api.getOntology.mockReset().mockResolvedValue({
      id: 'onto-drug', library_id: 'lib-1', name: '药品', description: null,
      created_at: '2026-11-17T08:00:00+00:00',
      attributes: [
        { id: 'attr-1', name: '适应症', description: '适用症状', sort_order: 0 },
        { id: 'attr-2', name: '剂量', description: null, sort_order: 1 },
        { id: 'attr-3', name: '处方药', description: null, sort_order: 2 },
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

  it('builds the dynamic form from the ontology attribute defs', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    expect(vm.formVisible).toBe(true)
    vm.form.ontologyId = 'onto-drug'
    await vm.onFormOntologyChange('onto-drug')

    expect(state.api.getOntology).toHaveBeenCalledWith('onto-drug')
    expect(vm.form.attributeDefs.map(d => d.name)).toEqual(['适应症', '剂量', '处方药'])
  })

  it('collects only filled values — empty text keys are omitted', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-drug'
    await vm.onFormOntologyChange('onto-drug')

    // 新建无存量值 → 全部按文本槽（数字/布尔槽由编辑路径的存量值激活，见下例）
    vm.textSlots['适应症'] = '  发热、头痛  '
    expect(vm.collectAttributes()).toEqual({ 适应症: '发热、头痛' })

    vm.textSlots['剂量'] = ' 50mg '
    expect(vm.collectAttributes()).toEqual({ 适应症: '发热、头痛', 剂量: '50mg' })

    // 空白文本 = 不写键
    vm.textSlots['适应症'] = '   '
    expect(vm.collectAttributes()).toEqual({ 剂量: '50mg' })
  })

  it('creates an object with the collected attributes', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-drug'
    await vm.onFormOntologyChange('onto-drug')
    vm.form.name = '  布洛芬  '
    vm.textSlots['适应症'] = '止痛'
    await vm.save()

    expect(state.api.createObject).toHaveBeenCalledWith('onto-drug', {
      name: '布洛芬', attributes: { 适应症: '止痛' },
    })
    expect(vm.formVisible).toBe(false)
    expect(wrapper.emitted('refresh')).toBeTruthy()
  })

  it('splits value slots by existing value shape when editing', async () => {
    state.api.getObject.mockResolvedValue({
      id: 'obj-1', library_id: 'lib-1', ontology_id: 'onto-drug', ontology_name: '药品',
      name: '阿司匹林',
      attributes_json: { 适应症: '发热', 剂量: 100, 处方药: false },
      source: 'manual', created_by: 'u1',
      created_at: '2026-11-17T08:00:00+00:00', updated_at: '2026-11-17T08:00:00+00:00',
      attribute_defs: [
        { id: 'attr-1', name: '适应症', description: null, sort_order: 0 },
        { id: 'attr-2', name: '剂量', description: null, sort_order: 1 },
        { id: 'attr-3', name: '处方药', description: null, sort_order: 2 },
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
    expect(vm.textSlots['适应症']).toBe('发热')
    expect(vm.numberSlots['剂量']).toBe(100)
    expect(vm.boolSlots['处方药']).toBe(false)

    await vm.save()
    // PUT 全量替换 attributes（含布尔 false）
    expect(state.api.updateObject).toHaveBeenCalledWith('obj-1', {
      name: '阿司匹林',
      attributes: { 适应症: '发热', 剂量: 100, 处方药: false },
    })
  })

  it('blocks empty names and warns before calling the api', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as ObjectTabVm

    vm.openCreate()
    vm.form.ontologyId = 'onto-drug'
    await vm.onFormOntologyChange('onto-drug')
    vm.form.name = '   '
    await vm.save()

    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.api.createObject).not.toHaveBeenCalled()
  })
})
