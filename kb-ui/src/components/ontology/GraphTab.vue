<template>
  <div class="graph-tab">
    <div class="graph-tab__toolbar">
      <div class="graph-tab__legend">
        <button
          v-for="chip in legendChips"
          :key="chip.id"
          type="button"
          class="graph-tab__chip"
          :class="{ 'is-off': chip.off }"
          :title="chip.off ? '显示该本体的对象' : '隐藏该本体的对象'"
          @click="toggleOntology(chip.id)"
        >
          <span class="graph-tab__dot" :style="{ background: chip.color }" />
          <span class="graph-tab__name">{{ chip.name }}</span>
          <span class="graph-tab__count" :class="{ 'is-zero': chip.count === 0 }">{{ chip.count }}</span>
        </button>
        <span v-if="legendChips.length === 0" class="graph-tab__hint">库内还没有本体</span>
      </div>
      <div class="graph-tab__actions">
        <el-input
          v-model="keyword"
          class="graph-tab__search"
          placeholder="对象名关键字：命中高亮"
          clearable
          :prefix-icon="Search"
          data-test="graph-keyword"
          @keyup.enter="renderGraph"
          @clear="renderGraph"
        />
        <el-button data-test="graph-refresh" @click="load"><el-icon class="graph-tab__btn-icon"><Refresh /></el-icon>刷新</el-button>
        <el-button data-test="graph-fit" @click="fitView"><el-icon class="graph-tab__btn-icon"><Aim /></el-icon>适应画布</el-button>
        <el-button data-test="graph-export" @click="exportPng"><el-icon class="graph-tab__btn-icon"><Download /></el-icon>导出 PNG</el-button>
      </div>
    </div>

    <div v-if="graphData?.truncated" class="graph-tab__alert" data-test="graph-truncated">
      对象过多，已截断至 {{ graphData.nodes.length }} / {{ graphData.total_objects }}——用上方图例缩小范围。
    </div>

    <div v-if="libraryEmpty" class="graph-tab__empty">
      <EmptyState text="还没有语义对象——到「语义对象」页手工创建、批量导入或 AI 抽取后，这里会画出实例图" />
    </div>
    <template v-else>
      <div ref="containerRef" class="graph-tab__canvas" data-test="graph-canvas" />
      <p v-if="allToggledOff" class="graph-tab__hint graph-tab__center-hint">
        已隐藏全部本体——点上方图例色块恢复
      </p>
    </template>

    <el-drawer v-model="drawerVisible" title="对象详情" size="420px">
      <div v-loading="drawerLoading" class="graph-tab__drawer">
        <template v-if="drawerDetail">
          <h3 class="graph-tab__drawer-title">{{ drawerDetail.name }}</h3>
          <el-tag size="small" effect="plain">{{ drawerDetail.ontology_name }}</el-tag>
          <!-- 69号溯源：extract 对象带来源文档（与对象 Tab 来源列同口径） -->
          <p v-if="drawerDetail.source === 'extract' && drawerDetail.source_document_id" class="graph-tab__provenance">
            AI 抽取 ·
            <button
              v-if="drawerDetail.source_document_name && drawerDetail.source_kb_id"
              class="kb-link graph-tab__doc-link"
              type="button"
              @click="openSourceDocument"
            >{{ drawerDetail.source_document_name }}</button>
            <span v-else>来源文档已不可访问</span>
          </p>
          <table v-if="drawerDetail.attribute_defs.length" class="graph-tab__attrs">
            <tr v-for="def in drawerDetail.attribute_defs" :key="def.id">
              <th>{{ def.name }}</th>
              <td>{{ formatValue(drawerDetail.attributes_json?.[def.name]) }}</td>
            </tr>
          </table>
          <p v-else class="graph-tab__hint">该本体没有属性定义</p>
          <h4 class="kb-section-title graph-tab__drawer-sub">出边（→）</h4>
          <NeighborList
            :edges="drawerNeighbors.outgoing"
            direction-label="→"
            :can-write="false"
            @jump="openDrawer"
          />
          <h4 class="kb-section-title graph-tab__drawer-sub">入边（←）</h4>
          <NeighborList
            :edges="drawerNeighbors.incoming"
            direction-label="←"
            :can-write="false"
            @jump="openDrawer"
          />
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Graph } from '@antv/g6'
import { Aim, Download, Refresh, Search } from '@element-plus/icons-vue'
import type { EdgeData, IElementEvent, NodeData } from '@antv/g6'
import { useOntologyApi } from '@/api/ontology'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import NeighborList from '@/components/ontology/NeighborList.vue'
import type {
  GraphEdge, GraphNode, LibraryGraph, ObjectNeighbors, Ontology, OntologyObjectDetail,
} from '@/types/ontology'

