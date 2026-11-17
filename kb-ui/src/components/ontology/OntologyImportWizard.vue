<template>
  <div class="obj-import">
    <!-- 卡① 选本体与文件：上传 → import_token + 列清单 + 自动映射预选 -->
    <el-card class="obj-import__step" shadow="never">
      <template #header>
        <div class="kb-step">
          <span class="kb-step__no">1</span>选择本体与文件
          <span class="kb-step__hint">.csv / .xlsx，≤10MB，≤5000 数据行；解析后 1 小时内有效</span>
        </div>
      </template>
      <div class="obj-import__pick">
        <span class="obj-import__label">目标本体：</span>
        <el-select
          v-model="ontologyId"
          style="width: 220px"
          placeholder="选择要导入的本体"
          data-test="import-ontology"
          :disabled="!!upload"
        >
          <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
        </el-select>
      </div>
      <el-upload
        accept=".csv,.xlsx"
        :show-file-list="false"
        :http-request="uploadRequest"
        :disabled="!ontologyId"
        class="obj-import__upload"
      >
        <el-button type="primary" :loading="uploading" :disabled="!ontologyId" data-test="import-upload">
          {{ upload ? '重新上传' : '上传文件' }}
        </el-button>
        <template #tip>
          <span v-if="upload" class="obj-import__file">
            已解析：{{ fileName }}（{{ upload.row_count }} 行）
          </span>
          <span v-else class="obj-import__hint">对象落库后来源标记为「导入」；整批校验，任一行错误不会导入任何数据。</span>
        </template>
      </el-upload>
    </el-card>

    <!-- 卡② 列映射：对象名列（必选）+ 每个属性定义选一个来源列 -->
    <el-card v-if="upload" class="obj-import__step" shadow="never">
      <template #header>
        <div class="kb-step">
          <span class="kb-step__no">2</span>列映射
          <span class="kb-step__hint">列名与属性名相同的已自动预选；未映射的属性留空</span>
        </div>
      </template>
      <div class="obj-import__name-row">
        <span class="obj-import__label">对象名列：</span>
        <el-select v-model="nameColumn" style="width: 240px" data-test="import-name-column">
          <el-option
            v-for="col in upload.columns"
            :key="col.name"
            :value="col.name"
            :label="withSample(col)"
          />
        </el-select>
      </div>
      <div v-if="attributeDefs.length" class="obj-import__attrs">
        <div
          v-for="def in attributeDefs"
          :key="def.id"
          class="obj-import__attr-row"
          :class="{ 'obj-import__attr-row--off': !attributeMapping[def.name] }"
        >
          <span class="obj-import__attr-name">{{ def.name }}</span>
          <span v-if="def.description" class="obj-import__attr-hint">{{ def.description }}</span>
          <el-select
            v-model="attributeMapping[def.name]"
            style="width: 240px"
            clearable
            placeholder="不导入"
            data-test="import-attr-column"
          >
            <el-option
              v-for="col in upload.columns"
              :key="col.name"
              :value="col.name"
              :label="withSample(col)"
            />
          </el-select>
        </div>
      </div>
      <p v-else class="obj-import__hint">该本体未定义属性——仅导入对象名称。</p>
      <el-table
        :data="upload.preview_rows"
        size="small"
        max-height="220"
        class="obj-import__preview"
      >
        <el-table-column
          v-for="col in upload.columns"
          :key="col.name"
          :prop="col.name"
          :label="col.name"
          min-width="110"
          show-overflow-tooltip
        >
          <template #default="{ row }">{{ formatCell(row[col.name]) }}</template>
        </el-table-column>
        <template #empty>文件无数据行</template>
      </el-table>
      <div class="obj-import__actions">
        <el-button
          type="primary"
          :loading="committing"
          :disabled="!nameColumn"
          data-test="import-commit"
          @click="runImport"
        >开始导入（{{ upload.row_count }} 行）</el-button>
      </div>
    </el-card>

    <!-- 卡③ 结果：成功计数 / 422 逐行报告（可导出 CSV，返回调整映射） -->
    <el-card v-if="result || report" class="obj-import__step" shadow="never">
      <template #header>
        <div class="kb-step"><span class="kb-step__no">3</span>导入结果</div>
      </template>
      <template v-if="result">
        <el-alert type="success" :closable="false" :title="`已导入 ${result.imported} 个对象（来源：导入）`" />
        <div class="obj-import__actions">
          <el-button data-test="import-reset" @click="resetWizard">再导入一批</el-button>
        </div>
      </template>
      <template v-else-if="report">
        <el-alert
          type="error"
          :closable="false"
          :title="`${report.message}（${report.error_rows}/${report.total_rows} 行有问题，未导入任何数据）`"
        />
        <el-table :data="report.errors" size="small" max-height="260" class="obj-import__errors">
          <el-table-column prop="row" label="行号" width="80" />
          <el-table-column label="错误类型" width="150">
            <template #default="{ row }">
              <el-tag size="small" type="danger" effect="plain">{{ codeLabel(row.code) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="180" show-overflow-tooltip />
        </el-table>
        <div class="obj-import__actions">
          <el-button data-test="import-export-csv" @click="exportErrors">导出错误报告 CSV</el-button>
          <el-button data-test="import-back" @click="report = null">返回调整映射</el-button>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import type {
  ImportColumnMeta, ImportCommitResult, ImportUploadResult,
  ImportValidationReport, Ontology, OntologyAttributeDef,
} from '@/types/ontology'

defineProps<{ ontologies: Ontology[] }>()

const emit = defineEmits<{ imported: [result: ImportCommitResult] }>()

const api = useOntologyApi()

const ontologyId = ref('')
const uploading = ref(false)
const committing = ref(false)
const upload = ref<ImportUploadResult | null>(null)
const fileName = ref('')
const attributeDefs = ref<OntologyAttributeDef[]>([])
/** 属性定义名 → 来源列名（'' = 不导入）；提交时反转为列名→属性名。 */
const attributeMapping = ref<Record<string, string>>({})
const nameColumn = ref('')
const result = ref<ImportCommitResult | null>(null)
const report = ref<ImportValidationReport | null>(null)

const _CODE_LABELS: Record<string, string> = {
  empty_name: '名称为空',
  invalid_name: '名称超长',
  duplicate_existing: '与库内重名',
  duplicate_in_batch: '批内重名',
}

function codeLabel(code: string): string {
  return _CODE_LABELS[code] ?? code
}

/** el-upload 自定义请求 + 测试直调入口。 */
async function uploadRequest(options: UploadRequestOptions): Promise<void> {
  await doUpload(options.file as File)
}

async function doUpload(file: File): Promise<void> {
  if (!ontologyId.value) {
    ElMessage.warning('请先选择目标本体')
    return
  }
  uploading.value = true
  try {
    const data = await api.uploadObjectImport(ontologyId.value, file)
    const detail = await api.getOntology(ontologyId.value)
    attributeDefs.value = detail.attributes
    upload.value = data
    fileName.value = file.name
    result.value = null
    report.value = null
    applyDetectedMapping(data)
  } catch (error) {
    ElMessage.error(`上传失败：${await apiErrorDetail(error)}`)
  } finally {
    uploading.value = false
  }
}

/** 应用后端自动预选：名称列同义词 + 列名与属性名精确匹配。 */
function applyDetectedMapping(data: ImportUploadResult): void {
  nameColumn.value = data.detected_mapping.name_column
  const detected = data.detected_mapping.attribute_columns
  const mapping: Record<string, string> = {}
  for (const def of attributeDefs.value) {
    const column = Object.entries(detected).find(([, attr]) => attr === def.name)
    mapping[def.name] = column ? column[0] : ''
  }
  attributeMapping.value = mapping
}

/** 提交负载的 attribute_columns：列名 → 属性定义名（空/空白选择剔除）。 */
function buildAttributeColumns(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [attribute, column] of Object.entries(attributeMapping.value)) {
    const target = column.trim()
    if (target) out[target] = attribute
  }
  return out
}

/** 422 结构化报告提取（detail={message, report}；apiErrorDetail 只出文案）。 */
function extractReport(error: unknown): ImportValidationReport | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'report' in detail) {
    const candidate = (detail as { report?: unknown }).report
    if (candidate && typeof candidate === 'object'
        && Array.isArray((candidate as ImportValidationReport).errors)) {
      return candidate as ImportValidationReport
    }
  }
  return null
}

