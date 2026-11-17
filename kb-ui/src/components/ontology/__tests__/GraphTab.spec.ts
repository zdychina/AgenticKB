import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

/** G6 桩：jsdom 无 canvas，整模块 mock；实例捕获后断言喂给画布的数据形状。 */
const g6 = vi.hoisted(() => {
  class Graph {
    static instances: Array<InstanceType<typeof Graph>> = []
    opts: Record<string, unknown>
    handlers: Record<string, Array<(e: unknown) => void>> = {}
    lastData: { nodes: unknown[]; edges: unknown[] } | null = null
    rendered = 0
    destroyed = false
    constructor(opts: Record<string, unknown>) {
      this.opts = opts
      Graph.instances.push(this)
    }
    on(evt: string, fn: (e: unknown) => void) {
      ;(this.handlers[evt] ??= []).push(fn)
    }
    setData(data: { nodes: unknown[]; edges: unknown[] }) {
      this.lastData = data
    }
    async render() {
      this.rendered += 1
    }
    async fitView() {}
    async toDataURL() {
      return 'data:image/png;base64,TEST'
    }
    destroy() {
      this.destroyed = true
    }
  }
  return { Graph }
})
vi.mock('@antv/g6', () => ({ Graph: g6.Graph }))

const state = vi.hoisted(() => ({
  api: {
    getLibraryGraph: vi.fn(), getObject: vi.fn(), getObjectNeighbors: vi.fn(),
  },
  ui: { error: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('element-plus', () => ({
  ElMessage: { error: state.ui.error, warning: state.ui.warning },
}))

import GraphTab from '../GraphTab.vue'

interface GraphTabVm {
  graphData: { nodes: Array<{ id: string }>; total_objects: number } | null
  keyword: string
  libraryEmpty: boolean
  legendChips: Array<{ id: string; name: string; off: boolean; count: number }>
  drawerVisible: boolean
  drawerDetail: { name: string } | null
  drawerNeighbors: { outgoing: unknown[]; incoming: unknown[] }
  load: () => Promise<void>
  renderGraph: () => Promise<void>
  toggleOntology: (id: string) => void
  formatValue: (value: unknown) => string
}

const ontologies = [
  { id: 'onto-device', library_id: 'lib-1', name: '设备', description: null, created_at: '2026-11-17T08:00:00+00:00' },
  { id: 'onto-feature', library_id: 'lib-1', name: '特性', description: null, created_at: '2026-11-17T08:01:00+00:00' },
]

const graph = {
  nodes: [
    { id: 'obj-amf', name: 'AMF', ontology_id: 'onto-device', ontology_name: '设备' },
    { id: 'obj-smf', name: 'SMF', ontology_id: 'onto-device', ontology_name: '设备' },
    { id: 'obj-slice', name: '切片1', ontology_id: 'onto-feature', ontology_name: '特性' },
  ],
  edges: [
    { id: 'srel-1', source: 'obj-amf', target: 'obj-slice', ontology_relation_id: 'orel-1', relation_name: '承载' },
  ],
  total_objects: 3,
  truncated: false,
}

interface DisplayNode {
  id: string
  data: { name: string; color: string; dim: boolean }
}
interface DisplayEdge {
  id: string
  data: { relation_name: string }
}

function lastGraph() {
  const inst = g6.Graph.instances.at(-1)!
  return {
    nodes: (inst.lastData?.nodes ?? []) as DisplayNode[],
    edges: (inst.lastData?.edges ?? []) as DisplayEdge[],
    clickNode: (id: string) => {
      for (const fn of inst.handlers['node:click'] ?? []) {
        fn({ target: { id } })
      }
    },
  }
}

function mountTab() {
  return shallowMount(GraphTab, {
    props: { libraryId: 'lib-1', ontologies },
  })
}

describe('graph tab — 实例图渲染（68号 §四）', () => {
  beforeEach(() => {
    g6.Graph.instances.length = 0
    state.api.getLibraryGraph.mockReset().mockResolvedValue(graph)
    state.api.getObject.mockReset().mockResolvedValue({
      id: 'obj-amf', library_id: 'lib-1', ontology_id: 'onto-device',
      ontology_name: '设备', name: 'AMF', attributes_json: { 厂商: '华为' },
      source: 'manual', created_by: 'u1',
      created_at: '2026-11-17T08:00:00+00:00', updated_at: '2026-11-17T08:00:00+00:00',
      attribute_defs: [
        { id: 'attr-1', name: '厂商', description: null, sort_order: 0 },
      ],
      outgoing_count: 1, incoming_count: 0,
    })
    state.api.getObjectNeighbors.mockReset().mockResolvedValue({
      outgoing: [{
        id: 'srel-1', ontology_relation_id: 'orel-1', relation_name: '承载',
        other_id: 'obj-slice', other_name: '切片1',
        other_ontology_id: 'onto-feature', other_ontology_name: '特性',
      }],
      incoming: [],
    })
    state.ui.error.mockReset()
  })

  it('loads the graph and renders ontology-colored nodes with labeled edges', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphTabVm

    expect(state.api.getLibraryGraph).toHaveBeenCalledWith('lib-1')
    expect(g6.Graph.instances).toHaveLength(1)
    const { nodes, edges } = lastGraph()
    expect(nodes).toHaveLength(3)
    expect(edges).toHaveLength(1)

    // 着色：同本体同色、不同本体不同色（palette 按 props.ontologies 顺序）
    const amf = nodes.find(n => n.id === 'obj-amf')!
    const smf = nodes.find(n => n.id === 'obj-smf')!
    const slice = nodes.find(n => n.id === 'obj-slice')!
    expect(amf.data.color).toBe(smf.data.color)
    expect(slice.data.color).not.toBe(amf.data.color)
    expect(amf.data.dim).toBe(false)
    expect(edges[0].data.relation_name).toBe('承载')
    expect(g6.Graph.instances[0].rendered).toBeGreaterThanOrEqual(1)

    // 图例带对象计数
    expect(vm.legendChips.map(c => [c.name, c.count]))
      .toEqual([['设备', 2], ['特性', 1]])
  })

  it('toggling an ontology chip filters nodes and converges edges', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphTabVm

    vm.toggleOntology('onto-feature')
    await flushPromises()

    const { nodes, edges } = lastGraph()
    expect(nodes.map(n => n.id)).toEqual(['obj-amf', 'obj-smf'])
    // 边的 target 被滤掉 → 整条边收敛（图上不出现悬空半边）
    expect(edges).toEqual([])
    expect(vm.legendChips.find(c => c.id === 'onto-feature')!.off).toBe(true)

    // 再点一次恢复
    vm.toggleOntology('onto-feature')
    await flushPromises()
    expect(lastGraph().nodes).toHaveLength(3)
  })

  it('keyword dims non-matching nodes instead of removing them', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphTabVm

    vm.keyword = 'AMF'
    await vm.renderGraph()
    await flushPromises()

    const { nodes } = lastGraph()
    expect(nodes.find(n => n.id === 'obj-amf')!.data.dim).toBe(false)
    expect(nodes.find(n => n.id === 'obj-smf')!.data.dim).toBe(true)
    expect(nodes).toHaveLength(3)  // 高亮不是删除
  })

  it('node click opens the drawer with detail and neighbors', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphTabVm

    lastGraph().clickNode('obj-amf')
    await flushPromises()

    expect(state.api.getObject).toHaveBeenCalledWith('obj-amf')
    expect(state.api.getObjectNeighbors).toHaveBeenCalledWith('obj-amf')
    expect(vm.drawerVisible).toBe(true)
    expect(vm.drawerDetail?.name).toBe('AMF')
    expect(vm.drawerNeighbors.outgoing).toHaveLength(1)

    // 邻接行 jump → 抽屉切到对端对象
    lastGraph().clickNode('obj-slice')
    await flushPromises()
    expect(state.api.getObject).toHaveBeenCalledWith('obj-slice')
  })

  it('empty library shows guidance and does not create a canvas', async () => {
    state.api.getLibraryGraph.mockResolvedValue({
      nodes: [], edges: [], total_objects: 0, truncated: false,
    })
    const wrapper = mountTab()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphTabVm

    expect(vm.libraryEmpty).toBe(true)
    expect(g6.Graph.instances).toHaveLength(0)
  })

  it('reloads when the parent refreshes ontologies after a write', async () => {
    const wrapper = mountTab()
    await flushPromises()
    expect(state.api.getLibraryGraph).toHaveBeenCalledTimes(1)

    // 其他 Tab 写操作 → 父级 refreshAll 替换 ontologies 引用 → 图谱自动刷新
    await wrapper.setProps({ ontologies: [...ontologies] })
    await flushPromises()
    expect(state.api.getLibraryGraph).toHaveBeenCalledTimes(2)
  })

  it('unmount destroys the graph instance', async () => {
    const wrapper = mountTab()
    await flushPromises()
    wrapper.unmount()
    expect(g6.Graph.instances[0].destroyed).toBe(true)
  })
})