// Boolean prop 缺省会被 Vue cast 成 false（而非 undefined）：显式 default true，
// 这样未传 active 的挂载方（测试/未来复用）默认即可渲染
const props = withDefaults(defineProps<{
  libraryId: string
  /** 父级已加载的本体清单：图例与着色顺序直接用，不重复拉 schema。 */
  ontologies: Ontology[]
  /** Tab 是否激活：隐藏时画布尺寸为 0，渲染/自适应须推迟到激活后。 */
  active?: boolean
  /** 69号 §4.3：外链聚焦对象 id（检索面板卡组跳入）；图渲染后一次性消费。 */
  focus?: string
}>(), { active: true, focus: '' })

const api = useOntologyApi()

/** 12 色循环：本体超 12 个时从头复用（色区分度有限，属可接受降级）。 */
const PALETTE = [
  '#0891b2', '#2563eb', '#16a34a', '#d97706', '#dc2626', '#7c3aed',
  '#db2777', '#65a30d', '#ea580c', '#6366f1', '#b45309', '#475569',
]

const loading = ref(false)
const graphData = ref<LibraryGraph | null>(null)
const keyword = ref('')
const disabled = ref<Set<string>>(new Set())
const containerRef = ref<HTMLElement>()

/** 首次 load 完成前不响应 props.ontologies 变化（那是父级初始加载，不是写后刷新）。 */
let loadedOnce = false
let graph: Graph | null = null
/** 并发防护：load / openDrawer 各自只认最新一次请求的响应。 */
let loadSeq = 0
let drawerSeq = 0

const active = computed(() => props.active !== false)
const libraryEmpty = computed(() =>
  graphData.value !== null && graphData.value.nodes.length === 0)
const allToggledOff = computed(() => {
  const g = graphData.value
  return g !== null && g.nodes.length > 0
    && g.nodes.every(n => disabled.value.has(n.ontology_id))
})

const legendChips = computed(() => {
  const counts = new Map<string, number>()
  for (const n of graphData.value?.nodes ?? []) {
    counts.set(n.ontology_id, (counts.get(n.ontology_id) ?? 0) + 1)
  }
  const colors = buildColorMap()
  return props.ontologies.map(o => ({
    id: o.id,
    name: o.name,
    color: colors.get(o.id) ?? PALETTE[0],
    count: counts.get(o.id) ?? 0,
    off: disabled.value.has(o.id),
  }))
})

