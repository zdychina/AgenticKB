<template>
  <div class="product-list">
    <div class="product-list__header">
      <h2 class="product-list__title">知识制品</h2>
      <el-button type="primary" @click="createVisible = true">新建制品</el-button>
    </div>

    <p class="product-list__hint">
      把 Agent 反复要做的查找、整理、对齐提前做完，形成可复用、可校准、可回源的成果。
      制品跟随当前知识域，切域即换一批。
    </p>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <el-table v-loading="loading" :data="products" class="product-list__table">
      <el-table-column label="名称" min-width="220">
        <template #default="{ row }">
          <router-link :to="`/products/${encodeURIComponent(row.id)}`" class="product-list__link">
            {{ row.name }}
          </router-link>
          <div class="product-list__id">{{ row.id }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="product_type" label="类型" width="170" />
      <el-table-column prop="owner" label="负责人" width="120" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="lifecycleTagType(row.lifecycle_status)" size="small" effect="light">
            {{ lifecycleLabel(row.lifecycle_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="草稿 / 发布" width="130">
        <template #default="{ row }">
          <span class="product-list__rev">
            {{ row.current_draft_revision ?? '—' }} / {{ row.released_revision ?? '—' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="purpose" label="用途" min-width="240" show-overflow-tooltip />
      <template #empty>
        <EmptyState text="当前知识域还没有知识制品" />
      </template>
    </el-table>

    <el-dialog v-model="createVisible" title="新建知识制品" width="640px">
      <el-form :model="form" label-width="104px" label-position="left">
        <el-form-item label="制品 ID" required>
          <el-input v-model="form.product_id" placeholder="英文 slug，同时是对象逻辑 ID 的首段" />
          <div class="product-list__field-hint">建好后不可更改——它会写进每个对象的 ID。</div>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="制品类型" required>
          <el-input v-model="form.product_type" placeholder="如 specification_table" />
        </el-form-item>
        <el-form-item label="用途">
          <el-input
            v-model="form.purpose" type="textarea" :rows="2"
            placeholder="服务什么业务任务或决策——发布门禁会检查这一项"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useKnowledgeProductApi } from '@/api/knowledgeProduct'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { lifecycleLabel, lifecycleTagType } from '@/views/product/productMeta'
import type { KnowledgeProduct } from '@/types/knowledgeProduct'

const api = useKnowledgeProductApi()
const router = useRouter()
const domain = useDomainStore()

const products = ref<KnowledgeProduct[]>([])
const loading = ref(false)
const error = ref('')
const createVisible = ref(false)
const creating = ref(false)

const form = reactive({ product_id: '', name: '', product_type: '', purpose: '' })

async function load() {
  loading.value = true
  error.value = ''
  try {
    products.value = await api.list()
  } catch (e) {
    error.value = await apiErrorDetail(e)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.product_id.trim() || !form.name.trim() || !form.product_type.trim()) {
    ElMessage.warning('制品 ID、名称、类型都要填')
    return
  }
  creating.value = true
  try {
    const created = await api.create({
      product_id: form.product_id.trim(),
      name: form.name.trim(),
      product_type: form.product_type.trim(),
      purpose: form.purpose.trim() || null,
    })
    createVisible.value = false
    router.push(`/products/${encodeURIComponent(created.id)}`)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    creating.value = false
  }
}

onMounted(load)
// 制品不跨知识域，切域就是换一批
watch(() => domain.currentDomain, load)
</script>

<style scoped>
.product-list__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.product-list__title {
  margin: 0;
  font-size: 20px;
}

.product-list__hint {
  margin: 8px 0 16px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.product-list__table {
  margin-top: 8px;
}

.product-list__link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.product-list__id,
.product-list__rev {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.product-list__field-hint {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
