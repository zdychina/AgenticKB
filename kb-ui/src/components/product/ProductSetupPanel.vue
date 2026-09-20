<template>
  <div class="ps">
    <!-- 资料告警：48号 §八，不能只挂待办继续暴露 -->
    <el-alert
      v-if="report && report.alerts.length"
      :type="report.blocking ? 'error' : 'warning'"
      show-icon :closable="false" class="ps__alert"
      :title="report.blocking
        ? '本制品引用的部分资料已失效或被收回'
        : '本制品引用的部分资料已更新或标记废弃'"
    >
      <p class="ps__alert-hint">
        {{ report.blocking
          ? '这份内容不应再被当作依据。请更新资料并重做，或下架该制品。'
          : '旧依据是否仍适用，由负责人按业务范围判断。' }}
      </p>
      <el-table :data="report.alerts" size="small" class="ps__alert-table">
        <el-table-column label="类别" width="120">
          <template #default="{ row }">
            <el-tag :type="alertTagType(row.kind)" size="small" effect="light">
              {{ alertLabel(row.kind) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_id" label="文档" min-width="160" show-overflow-tooltip />
        <el-table-column prop="detail" label="说明" min-width="240" show-overflow-tooltip />
        <el-table-column label="受影响字段" width="110">
          <template #default="{ row }">
            <el-button size="small" text @click="showAffected(row)">
              {{ row.affected_count }} 处
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-alert>

    <div class="ps__head">
      <div>
        <h4 class="ps__title">制品定义</h4>
        <p class="ps__hint">
          当前第 {{ definition?.definition_revision ?? '—' }} 版
          <template v-if="definition">· {{ definition.created_by }} 于 {{ definition.created_at }}</template>
        </p>
      </div>
      <div>
        <el-button v-if="!editing" size="small" :disabled="!definition" @click="startEdit">
          修改定义
        </el-button>
        <template v-else>
          <el-button size="small" @click="editing = false">取消</el-button>
          <el-button size="small" type="primary" :loading="saving" @click="save">
            保存为新一版
          </el-button>
        </template>
      </div>
    </div>

    <el-alert v-if="editing" type="warning" show-icon :closable="false" class="ps__notice">
      改定义会**开新的一版**（旧版留着，已发布修订仍指向它），并**立即撤销该制品
      在用的全部任务票据**——Agent 手里的旧票据绑定旧定义，按旧范围继续提交就等于
      绕过了这次改动。改完需要重新起制作。
    </el-alert>

    <div v-loading="loading" class="ps__sections">
      <section v-for="section in SECTIONS" :key="section.key" class="ps__section">
        <div class="ps__section-head">
          <span class="ps__section-title">{{ section.title }}</span>
          <span class="ps__section-desc">{{ section.desc }}</span>
        </div>
        <YamlEditor v-if="editing" v-model="draft[section.key]" />
        <pre v-else class="ps__yaml">{{ draft[section.key] || '（空）' }}</pre>
      </section>
    </div>

    <el-dialog v-model="affectedVisible" title="受影响的字段" width="720px">
      <el-table :data="affected" size="small">
        <el-table-column prop="object_id" label="对象" min-width="240" show-overflow-tooltip />
        <el-table-column prop="field" label="字段" width="140" />
        <el-table-column prop="snapshot_id" label="快照" width="160" />
      </el-table>
      <p v-if="affectedTruncated" class="ps__hint">
        仅列前 {{ affected.length }} 条——列出哪些字段受影响是为了让人判断，不是为了倒全量。
      </p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import yaml from 'js-yaml'
import YamlEditor from '@/components/common/YamlEditor.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import { alertLabel, alertTagType } from '@/views/product/productMeta'
import type {
  AffectedField, KnowledgeProduct, ProductDefinition, SourceAlert, SourceAlertReport,
} from '@/types/knowledgeProduct'

const props = defineProps<{ product: KnowledgeProduct; active: boolean }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const api = useKnowledgeProductApi()

type SectionKey = 'fields' | 'object_rules' | 'examples' | 'trial_questions'

const SECTIONS: Array<{ key: SectionKey; title: string; desc: string }> = [
  { key: 'fields', title: '字段定义', desc: '这类对象卡必须有哪些业务字段；required 的字段会进发布门禁的「能回源」那一条' },
  { key: 'object_rules', title: '对象规则', desc: '一行代表什么、怎么去重、单位怎么留、冲突怎么处理' },
  { key: 'examples', title: '人工样例', desc: '至少一条已人工确认的，让 Agent 按真实标准整理' },
  { key: 'trial_questions', title: '试用问题', desc: '发布前的检验题——不只看表格好不好看' },
]

const definition = ref<ProductDefinition | null>(null)
const report = ref<SourceAlertReport | null>(null)
const loading = ref(false)
const editing = ref(false)
const saving = ref(false)

const draft = reactive<Record<SectionKey, string>>({
  fields: '', object_rules: '', examples: '', trial_questions: '',
})

const affected = ref<AffectedField[]>([])
const affectedTruncated = ref(false)
const affectedVisible = ref(false)

function dump(value: unknown): string {
  if (value == null) return ''
  if (Array.isArray(value) && !value.length) return ''
  if (typeof value === 'object' && !Object.keys(value as object).length) return ''
  return yaml.dump(value, { indent: 2, lineWidth: 100, noRefs: true })
}

function fill(source: ProductDefinition) {
  draft.fields = dump(source.fields_json)
  draft.object_rules = dump(source.object_rules_json)
  draft.examples = dump(source.examples_json)
  draft.trial_questions = dump(source.trial_questions_json)
}

async function reload() {
  loading.value = true
  try {
    definition.value = await api.getDefinition(props.product.id)
    fill(definition.value)
  } catch (e) {
    // 还没有定义修订是正常的（刚建的制品），不弹错
    definition.value = null
  }
  try {
    report.value = await api.sourceAlerts(props.product.id)
  } catch {
    report.value = null
  } finally {
    loading.value = false
  }
}

function startEdit() {
  if (definition.value) fill(definition.value)
  editing.value = true
}

function parse(key: SectionKey, fallback: unknown): unknown {
  const text = draft[key].trim()
  if (!text) return fallback
  const parsed = yaml.load(text)
  if (parsed == null) return fallback
  return parsed
}

async function save() {
  let body
  try {
    body = {
      fields: parse('fields', {}) as Record<string, unknown>,
      object_rules: parse('object_rules', {}) as Record<string, unknown>,
      examples: parse('examples', []) as unknown[],
      trial_questions: parse('trial_questions', []) as unknown[],
    }
  } catch (e) {
    ElMessage.error(`YAML 解析失败：${e instanceof Error ? e.message : e}`)
    return
  }

  try {
    await ElMessageBox.confirm(
      '保存会开新的一版定义，并立即撤销该制品在用的全部任务票据。进行中的制作会无法继续提交，需要重新起制作。',
      '确认修改定义',
      { type: 'warning', confirmButtonText: '保存并撤票', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }

  saving.value = true
  try {
    const result = await api.updateDefinition(props.product.id, body)
    editing.value = false
    ElMessage.success(
      `已保存为第 ${result.definition.definition_revision} 版` +
      (result.revoked_tickets ? `，撤销了 ${result.revoked_tickets} 张票据` : ''),
    )
    await reload()
    emit('changed')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    saving.value = false
  }
}

function showAffected(alert: SourceAlert) {
  affected.value = alert.affected
  affectedTruncated.value = alert.affected_count > alert.affected.length
  affectedVisible.value = true
}

watch(() => props.active, (on) => { if (on) reload() }, { immediate: true })
watch(() => props.product.released_revision, () => { if (props.active) reload() })
</script>

<style scoped>
.ps__alert {
  margin-bottom: 16px;
}

.ps__alert-hint {
  margin: 4px 0 8px;
  font-size: 12px;
}

.ps__alert-table {
  background: transparent;
}

.ps__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.ps__title {
  margin: 0 0 4px;
  font-size: 14px;
}

.ps__hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ps__notice {
  margin: 12px 0;
}

.ps__sections {
  margin-top: 12px;
  display: grid;
  gap: 16px;
}

.ps__section-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
}

.ps__section-title {
  font-size: 13px;
}

.ps__section-desc {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ps__yaml {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
}
</style>