async function runImport(): Promise<void> {
  if (!upload.value || !nameColumn.value) return
  committing.value = true
  try {
    const data = await api.commitObjectImport(ontologyId.value, {
      import_token: upload.value.import_token,
      name_column: nameColumn.value,
      attribute_columns: buildAttributeColumns(),
    })
    result.value = data
    report.value = null
    emit('imported', data)
    ElMessage.success(`导入完成：${data.imported} 个对象`)
  } catch (error) {
    const validation = extractReport(error)
    if (validation) {
      report.value = validation
    } else if ((error as { response?: { status?: number } })?.response?.status === 410) {
      ElMessage.error('导入会话已过期（1 小时），请重新上传文件')
      resetWizard()
    } else {
      ElMessage.error(`导入失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    committing.value = false
  }
}

/** 纯前端生成错误报告 CSV（BOM 头，Excel 直开不乱码）。 */
function exportErrors(): void {
  if (!report.value) return
  const escape = (value: string) => `"${value.replace(/"/g, '""')}"`
  const lines = [
    '行号,错误类型,说明',
    ...report.value.errors.map(e =>
                  `${e.row},${escape(codeLabel(e.code))},${escape(e.message)}`),
  ]
  const blob = new Blob([String.fromCharCode(0xfeff) + lines.join('\r\n')], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = '本体导入错误报告.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

function resetWizard(): void {
  upload.value = null
  fileName.value = ''
  attributeDefs.value = []
  attributeMapping.value = {}
  nameColumn.value = ''
  result.value = null
  report.value = null
}

function withSample(col: ImportColumnMeta): string {
  const sample = col.sample
  if (sample === null || sample === undefined || sample === '') return col.name
  const text = formatCell(sample)
  return text.length > 12 ? `${col.name}（${text.slice(0, 12)}…）` : `${col.name}（${text}）`
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

// 供 ObjectTab 抽屉挂载后父组件/测试直调（对齐 ArtifactImportWizard 模式）
defineExpose({
  ontologyId, upload, nameColumn, attributeMapping, result, report,
  doUpload, runImport, applyDetectedMapping, buildAttributeColumns,
  exportErrors, resetWizard,
})
</script>

<style scoped>
.obj-import { display: flex; flex-direction: column; gap: 12px; }
.obj-import__step :deep(.el-card__header) {
  padding: 10px 16px;
  background: var(--kb-bg-card-hover);
  border-bottom: 1px solid var(--kb-border-light);
}
.obj-import__step :deep(.el-card__body) { padding: 14px 16px; }
.obj-import__hint { font-size: 12px; color: var(--kb-text-tertiary); font-weight: normal; }
.obj-import__file { margin-left: 10px; font-size: 12.5px; color: var(--kb-text-tertiary); }
.obj-import__pick { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.obj-import__label { font-size: 13px; }
.obj-import__name-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.obj-import__attrs { border-top: 1px dashed var(--kb-border-light, #eee); padding-top: 6px; }
.obj-import__attr-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px dashed var(--kb-border-light, #eee); }
.obj-import__attr-row--off { opacity: 0.55; }
.obj-import__attr-name { min-width: 90px; font-size: 13px; }
.obj-import__attr-hint { flex: 1; font-size: 12px; color: var(--kb-text-tertiary); }
.obj-import__preview { margin-top: 10px; }
.obj-import__errors { margin-top: 10px; }
.obj-import__actions { margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px; }
</style>
