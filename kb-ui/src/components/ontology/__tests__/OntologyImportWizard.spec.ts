import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    getOntology: vi.fn(), uploadObjectImport: vi.fn(), commitObjectImport: vi.fn(),
  },
  ui: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/ontology', () => ({ useOntologyApi: () => state.api }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: vi.fn(async (error: unknown) =>
    String((error as { response?: { data?: { detail?: string } } })
      ?.response?.data?.detail ?? '请求失败')),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import OntologyImportWizard from '../OntologyImportWizard.vue'
import type {
  ImportUploadResult, ImportValidationReport,
} from '@/types/ontology'

interface WizardVm {
  ontologyId: string
  upload: ImportUploadResult | null
  nameColumn: string
  attributeMapping: Record<string, string>
  result: { ontology_id: string; imported: number } | null
  report: ImportValidationReport | null
  doUpload: (file: File) => Promise<void>
  runImport: () => Promise<void>
  buildAttributeColumns: () => Record<string, string>
  exportErrors: () => void
  resetWizard: () => void
}

const ontologies = [
  { id: 'onto-drug', library_id: 'lib-1', name: '药品', description: null, created_at: '2026-11-17T08:00:00+00:00' },
]

const uploadResult: ImportUploadResult = {
  import_token: 'tok-1',
  row_count: 2,
  columns: [
    { name: '名称', sample: '阿司匹林' },
    { name: '适应症', sample: '发热' },
    { name: '用量', sample: 100 },
  ],
  preview_rows: [
    { 名称: '阿司匹林', 适应症: '发热', 用量: 100 },
    { 名称: '布洛芬', 适应症: '止痛', 用量: 200 },
  ],
  detected_mapping: { name_column: '名称', attribute_columns: { 适应症: '适应症' } },
}

function mountWizard() {
  return shallowMount(OntologyImportWizard, { props: { ontologies } })
}

/** 完成上传步：选定本体 + 解析成功，返回 vm。 */
async function mountedAfterUpload() {
  const wrapper = mountWizard()
  const vm = wrapper.vm as unknown as WizardVm
  vm.ontologyId = 'onto-drug'
  await vm.doUpload(new File(['名称,适应症\n阿司匹林,发热\n'], '药品.csv', { type: 'text/csv' }))
  await flushPromises()
  return { wrapper, vm }
}

