import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    startObjectExtract: vi.fn(), getObjectExtract: vi.fn(),
    instantiateObjects: vi.fn(), listRelations: vi.fn(),
  },
  kbApi: { listKbs: vi.fn(), listDocuments: vi.fn() },
  ui: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('@/api/kb', () => ({ useKbApi: () => state.kbApi }))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'cloud_core_network' }),
}))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: vi.fn(async (error: unknown) =>
    String((error as { response?: { data?: { detail?: string } } })
      ?.response?.data?.detail ?? '请求失败')),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import OntologyExtractWizard from '../OntologyExtractWizard.vue'
import type {
  ExtractCandidates, InstantiateValidationReport,
} from '@/types/ontology'

interface WizardVm {
  ontologyId: string
  suggestRelations: boolean
  sourceKind: 'text' | 'file' | 'kb_document'
  text: string
  fileName: string
  kbId: string
  documentId: string
  extractError: string
  candidates: ExtractCandidates | null
  objectRows: Array<{
    name: string
    originalName: string
    attributes: Record<string, unknown>
    conflict: boolean
    selected: boolean
  }>
  relationRows: Array<{
    source: string
    relation: string
    target: string
    targetExisting: boolean
    endpointOk: boolean
    relationId: string | null
    selected: boolean
  }>
  result: { object_ids: string[]; relation_ids: string[] } | null
  report: InstantiateValidationReport | null
  readFileText: (file: File) => Promise<void>
  loadKbs: () => Promise<void>
  onKbChange: () => Promise<void>
  startExtract: () => Promise<void>
  seedRows: (data: ExtractCandidates) => Promise<void>
  buildInstantiatePayload: () => {
    ontology_id: string
    source: 'extract'
    objects: Array<{ name: string; attributes: Record<string, unknown> }>
    relations: Array<{
      ontology_relation_id: string
      source_index: number
      target_index?: number
      target_object_id?: string
    }>
  }
  commitInstantiate: () => Promise<void>
  resetWizard: () => void
}

const ontologies = [
  { id: 'onto-drug', library_id: 'lib-1', name: '药品', description: null, created_at: '2026-11-17T08:00:00+00:00' },
]

/** §8.5 候选样例：1 冲突对象 + 1 新对象；2 条关系（已有目标 / 端点不匹配）。 */
const candidates: ExtractCandidates = {
  objects: [
    { name: '阿司匹林', attributes: { 适应症: '发热' }, conflict: true, conflict_with: '阿司匹林' },
    { name: '布洛芬', attributes: {}, conflict: false, conflict_with: null },
  ],
  relations: [
    { source: '阿司匹林', relation: '治疗', target: '感冒', target_existing: true, target_object_id: 'obj-cold', endpoint_ok: true },
    { source: '阿司匹林', relation: '治疗', target: '发烧', target_existing: false, target_object_id: null, endpoint_ok: false },
  ],
  warnings: ['关系「阿司匹林-[治疗]->发烧」的目标对象无法解析，已丢弃'],
  input_chars: 12,
  truncated: false,
}

function mountWizard() {
  return shallowMount(OntologyExtractWizard, {
    props: { libraryId: 'lib-1', ontologies, pollIntervalMs: 1 },
  })
}

function mountedVm(): WizardVm {
  return mountWizard().vm as unknown as WizardVm
}

