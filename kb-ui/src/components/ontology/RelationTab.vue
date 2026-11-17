<template>
  <div class="rel-tab">
    <div class="rel-tab__toolbar">
      <p class="rel-tab__tip">关系类型 = 库内有名有向边（源本体 → 目标本体），语义关系创建时按端点强校验。</p>
      <el-button
        v-if="canWrite"
        type="primary"
        :disabled="ontologies.length < 1"
        data-test="create-relation"
        @click="openCreate"
      >新建关系类型</el-button>
    </div>

    <el-table :data="relations">
      <el-table-column prop="name" label="名称" width="160">
        <template #default="{ row }">
          <span class="rel-tab__name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="方向" min-width="240">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.source_ontology_name }}</el-tag>
          <span class="rel-tab__arrow">→</span>
          <el-tag size="small" effect="plain" type="success">{{ row.target_ontology_name }}</el-tag>
          <el-tag v-if="row.source_ontology_id === row.target_ontology_id" size="small" type="warning" effect="plain">自环</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column label="实例数" width="90" align="center">
        <template #default="{ row }">{{ row.instance_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="130" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState :text="ontologies.length < 1
          ? '先在「本体」Tab 创建至少一个本体，才能定义关系类型'
          : '还没有关系类型——定义边类型后即可在对象之间建立语义关系'" />
      </template>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑关系类型' : '新建关系类型'" width="500px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：治疗、认识" maxlength="80" data-test="relation-name" />
        </el-form-item>
        <el-form-item label="源本体">
          <el-select v-model="form.sourceOntologyId" placeholder="选择源本体" style="width: 100%" data-test="relation-source">
            <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标本体">
          <el-select v-model="form.targetOntologyId" placeholder="选择目标本体" style="width: 100%" data-test="relation-target">
            <el-option v-for="o in ontologies" :key="o.id" :value="o.id" :label="o.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="这条边表达什么语义（可选）" />
        </el-form-item>
      </el-form>
      <p class="rel-tab__hint">源=目标 时为自环类型（如「人物—认识—人物」）；库内名称唯一；改端点在已有实例时会被拒绝。</p>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="submit-relation" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import type { Ontology, OntologyRelation } from '@/types/ontology'

const props = defineProps<{
  libraryId: string
  canWrite: boolean
  ontologies: Ontology[]
  relations: OntologyRelation[]
}>()

const emit = defineEmits<{ refresh: [] }>()

const api = useOntologyApi()

const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref('')
const form = ref({ name: '', sourceOntologyId: '', targetOntologyId: '', description: '' })

function openCreate() {
  editingId.value = ''
  form.value = { name: '', sourceOntologyId: '', targetOntologyId: '', description: '' }
  dialogVisible.value = true
}

function openEdit(row: OntologyRelation) {
  editingId.value = row.id
  form.value = {
    name: row.name,
    sourceOntologyId: row.source_ontology_id,
    targetOntologyId: row.target_ontology_id,
    description: row.description || '',
  }
  dialogVisible.value = true
}

async function save() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!form.value.sourceOntologyId || !form.value.targetOntologyId) {
    ElMessage.warning('请选择源本体与目标本体')
    return
  }
  saving.value = true
  try {
    const payload = {
      name,
      description: form.value.description.trim() || undefined,
      source_ontology_id: form.value.sourceOntologyId,
      target_ontology_id: form.value.targetOntologyId,
    }
    if (editingId.value) {
      await api.updateRelation(editingId.value, payload)
    } else {
      await api.createRelation(props.libraryId, payload)
    }
    dialogVisible.value = false
    ElMessage.success('已保存')
    emit('refresh')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove(row: OntologyRelation) {
  try {
    await ElMessageBox.confirm(
      `确定删除关系类型「${row.name}」？已有 ${row.instance_count ?? 0} 条实例时会阻止删除。`,
      { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteRelation(row.id)
    ElMessage.success('已删除')
    emit('refresh')
  } catch (error) {
    ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
  }
}
</script>

<style scoped>
.rel-tab__toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 10px; }
.rel-tab__tip { margin: 0; color: var(--kb-text-tertiary); font-size: 13px; }
.rel-tab__name { font-weight: 650; }
.rel-tab__arrow { margin: 0 6px; color: var(--kb-text-tertiary); }
.rel-tab__hint { color: var(--kb-text-tertiary); font-size: 12px; margin: 4px 0 0 80px; }
</style>
