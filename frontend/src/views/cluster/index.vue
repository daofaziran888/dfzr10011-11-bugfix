<template>
  <section class="page" data-module="cluster">
    <header class="page-head">
      <div>
        <h2>采集节点集群管理</h2>
        <p class="page-desc">
          维护采集集群的节点配置与可调度性；节点模块的修复巡检记录、待处理队列与巡检异常清单都以这里的真实节点为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记集群节点</button>
        <button class="btn" type="button" @click="exportRows">导出采集节点集群清单</button>
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
        <span>节点编号</span>
        <input v-model="filters.keyword" placeholder="按节点编号或地址检索" />
      </label>
      <label class="filter-item">
        <span>节点状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>序号</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可调度</th>
          <th>调度状态</th>
          <th>巡检异常</th>
          <th>详情</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="{ 'row-active': String(row.id) === String(activeNodeId) }"
        >
          <td>{{ row['列表序号'] }}</td>
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td>
            <span :class="row['可调度'] ? 'tag done' : 'tag pending'">
              {{ row['可调度'] ? '可调度' : '不可调度' }}
            </span>
          </td>
          <td>{{ row['调度状态'] }}</td>
          <td>{{ row['巡检异常数'] }} / 待处置 {{ row['待处置采样数'] }}</td>
          <td><button class="link" type="button" @click="openDetail(row)">查看详情</button></td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 6" class="empty-state">暂无采集节点集群数据，可先登记集群节点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集节点集群记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <aside v-if="detail" class="detail-panel" data-role="cluster-detail">
      <div class="panel-head">
        <h3>节点明细 · {{ detail['节点编号'] }}（#{{ detail['节点ID'] }}）</h3>
        <button class="btn ghost" type="button" @click="detail = null">收起</button>
      </div>
      <dl class="detail-grid">
        <dt>节点状态</dt><dd>{{ formatCell(detail['status']) }}</dd>
        <dt>可调度</dt><dd>{{ detail['可调度'] ? '是' : '否' }}</dd>
        <dt>调度状态</dt><dd>{{ formatCell(detail['调度状态']) }}</dd>
        <dt>巡检核对</dt>
        <dd>
          {{ detail['巡检核对']?.['一致'] ? '巡检与集群一致' : formatCell(detail['巡检核对']?.['差异说明']) }}
        </dd>
      </dl>
      <InspectionPanels
        ref="panels"
        :node-id="detail['节点ID']"
        :node-code="detail['节点编号']"
        :title="`节点 ${detail['节点编号']} 的修复巡检记录与清单`"
      />
    </aside>

    <form v-if="createPanel" class="create-panel" @submit.prevent="submitNode">
      <div class="panel-head">
        <h3>登记集群节点</h3>
        <button class="btn ghost" type="button" @click="createPanel = false">取消</button>
      </div>
      <label class="filter-item">
        <span>节点编号</span>
        <input v-model="draft.code" placeholder="例如 CLUS-0005" />
      </label>
      <label class="filter-item">
        <span>节点地址</span>
        <input v-model="draft.address" placeholder="例如 10.10.0.15:9100" />
      </label>
      <label class="filter-item">
        <span>节点角色</span>
        <input v-model="draft.role" placeholder="例如 采集节点" />
      </label>
      <button class="btn primary" type="submit">提交节点</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'
import InspectionPanels from '@/components/InspectionPanels.vue'

type Row = Record<string, any>

const ENDPOINT = '/api/cluster'
const columns = ['节点编号', '节点地址', '节点角色', 'CPU核心', '内存容量', '网络带宽', '心跳时间', '节点状态']
const actions = ['加入集群', '下线节点', '移除节点']
const statuses = ['待加入', '运行中', '已离线', '已移除']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const applied = ref({ keyword: '', status: '' })
const stats = ref<Row>({})
const detail = ref<Row | null>(null)
const activeNodeId = ref<string | null>(null)
const createPanel = ref(false)
const draft = ref({ code: '', address: '', role: '' })
const panels = ref<InstanceType<typeof InspectionPanels> | null>(null)

const statCards = computed(() => [
  { label: '集群节点总数', value: stats.value.total ?? total.value },
  { label: '可调度节点', value: stats.value.schedulable ?? 0 },
  { label: '巡检异常节点', value: stats.value.abnormal ?? 0 },
  { label: '待处置采样', value: stats.value.pending ?? 0 },
])

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join('；') : '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function buildQuery(): string {
  const params = new URLSearchParams({ size: '200' })
  if (applied.value.keyword.trim()) params.set('keyword', applied.value.keyword.trim())
  if (applied.value.status) params.set('status', applied.value.status)
  return params.toString()
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ items?: Row[]; total?: number }>(`${ENDPOINT}?${buildQuery()}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = {
      total: total.value,
      schedulable: rows.value.filter((row) => row['可调度']).length,
      abnormal: rows.value.reduce((sum, row) => sum + Number(row['巡检异常数'] ?? 0), 0),
      pending: rows.value.reduce((sum, row) => sum + Number(row['待处置采样数'] ?? 0), 0),
    }
    if (detail.value) {
      const refreshed = rows.value.find((row) => String(row.id) === String(detail.value?.id))
      if (refreshed) await openDetail(refreshed)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集节点集群列表读取失败'
  }
}

function applyFilters() {
  applied.value = { ...filters.value }
  void reload()
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  applied.value = { keyword: '', status: '' }
  void reload()
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  activeNodeId.value = String(row.id)
  try {
    detail.value = await fetchJson<Row>(`${ENDPOINT}/${row.id}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '集群节点明细读取失败'
  }
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
      throw new Error(payload.message || '采集节点集群动作未生效，请稍后重试')
    }
    await reload()
    await panels.value?.reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集节点集群操作失败'
  }
}

function openCreate() {
  createPanel.value = true
}

async function submitNode() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          节点编号: draft.value.code,
          节点地址: draft.value.address,
          节点角色: draft.value.role,
        },
      }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || '集群节点登记失败')
    }
    createPanel.value = false
    draft.value = { code: '', address: '', role: '' }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '集群节点登记失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(reload)
</script>