describe('ontology import wizard — 两步式批量导入（67号 §七）', () => {
  beforeEach(() => {
    state.api.getOntology.mockReset().mockResolvedValue({
      id: 'onto-drug', library_id: 'lib-1', name: '药品', description: null,
      created_at: '2026-11-17T08:00:00+00:00',
      attributes: [
        { id: 'attr-1', name: '适应症', description: null, sort_order: 0 },
        { id: 'attr-2', name: '剂量', description: null, sort_order: 1 },
        { id: 'attr-3', name: '处方药', description: null, sort_order: 2 },
      ],
      ontology_relations: [],
    })
    state.api.uploadObjectImport.mockReset().mockResolvedValue(uploadResult)
    state.api.commitObjectImport.mockReset()
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('requires an ontology selection before upload', async () => {
    const wrapper = mountWizard()
    const vm = wrapper.vm as unknown as WizardVm
    await vm.doUpload(new File(['x'], 'a.csv'))
    expect(state.api.uploadObjectImport).not.toHaveBeenCalled()
    expect(state.ui.warning).toHaveBeenCalled()
  })

  it('uploads, loads attribute defs and applies the detected mapping', async () => {
    const { vm } = await mountedAfterUpload()

    expect(state.api.uploadObjectImport).toHaveBeenCalledWith('onto-drug', expect.any(File))
    expect(state.api.getOntology).toHaveBeenCalledWith('onto-drug')
    expect(vm.upload?.import_token).toBe('tok-1')
    // 名称列自动预选；列名==属性名的列预选，其余属性留空（'不导入'）
    expect(vm.nameColumn).toBe('名称')
    expect(vm.attributeMapping).toEqual({ 适应症: '适应症', 剂量: '', 处方药: '' })
  })

  it('inverts the per-attribute mapping into column→attribute and drops blanks', async () => {
    const { vm } = await mountedAfterUpload()

    vm.attributeMapping['剂量'] = '用量'
    vm.attributeMapping['处方药'] = '   '
    expect(vm.buildAttributeColumns()).toEqual({ 适应症: '适应症', 用量: '剂量' })
  })

  it('commits with the token and mapping, then emits imported', async () => {
    const { wrapper, vm } = await mountedAfterUpload()
    vm.attributeMapping['剂量'] = '用量'
    state.api.commitObjectImport.mockResolvedValue({ ontology_id: 'onto-drug', imported: 2 })

    await vm.runImport()
    await flushPromises()

    expect(state.api.commitObjectImport).toHaveBeenCalledWith('onto-drug', {
      import_token: 'tok-1', name_column: '名称',
      attribute_columns: { 适应症: '适应症', 用量: '剂量' },
    })
    expect(vm.result?.imported).toBe(2)
    expect(wrapper.emitted('imported')?.[0]).toEqual([{ ontology_id: 'onto-drug', imported: 2 }])
  })

  it('extracts the 422 structured report instead of a flat toast', async () => {
    const { vm } = await mountedAfterUpload()
    const report: ImportValidationReport = {
      message: '2 行校验失败，整批未导入',
      total_rows: 5, valid_rows: 3, error_rows: 2,
      errors: [
        { row: 1, code: 'duplicate_existing', message: '对象「阿司匹林」已存在' },
        { row: 3, code: 'empty_name', message: '名称列为空' },
      ],
    }
    state.api.commitObjectImport.mockRejectedValue({
      response: { status: 422, data: { detail: { message: report.message, report } } },
    })

    await vm.runImport()
    await flushPromises()

    // 结构化报告进入卡③表格，而不是被 apiErrorDetail 压平成一条 toast
    expect(vm.report).toEqual(report)
    expect(vm.result).toBeNull()
    expect(state.ui.error).not.toHaveBeenCalled()
  })

  it('resets the wizard on 410 preview_expired', async () => {
    const { vm } = await mountedAfterUpload()
    state.api.commitObjectImport.mockRejectedValue({
      response: { status: 410, data: { detail: 'preview_expired' } },
    })

    await vm.runImport()
    await flushPromises()

    expect(vm.upload).toBeNull()
    expect(vm.nameColumn).toBe('')
    expect(state.ui.error).toHaveBeenCalledWith(expect.stringContaining('重新上传'))
  })

  it('exports the error report as a BOM-prefixed CSV', async () => {
    const { vm } = await mountedAfterUpload()
    vm.report = {
      message: '1 行校验失败，整批未导入',
      total_rows: 2, valid_rows: 1, error_rows: 1,
      errors: [{ row: 1, code: 'duplicate_existing', message: '对象「阿司匹林"旧」已存在' }],
    }
    // jsdom 未实现 createObjectURL —— 手工打桩并捕获 Blob
    const createObjectURL = vi.fn().mockReturnValue('mock-url')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    try {
      vm.exportErrors()
      expect(createObjectURL).toHaveBeenCalledTimes(1)
      expect(click).toHaveBeenCalledTimes(1)
      expect(revokeObjectURL).toHaveBeenCalledWith('mock-url')

      const blob = createObjectURL.mock.calls[0][0] as Blob
      // BOM 按字节断言（Blob.text() 的 UTF-8 解码会吞掉首部 U+FEFF）
      const bytes = new Uint8Array(await blob.arrayBuffer())
      expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf])
      const text = await blob.text()
      expect(text.startsWith('行号,错误类型,说明')).toBe(true)
      expect(text).toContain('1,"与库内重名","对象「阿司匹林""旧」已存在"')  // 引号转义
    } finally {
      click.mockRestore()
      delete (URL as { createObjectURL?: unknown }).createObjectURL
      delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL
    }
  })
})
