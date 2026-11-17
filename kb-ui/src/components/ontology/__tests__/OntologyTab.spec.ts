import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    createOntology: vi.fn(), updateOntology: vi.fn(), deleteOntology: vi.fn(),
    getOntology: vi.fn(), addAttribute: vi.fn(), updateAttribute: vi.fn(),
    deleteAttribute: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import OntologyTab from '../OntologyTab.vue'

interface OntoTabVm {
  keyword: string
  filteredOntologies: Array<{ id: string; name: string }>
  createForm: {
    name: string
    description: string
    attributes: Array<{ name: string; description: string }>
  }
  createVisible: boolean
  openCreate: () => void
  create: () => Promise<void>
}

const ontologies = [
  { id: 'onto-device', library_id: 'lib-1', name: '设备', description: null, created_at: '2026-11-17T08:00:00+00:00' },
  { id: 'onto-feature', library_id: 'lib-1', name: '特性', description: null, created_at: '2026-11-17T08:01:00+00:00' },
]

function mountTab(list = ontologies) {
  return shallowMount(OntologyTab, {
    props: { libraryId: 'lib-1', canWrite: true, ontologies: list },
  })
}

describe('ontology tab — 类型层管理（67号 §5.2）', () => {
  beforeEach(() => {
    state.api.createOntology.mockReset().mockResolvedValue({ id: 'onto-new' })
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('submits initial attributes with trimmed names and rejects blank rows', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as OntoTabVm

    vm.openCreate()
    vm.createForm = {
      name: '  命令  ', description: ' MML 命令 ',
      attributes: [
        { name: ' 命令语法 ', description: '' },
        { name: '风险等级', description: ' 说明 ' },
      ],
    }
    await vm.create()

    expect(state.api.createOntology).toHaveBeenCalledWith('lib-1', {
      name: '命令', description: 'MML 命令',
      attributes: [
        { name: '命令语法', description: undefined },
        { name: '风险等级', description: '说明' },
      ],
    })

    // 空属性行（有行无名字）整单拦下
    state.api.createOntology.mockClear()
    vm.openCreate()
    vm.createForm = { name: '场景', description: '', attributes: [{ name: '  ', description: '' }] }
    await vm.create()
    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.api.createOntology).not.toHaveBeenCalled()
  })

  it('新建后清掉名称过滤，新本体立即可见（回归：曾须 F5 才显示）', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as OntoTabVm
    vm.keyword = '设备'
    expect(vm.filteredOntologies.map(o => o.name)).toEqual(['设备'])

    vm.openCreate()
    vm.createForm = { name: '命令', description: '', attributes: [] }
    await vm.create()
    await flushPromises()

    // 模拟父组件 refreshAll 后下发新 ontologies
    await wrapper.setProps({
      ontologies: [...ontologies,
        { id: 'onto-new', library_id: 'lib-1', name: '命令', description: null, created_at: '2026-11-17T09:00:00+00:00' }],
    })

    expect(vm.keyword).toBe('')
    expect(vm.filteredOntologies.map(o => o.name)).toContain('命令')
  })
})
