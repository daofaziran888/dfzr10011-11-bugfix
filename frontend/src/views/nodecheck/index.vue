<template>
  <section class="page" data-module="nodecheck">
    <header class="page-head">
      <div>
        <h2>节点状态巡检管理</h2>
        <p class="page-desc">
          一条采样只归属一个真实节点；列表、详情、定位与处置使用同一身份，按集群节点定位时只看到该节点自己的采样和处置。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记巡检采样</button>
        <button class="btn" type="button" @click="exportRows">导出节点状态巡检清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label class="filter-item">
        <span>记录编号 / 节点编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号或节点编号检索" />
      </label>
      <label class="filter-item">
        <span>巡检状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>真实节点（节点ID）</span>
        <select v-model="filters.nodeId">
          <option value="">全部节点</option>
          <option v-for="node in nodes" :key="node.id" :value="String(node.id)">
            {{ node['节点编号'] }}（#{{ node.id }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>每页条数</span>
        <select v-model.number="pageSize">
          <option :value="5">5</option>
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="50">50</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="policy-bar">
      <span class="policy-label">编号冲突处理</span>
      <button
        v-for="option in policyOptions"
        :key="option.mode"
        type="button"
        class="btn"
        :class="{ primary: policyMode === option.mode }"
        @click="switchPolicy(option.mode)"
      >
        {{ option.name }}
      </button>
      <span class="policy-hint">{{ currentPolicyDescription }}</span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>序号</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>巡检状态</th>
          <th>处置状态</th>
          <th>定位</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="{ 'row-active': String(row.id) === String(activeEntryId) }"
        >
          <td>{{ row['列表序号'] }}</td>
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td>
            <span class="tag" :class="row['abnormal'] ? 'conflict' : 'done'">{{ row['status'] }}</span>
          </td>
          <td>
            <span :class="row['处置状态'] === '已处置' ? 'tag done' : 'tag pending'">
              {{ row['处置状态'] ?? '待处置' }}
            </span>
            <span v-if="row['归属冲突']" class="tag conflict">编号冲突</span>
          </td>
          <td><button class="link" type="button" @click="openDetail(row)">查看详情</button></td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="row['归档']"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 5" class="empty-state">暂无节点状态巡检数据，可先登记巡检采样</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条巡检记录 · 第 {{ page }} / {{ totalPages }} 页 · 定位：{{ locateLabel }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <nav class="pager">
      <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
      <button
        v-for="item in pageButtons"
        :key="item"
        class="btn"
        :class="{ primary: item === page }"
        type="button"
        @click="goPage(item)"
      >
        {{ item }}
      </button>
      <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
    </nav>

    <aside v-if="detail" class="detail-panel" data-role="nodecheck-detail">
      <div class="panel-head">
        <h3>巡检详情 · {{ detail['记录编号'] }}</h3>
        <button class="btn ghost" type="button" @click="closeDetail">返回列表</button>
      </div>
      <dl class="detail-grid">
        <template v-for="field in detailFields" :key="field">
          <dt>{{ field }}</dt>
          <dd>{{ formatCell(detail[field]) }}</dd>
        </template>
      </dl>
      <p v-if="detail['编号切换说明']" class="switch-note">{{ detail['编号切换说明'] }}</p>
      <h4>该记录的处置记录（{{ detailDispositions.length }}）</h4>
      <ul class="detail-list">
        <li v-for="record in detailDispositions" :key="String(record.id)">
          {{ record['处置编号'] }} · {{ record['动作'] }} · {{ record['处置结果'] }} · {{ record['处置时间'] }}
        </li>
        <li v-if="!detailDispositions.length" class="empty-state">该记录还没有处置动作</li>
      </ul>
      <p class="detail-hint">
        列表第 {{ detail['定位']?.['列表序号'] }} 条（第 {{ detail['定位']?.['页码'] }} 页第
        {{ detail['定位']?.['页内位置'] }} 位）。定位与详情指向同一条记录，返回列表后按序号与记录编号核对。
      </p>
    </aside>

    <form v-if="createPanel" class="create-panel" @submit.prevent="submitSample">
      <div class="panel-head">
        <h3>登记巡检采样</h3>
        <button class="btn ghost" type="button" @click="createPanel = false">取消</button>
      </div>
      <label class="filter-item">
        <span>节点（真实节点）</span>
        <select v-model="draft.nodeId">
          <option value="">请选择集群节点</option>
          <option v-for="node in nodes" :key="node.id" :value="String(node.id)">
            {{ node['节点编号'] }}（#{{ node.id }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="draft.recordCode" placeholder="例如 NODE-0006" />
      </label>
      <label class="filter-item">
        <span>CPU使用率</span>
        <input v-model="draft.cpu" placeholder="例如 62" />
      </label>
      <label class="filter-item">
        <span>采样批次</span>
        <input v-model="draft.batch" placeholder="例如 BATCH-0003" />
      </label>
      <label class="filter-item">
        <span>采样序号</span>
        <input v-model="draft.seq" placeholder="例如 1" />
      </label>
      <button class="btn primary" type="submit">提交采样</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, any>