/** 本体 → 颜色：props.ontologies 顺序优先；图里出现而清单没有的本体按出现序补位。 */
function buildColorMap(): Map<string, string> {
  const ids = props.ontologies.map(o => o.id)
  for (const n of graphData.value?.nodes ?? []) {
    if (!ids.includes(n.ontology_id)) ids.push(n.ontology_id)
  }
  return new Map(ids.map((id, i) => [id, PALETTE[i % PALETTE.length]]))
}

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const data = await api.getLibraryGraph(props.libraryId)
    if (seq !== loadSeq) return  // 已有更新的一次 load 在途，丢弃旧响应
    graphData.value = data
    loadedOnce = true
    await nextTick()  // 空态切换后容器可能刚挂载/卸载，等 DOM 稳定再画
    await renderGraph()
    await maybeConsumeFocus()  // 69号：外链聚焦等图渲染完成后再消费
  } catch (error) {
    ElMessage.error(`加载图谱失败：${await apiErrorDetail(error)}`)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function destroyGraph() {
  graph?.destroy()
  graph = null
}

function ensureGraph(): Graph | null {
  if (graph) return graph
  if (!containerRef.value || libraryEmpty.value) return null
  graph = new Graph({
    container: containerRef.value,
    autoFit: 'view',
    animation: false,
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
    layout: { type: 'force', linkDistance: 110 },
    node: {
      style: {
        size: 34,
        fill: (d: NodeData): string => String(d.data?.color ?? PALETTE[0]),
        stroke: (d: NodeData): string => (d.data?.focus ? '#0891b2' : '#fff'),
        lineWidth: (d: NodeData): number => (d.data?.focus ? 3.5 : 2),
        labelText: (d: NodeData): string =>
          d.data?.dim ? '' : String(d.data?.name ?? d.id ?? ''),
        labelPlacement: 'bottom',
        labelFontSize: 12,
        labelFill: '#334155',
        fillOpacity: (d: NodeData): number => (d.data?.dim ? 0.12 : 1),
      },
    },
    edge: {
      style: {
        stroke: '#94a3b8',
        endArrow: true,
        labelText: (d: EdgeData): string => String(d.data?.relation_name ?? ''),
        labelFontSize: 11,
        labelFill: '#64748b',
        labelBackground: true,
      },
    },
  })
  graph.on('node:click', (event: IElementEvent) => {
    const id = (event.target as { id?: unknown }).id
    if (typeof id === 'string') void openDrawer(id)
  })
  return graph
}

/** 过滤（本体开关）+ 高亮（关键字/外链聚焦）→ G6 数据。纯映射，不碰实例。 */
function toDisplayData() {
  const g = graphData.value
  if (!g) return { nodes: [], edges: [] }
  const colors = buildColorMap()
  const kw = keyword.value.trim()
  const nodes = g.nodes
    .filter(n => !disabled.value.has(n.ontology_id))
    .map((n: GraphNode) => ({
      id: n.id,
      data: {
        name: n.name,
        color: colors.get(n.ontology_id) ?? PALETTE[0],
        dim: !!kw && !n.name.includes(kw),
        focus: n.id === focusId.value,
      },
    }))
  const ids = new Set(nodes.map(n => n.id))
  const edges = g.edges
    .filter((e: GraphEdge) => ids.has(e.source) && ids.has(e.target))
    .map(e => ({
      id: e.id, source: e.source, target: e.target,
      data: { relation_name: e.relation_name },
    }))
  return { nodes, edges }
}

async function renderGraph() {
  // Tab 隐藏（display:none）时画布尺寸为 0：布局/自适应会产生错误视口，
  // 推迟到激活时补渲染（见 active watch）
  if (!active.value) return
  // 空库时画布 div 随空态切换被卸载：实例若不回收，之后再有数据会仍绑在
  // 已脱离 DOM 的旧容器上（图谱隐形）——跟着销毁，重建时自然重绑新容器
  if (libraryEmpty.value) {
    destroyGraph()
    return
  }
  const g = ensureGraph()
  if (!g) return
  try {
    g.setData(toDisplayData())
    await g.render()
  } catch (error) {
    ElMessage.error(`图谱渲染失败：${await apiErrorDetail(error)}`)
  }
}

function toggleOntology(ontologyId: string) {
  const next = new Set(disabled.value)
  if (next.has(ontologyId)) next.delete(ontologyId)
  else next.add(ontologyId)
  disabled.value = next
  void renderGraph()
}

async function fitView() {
  await graph?.fitView()
}

async function exportPng() {
  if (!graph) return
  const url = await graph.toDataURL()
  const link = document.createElement('a')
  link.href = url
  link.download = '本体图谱.png'
  link.click()
}

// ── 节点点击抽屉：详情 + 一度邻接（复用 NeighborList）──

const drawerVisible = ref(false)
const drawerLoading = ref(false)
const drawerDetail = ref<OntologyObjectDetail | null>(null)
const drawerNeighbors = ref<ObjectNeighbors>({ outgoing: [], incoming: [] })
const router = useRouter()
const route = useRoute()

/** 69号溯源：抽屉里跳来源文档预览（文档已删时不渲染按钮，走降级文案）。 */
function openSourceDocument() {
  const detail = drawerDetail.value
  if (!detail?.source_document_id || !detail.source_kb_id) return
  void router.push({
    name: 'kb-doc-preview',
    params: { kbId: detail.source_kb_id, docId: detail.source_document_id },
  })
}

async function openDrawer(objectId: string) {
  const seq = ++drawerSeq
  drawerVisible.value = true
  drawerLoading.value = true
  try {
    const [detail, neighbors] = await Promise.all([
      api.getObject(objectId), api.getObjectNeighbors(objectId),
    ])
    if (seq !== drawerSeq) return  // 期间又点了别的节点：晚到的旧响应丢弃
    drawerDetail.value = detail
    drawerNeighbors.value = neighbors
  } catch (error) {
    if (seq === drawerSeq) {
      ElMessage.error(`加载对象详情失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    if (seq === drawerSeq) drawerLoading.value = false
  }
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// ── 外链聚焦（69号 §4.3）：检索面板卡组 → tab=graph&focus=<object_id> ──

const focusId = ref('')
/** 同一 focus 只消费一次：load 重跑/手动刷新不重复定位。 */
let consumedFocus = ''

/**
 * 消费 props.focus：定位 + 描边强调（不开抽屉）。节点被本体过滤排除 →
 * 自动清过滤再定位；库超限截断未含该节点 → 带该对象的本体重拉一次图。
 */
async function maybeConsumeFocus() {
  const target = props.focus
  // 首次数据未就绪时搁置（load 收尾会再调一次）
  if (!target || target === consumedFocus || !loadedOnce || !graphData.value) return
  consumedFocus = target
  focusId.value = target
  try {
    // 先把描边态画进画布（focusElement 只移视口，不改样式）
    await renderGraph()
    const node = graphData.value.nodes.find(n => n.id === target)
    if (node) {
      await locateNode(node.ontology_id, target)
    } else if (graphData.value.truncated) {
      // 截断兜底：图里没有 ≠ 不存在——取对象所属本体，带过滤重拉再定位
      try {
        const detail = await api.getObject(target)
        const data = await api.getLibraryGraph(props.libraryId, {
          ontology_ids: [detail.ontology_id],
        })
        graphData.value = data
        await nextTick()
        await renderGraph()
        const found = data.nodes.find(n => n.id === target)
        if (found) {
          await locateNode(found.ontology_id, target)
        } else {
          ElMessage.info('对象存在，但当前图已截断——请用图例缩小范围后重试')
        }
      } catch (error) {
        focusId.value = ''
        ElMessage.error(`定位失败：${await apiErrorDetail(error)}`)
      }
    } else {
      // 图未截断还没有 → 对象不在本库图里（已删/换库）
      focusId.value = ''
      ElMessage.info('未在当前库图谱中找到该对象（可能已被删除）')
    }
  } finally {
    clearFocusQuery()
  }
}

async function locateNode(ontologyId: string, target: string) {
  if (disabled.value.has(ontologyId)) {
    const next = new Set(disabled.value)
    next.delete(ontologyId)
    disabled.value = next
    await renderGraph()
  }
  try {
    await graph?.focusElement(target)
  } catch {
    // 元素不在画布上（理论上不会走到）：描边仍在，静默
  }
}

/** 消费后清掉 URL 的 focus 键：刷新不重复跳 tab/定位。 */
function clearFocusQuery() {
  const current = route.query.focus
  if (typeof current !== 'string' || !current) return
  const query = { ...route.query }
  delete query.focus
  void router.replace({ query })
}

onMounted(load)
// 父级任何写操作后 refreshAll 会替换 ontologies 引用——借此保持跨 Tab 新鲜
// （本体/对象页建了东西，切回图谱不用手点刷新）
watch(() => props.ontologies, () => {
  if (loadedOnce) void load()
})
// 隐藏期间数据已换（renderGraph 被搁置）：激活时补一次渲染
watch(() => props.active, (now, was) => {
  if (now && !was) void renderGraph()
})
// 外链 focus 在页面已打开时再次跳入（同库不同对象）：渲染完成后消费
watch(() => props.focus, () => { void maybeConsumeFocus() })
onBeforeUnmount(destroyGraph)
</script>

<style scoped>
.graph-tab { padding: 4px; }
.graph-tab__toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.graph-tab__btn-icon { margin-right: 4px; }

/* ── 图例色块：胶囊 + 计数徽章，关闭态整体退隐 ── */
.graph-tab__legend { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

.graph-tab__chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--kb-border);
  background: var(--kb-bg-card);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--kb-text-primary, #0f172a);
  transition: border-color var(--kb-duration) var(--kb-ease),
    box-shadow var(--kb-duration) var(--kb-ease);
}

.graph-tab__chip:hover { border-color: var(--kb-accent); box-shadow: var(--kb-shadow-xs); }
.graph-tab__chip.is-off { opacity: 0.45; }
.graph-tab__chip.is-off .graph-tab__name { text-decoration: line-through; }

.graph-tab__dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }

.graph-tab__count {
  min-width: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--kb-bg-page);
  color: var(--kb-text-secondary);
  text-align: center;
  font-variant-numeric: tabular-nums;
  line-height: 16px;
}

.graph-tab__count.is-zero { color: var(--kb-text-tertiary); }

.graph-tab__actions { display: flex; align-items: center; gap: 8px; }
.graph-tab__search { width: 210px; }

/* 截断提示走告警令牌，与全站告警条同语言 */
.graph-tab__alert {
  border: 1px solid rgba(245, 158, 11, 0.4);
  background: var(--kb-warning-soft);
  color: #92400e;
  border-radius: var(--kb-radius-sm);
  padding: 6px 12px;
  font-size: 13px;
  margin-bottom: 8px;
}

/* ── 画布：点阵网格底，图谱「图纸」感 ── */
.graph-tab__canvas {
  height: 560px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius);
  background-color: var(--kb-bg-card-hover);
  background-image: radial-gradient(circle, #cbd5e1 1px, transparent 1px);
  background-size: 22px 22px;
}

.graph-tab__empty { padding: 40px 0; }
.graph-tab__hint { color: var(--kb-text-tertiary); font-size: 13px; }
.graph-tab__center-hint { text-align: center; margin-top: 8px; }
.graph-tab__drawer-title { margin: 0 0 6px; font-size: 16px; }
.graph-tab__drawer-sub { margin-top: 18px; }
/* 69号溯源：抽屉来源文档行（本体 tag 下方） */
.graph-tab__provenance { margin: 8px 0 0; font-size: 12px; color: var(--kb-text-tertiary); }
.graph-tab__doc-link { font-weight: 500; }
.graph-tab__attrs { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
.graph-tab__attrs th { text-align: left; font-weight: 600; width: 40%; padding: 4px 8px 4px 0; color: var(--kb-text-secondary); vertical-align: top; }
.graph-tab__attrs td { padding: 4px 0; word-break: break-all; }
</style>