describe('ontology extract wizard — AI 抽取五步向导（67号 §八）', () => {
  beforeEach(() => {
    state.api.startObjectExtract.mockReset()
    state.api.getObjectExtract.mockReset()
    state.api.instantiateObjects.mockReset()
    state.api.listRelations.mockReset().mockResolvedValue([
      { id: 'rel-1', library_id: 'lib-1', name: '治疗', description: null,
        source_ontology_id: 'onto-drug', target_ontology_id: 'onto-disease',
        created_at: '2026-11-17T08:00:00+00:00' },
    ])
    state.kbApi.listKbs.mockReset().mockResolvedValue([
      { id: 'kb-1', domain: 'cloud_core_network', name: '药品知识库', description: null,
        owner_id: 'u1', visibility: 'private', mining_workflow_id: null,
        created_at: '2026-11-17T08:00:00+00:00' },
    ])
    state.kbApi.listDocuments.mockReset().mockResolvedValue([
      { id: 'doc-1', document_name: '解热镇痛药说明书.md' },
      { id: 'doc-2', document_name: '处方集.csv' },
    ])
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('requires an ontology before starting and pre-checks the 20000-char limit', async () => {
    const vm = mountedVm()
    vm.text = '阿司匹林用于发热。'
    await vm.startExtract()
    expect(state.api.startObjectExtract).not.toHaveBeenCalled()  // 未选本体

    vm.ontologyId = 'onto-drug'
    vm.text = '长'.repeat(20001)
    await vm.startExtract()
    expect(state.api.startObjectExtract).not.toHaveBeenCalled()  // 前端预检超限
  })

  it('starts with kind=text, polls pending→done and seeds candidate rows', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    vm.text = '阿司匹林用于发热。'
    state.api.startObjectExtract.mockResolvedValue({ extract_token: 'tok-1' })
    state.api.getObjectExtract
      .mockResolvedValueOnce({ status: 'pending' })
      .mockResolvedValueOnce({ status: 'done', candidates })

    await vm.startExtract()
    await flushPromises()

    expect(state.api.startObjectExtract).toHaveBeenCalledWith('lib-1', {
      ontology_id: 'onto-drug',
      source: { kind: 'text', text: '阿司匹林用于发热。' },
      suggest_relations: true,
    })
    expect(state.api.getObjectExtract).toHaveBeenCalledTimes(2)
    expect(vm.candidates).toEqual(candidates)
    // 对象默认勾选：非冲突；冲突行默认不选
    expect(vm.objectRows.map(r => [r.name, r.selected])).toEqual([
      ['阿司匹林', false], ['布洛芬', true],
    ])
    // 关系默认勾选：端点匹配且类型在库内；relation name → id 已映射
    expect(vm.relationRows[0]).toMatchObject({ relationId: 'rel-1', selected: true })
    expect(vm.relationRows[1]).toMatchObject({ relationId: 'rel-1', selected: false })
  })

  it('marks the wizard failed when the LLM task fails (no auto retry)', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    vm.text = '文本'
    state.api.startObjectExtract.mockResolvedValue({ extract_token: 'tok-2' })
    state.api.getObjectExtract.mockResolvedValue({
      status: 'failed', error: 'RuntimeError: llm_service down',
    })

    await vm.startExtract()
    await flushPromises()

    expect(vm.extractError).toContain('llm_service down')
    expect(vm.candidates).toBeNull()
  })

  it('loads KBs and documents, and submits a kb_document source', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    vm.sourceKind = 'kb_document'
    await vm.loadKbs()
    vm.kbId = 'kb-1'
    await vm.onKbChange()
    vm.documentId = 'doc-1'
    state.api.startObjectExtract.mockResolvedValue({ extract_token: 'tok-3' })
    state.api.getObjectExtract.mockResolvedValue({ status: 'done', candidates })

    await vm.startExtract()
    await flushPromises()

    expect(state.kbApi.listKbs).toHaveBeenCalledWith('cloud_core_network')
    expect(state.kbApi.listDocuments).toHaveBeenCalledWith('kb-1', undefined, 200)
    expect(state.api.startObjectExtract).toHaveBeenCalledWith('lib-1', {
      ontology_id: 'onto-drug',
      source: { kind: 'kb_document', kb_id: 'kb-1', document_id: 'doc-1' },
      suggest_relations: true,
    })
  })

  it('reads an uploaded txt/md file into text (client-side, kind=text)', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    await vm.readFileText(new File(['说明书内容'], 'drug.md', { type: 'text/markdown' }))
    expect(vm.text).toBe('说明书内容')
    expect(vm.fileName).toBe('drug.md')
  })

  it('builds the instantiate payload with edited names and resolved relation ends', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    await vm.seedRows({
      ...candidates,
      objects: [
        { name: '阿司匹林', attributes: { 适应症: '发热', 剂量: '' }, conflict: false, conflict_with: null },
        { name: '布洛芬', attributes: {}, conflict: false, conflict_with: null },
      ],
      relations: [
        { source: '阿司匹林', relation: '治疗', target: '感冒', target_existing: true, target_object_id: 'obj-cold', endpoint_ok: true },
        { source: '阿司匹林', relation: '治疗', target: '布洛芬', target_existing: false, target_object_id: null, endpoint_ok: true },
      ],
    })

    // 行内编辑：改名 + 目标按原始名（LLM 输出口径）仍可解析
    vm.objectRows[1].name = '布洛芬缓释'
    expect(vm.objectRows.every(r => r.selected)).toBe(true)

    const payload = vm.buildInstantiatePayload()
    expect(payload.ontology_id).toBe('onto-drug')
    expect(payload.source).toBe('extract')
    // 空串属性值剔除（对齐后端「留空不写键」）
    expect(payload.objects).toEqual([
      { name: '阿司匹林', attributes: { 适应症: '发热' } },
      { name: '布洛芬缓释', attributes: {} },
    ])
    expect(payload.relations).toEqual([
      { ontology_relation_id: 'rel-1', source_index: 0, target_object_id: 'obj-cold' },
      { ontology_relation_id: 'rel-1', source_index: 0, target_index: 1 },
    ])
  })

  it('drops relations whose ends fall outside the selected objects', async () => {
    const vm = mountedVm()
    await vm.seedRows(candidates)
    vm.objectRows[0].selected = false   // 阿司匹林（冲突）不选 → 两条关系的源都解析失败
    vm.relationRows[0].selected = true

    const payload = vm.buildInstantiatePayload()
    expect(payload.objects).toEqual([{ name: '布洛芬', attributes: {} }])
    expect(payload.relations).toEqual([])
  })

  it('commits via instantiate, emits instantiated and shows the counts', async () => {
    const wrapper = mountWizard()
    const vm = wrapper.vm as unknown as WizardVm
    vm.ontologyId = 'onto-drug'
    await vm.seedRows(candidates)
    vm.relationRows[0].selected = false   // 只落对象
    state.api.instantiateObjects.mockResolvedValue({
      object_ids: ['new-1'], relation_ids: [],
    })

    await vm.commitInstantiate()
    await flushPromises()

    expect(state.api.instantiateObjects).toHaveBeenCalledWith('lib-1', {
      ontology_id: 'onto-drug',
      source: 'extract',
      objects: [{ name: '布洛芬', attributes: {} }],
      relations: [],
    })
    expect(vm.result).toEqual({ object_ids: ['new-1'], relation_ids: [] })
    expect(wrapper.emitted('instantiated')?.[0])
      .toEqual([{ object_ids: ['new-1'], relation_ids: [] }])
    expect(state.ui.success).toHaveBeenCalled()
  })

  it('renders the 422 located report instead of a flat toast', async () => {
    const vm = mountedVm()
    vm.ontologyId = 'onto-drug'
    await vm.seedRows(candidates)
    const report: InstantiateValidationReport = {
      message: '2 项校验失败，整批未导入',
      total_objects: 2, total_relations: 1,
      errors: [
        { field: 'objects[0]', code: 'duplicate_existing', message: '对象「布洛芬」已存在' },
        { field: 'relations[0]', code: 'endpoint_mismatch', message: '关系「治疗」要求端点本体不匹配' },
      ],
    }
    state.api.instantiateObjects.mockRejectedValue({
      response: { status: 422, data: { detail: { message: report.message, report } } },
    })

    await vm.commitInstantiate()
    await flushPromises()

    expect(vm.report).toEqual(report)
    expect(vm.result).toBeNull()
    expect(state.ui.error).not.toHaveBeenCalled()
  })

  it('resets candidate state for another run', async () => {
    const vm = mountedVm()
    await vm.seedRows(candidates)
    vm.resetWizard()
    expect(vm.candidates).toBeNull()
    expect(vm.objectRows).toEqual([])
    expect(vm.relationRows).toEqual([])
    expect(vm.report).toBeNull()
  })
})