type DetailPayload = Row & { 记录?: Row; 处置记录?: Row[] }

const ENDPOINT = '/api/nodecheck'
const columns = ['记录编号', '节点编号', '采样节点编号', 'CPU使用率', '内存使用率', '磁盘使用率', '网络延迟', '错误次数', '采样时间']
const actions = ['标记异常', '隔离节点', '恢复节点']
const statuses = ['正常', '亚健康', '异常', '已下线']
const detailFields = [
  '记录编号', '节点ID', '节点编号', '采样节点编号', '节点主数据编号', 'status',
  'pending', 'CPU使用率', '内存使用率', '磁盘使用率', '网络延迟', '错误次数', '采样时间',
  '真实节点可调度', '真实节点调度状态', '归属模式名称', '归档', '归档原因',
]

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(5)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '', nodeId: '' })
const applied = ref({ keyword: '', status: '', nodeId: '' })
const nodes = ref<Row[]>([])
const stats = ref<Row>({})
const activeEntryId = ref<string | null>(null)
const detail = ref<Row | null>(null)
const detailDispositions = ref<Row[]>([])
const createPanel = ref(false)
const draft = ref({ nodeId: '', recordCode: '', cpu: '', batch: '', seq: '' })
const policyMode = ref('origin')
const policyOptions = ref<{ mode: string; name: string; description: string }[]>([])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const statCards = computed(() => [
  { label: '巡检记录总数', value: stats.value.total ?? 0 },
  { label: '异常记录', value: stats.value.abnormal ?? 0 },
  { label: '待处置记录', value: stats.value.pending ?? 0 },
  { label: '编号冲突采样', value: stats.value.conflict ?? 0 },
  { label: '待迁移采样', value: stats.value.migration ?? 0 },
])
const pageButtons = computed(() => {
  const start = Math.max(1, Math.min(page.value - 2, totalPages.value - 4))
  return Array.from({ length: Math.min(5, totalPages.value) }, (_, index) => start + index)
})
const locateLabel = computed(() => {
  if (activeEntryId.value) {
    const row = rows.value.find((item) => String(item.id) === activeEntryId.value)
    if (row) return `已定位「${row['记录编号']}」，列表序号 ${row['列表序号']}`
  }
  return '未定位'
})
const currentPolicyDescription = computed(
  () => policyOptions.value.find((option) => option.mode === policyMode.value)?.description ?? '',
)

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function buildQuery(): string {
  const params = new URLSearchParams()
  if (applied.value.keyword.trim()) params.set('keyword', applied.value.keyword.trim())
  if (applied.value.status) params.set('status', applied.value.status)
  if (applied.value.nodeId) params.set('node_id', applied.value.nodeId)
  params.set('page', String(page.value))
  params.set('size', String(pageSize.value))
  return params.toString()
}

