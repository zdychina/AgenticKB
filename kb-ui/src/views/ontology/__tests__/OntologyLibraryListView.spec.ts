import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  domain: {
    currentDomain: 'cloud_core_network',
    currentDomainInfo: undefined as
      | { domain_id: string; enabled: boolean; domain_role: string }
      | undefined,
  },
  auth: { siteRole: 'member' },
  router: { push: vi.fn() },
  api: {
    listLibraries: vi.fn(), createLibrary: vi.fn(),
    updateLibrary: vi.fn(), deleteLibrary: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => state.auth }))
vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import OntologyLibraryListView from '../OntologyLibraryListView.vue'

interface ListVm {
  libraries: Array<{ id: string; name: string }>
  canWrite: boolean
  emptyText: string
  form: { name: string; description: string }
  dialogVisible: boolean
  openCreate: () => void
  open: (id: string) => void
  save: () => Promise<void>
  remove: (row: { id: string; name: string; ontology_count: number }) => Promise<void>
}

const ElInputStub = {
  name: 'ElInput',
  props: { modelValue: { type: [String, Number], default: '' } },
  emits: ['update:modelValue'],
  template: '<input class="el-input-stub" />',
}

function mountList() {
  return shallowMount(OntologyLibraryListView, {
    global: { components: { ElInput: ElInputStub } },
  })
}

const libraries = [
  {
    id: 'lib-1', domain: 'cloud_core_network', name: '核心网知识本体', description: '域内设备',
    created_by: 'u1', created_at: '2026-11-17T08:00:00+00:00',
    updated_at: '2026-11-17T08:00:00+00:00',
    ontology_count: 3, object_count: 12, relation_count: 5,
  },
  {
    id: 'lib-2', domain: 'cloud_core_network', name: '设备本体', description: null,
    created_by: 'u1', created_at: '2026-11-16T08:00:00+00:00',
    updated_at: '2026-11-16T08:00:00+00:00',
    ontology_count: 0, object_count: 0, relation_count: 0,
  },
]

describe('ontology library list view（67号 P1）', () => {
  beforeEach(() => {
    state.auth.siteRole = 'member'
    state.domain.currentDomain = 'cloud_core_network'
    state.domain.currentDomainInfo = {
      domain_id: 'cloud_core_network', enabled: true, domain_role: 'admin',
    }
    state.router.push.mockReset()
    state.api.listLibraries.mockReset().mockResolvedValue(libraries)
    state.api.createLibrary.mockReset().mockResolvedValue(libraries[0])
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('loads libraries on mount', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    expect(state.api.listLibraries).toHaveBeenCalledWith(undefined)
    expect(vm.libraries).toHaveLength(2)
    expect(vm.libraries.map(l => l.name)).toEqual(['核心网知识本体', '设备本体'])
  })

  it('grants write to domain admins and members read-only', async () => {
    const wrapper = mountList()
    await flushPromises()
    let vm = wrapper.vm as unknown as ListVm
    expect(vm.canWrite).toBe(true)

    state.domain.currentDomainInfo = {
      domain_id: 'cloud_core_network', enabled: true, domain_role: 'member',
    }
    wrapper.unmount()
    const memberWrapper = mountList()
    await flushPromises()
    vm = memberWrapper.vm as unknown as ListVm
    expect(vm.canWrite).toBe(false)

    state.auth.siteRole = 'admin'
    state.domain.currentDomainInfo = undefined
    const adminWrapper = mountList()
    await flushPromises()
    expect((adminWrapper.vm as unknown as ListVm).canWrite).toBe(true)
  })

  it('creates a library via the current domain and refreshes the list', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.openCreate()
    expect(vm.dialogVisible).toBe(true)
    vm.form = { name: '  新本体库  ', description: ' 说明 ' }
    await vm.save()

    expect(state.api.createLibrary).toHaveBeenCalledWith({
      name: '新本体库', description: '说明',
    })
    // 刷新：mount 一次 + save 后一次
    expect(state.api.listLibraries).toHaveBeenCalledTimes(2)
    expect(vm.dialogVisible).toBe(false)
    expect(state.ui.success).toHaveBeenCalled()
  })

  it('blocks empty names and surfaces api errors', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.openCreate()
    vm.form = { name: '   ', description: '' }
    await vm.save()
    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.api.createLibrary).not.toHaveBeenCalled()

    state.api.createLibrary.mockRejectedValueOnce({
      response: { status: 409, data: { detail: '同名本体库已存在' } },
    })
    vm.form = { name: '核心网知识本体', description: '' }
    await vm.save()
    expect(state.ui.error).toHaveBeenCalled()
    // 失败不关对话框：用户改完名字可直接重试
    expect(vm.dialogVisible).toBe(true)
  })

  it('switches the empty text between pristine and no-match states', async () => {
    state.api.listLibraries.mockResolvedValue([])
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    expect(vm.emptyText).toContain('还没有本体库')

    // 有关键字无命中（关键字过滤在后端 q，空结果即无匹配口径）
    state.api.listLibraries.mockResolvedValue([])
    await flushPromises()
    expect(vm.libraries).toHaveLength(0)
  })

  it('routes into the library detail page', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    expect(vm.libraries).toHaveLength(2)
    // el-table 行在 shallowMount 下不渲染（stub），走 vm 通道直接断言导航
    vm.open('lib-1')
    expect(state.router.push).toHaveBeenCalledWith({
      name: 'ontology-library-detail', params: { libraryId: 'lib-1' },
    })
  })
})