async function loadNodes() {
  const payload = await fetchJson<{ items?: Row[] }>('/api/cluster?size=200')
  nodes.value = payload.items ?? []
}

async function loadStats() {
  stats.value = await fetchJson<Row>(`${ENDPOINT}/stats`)
}

async function loadPolicy() {
  const payload = await fetchJson<Row>(`${ENDPOINT}/conflict-policy`)
  policyMode.value = payload.mode ?? 'origin'
  policyOptions.value = payload.options ?? []
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ items?: Row[]; total?: number }>(
      `${ENDPOINT}?${buildQuery()}`,
    )
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
    // 返回列表后按定位结果核对：重新定位该记录，确认序号与页码没有错位。
    if (activeEntryId.value) {
      await locateEntry(activeEntryId.value, false)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点状态巡检列表读取失败'
  }
}

function applyFilters() {
  applied.value = { ...filters.value }
  page.value = 1
  activeEntryId.value = null
  detail.value = null
  void reload()
}

function resetFilters() {
  filters.value = { keyword: '', status: '', nodeId: '' }
  applied.value = { keyword: '', status: '', nodeId: '' }
  page.value = 1
  activeEntryId.value = null
  detail.value = null
  void reload()
}

function goPage(target: number) {
  page.value = Math.min(Math.max(target, 1), totalPages.value)
  void reload()
}

async function locateEntry(entryId: string, loadDetail = true) {
  const payload = await fetchJson<DetailPayload>(
    `${ENDPOINT}/locate?entry_id=${encodeURIComponent(entryId)}&size=${pageSize.value}`,
  )
  activeEntryId.value = entryId
  if (loadDetail) {
    detail.value = payload['记录'] ?? null
    detailDispositions.value = payload['记录']?.['处置记录'] ?? []
  }
  return payload
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const payload = await locateEntry(String(row.id))
    const locationPage = Number(payload['页码'] ?? page.value)
    if (locationPage && locationPage !== page.value) {
      page.value = locationPage
      await reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡检详情读取失败'
  }
}

function closeDetail() {
  detail.value = null
  detailDispositions.value = []
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || '节点状态巡检动作未生效，请稍后重试')
    }
    await reload()
    if (detail.value && String(detail.value.id) === String(row.id)) {
      await locateEntry(String(row.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点状态巡检操作失败'
  }
}

async function switchPolicy(mode: string) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/conflict-policy`, {
      method: 'POST',
      body: JSON.stringify({ values: { mode } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || '编号冲突口径切换失败')
    }
    await loadPolicy()
    await reload()
    if (activeEntryId.value) await locateEntry(activeEntryId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '编号冲突口径切换失败'
  }
}

function openCreate() {
  createPanel.value = true
}

async function submitSample() {
  errorMessage.value = ''
  const node = nodes.value.find((item) => String(item.id) === draft.value.nodeId)
  if (!node) {
    errorMessage.value = '请先选择要登记的集群节点'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/samples`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          记录编号: draft.value.recordCode,
          节点编号: node['节点编号'],
          节点ID: node.id,
          CPU使用率: draft.value.cpu,
          采样批次: draft.value.batch,
          采样序号: draft.value.seq,
          status: '正常',
          采样时间: new Date().toISOString().slice(0, 19).replace('T', ' '),
        },
      }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || '巡检采样登记失败')
    }
    createPanel.value = false
    draft.value = { nodeId: '', recordCode: '', cpu: '', batch: '', seq: '' }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡检采样登记失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

watch(pageSize, () => {
  page.value = 1
  void reload()
})

onMounted(async () => {
  try {
    await Promise.all([loadNodes(), loadPolicy()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡检基础数据读取失败'
  }
  await reload()
})
</script>
