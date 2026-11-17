<template>
  <section class="inspection-panels" data-role="inspection-panels">
    <div class="panel-head">
      <h3>{{ title }}</h3>
      <span class="panel-scope">
        {{ nodeId ? `节点 #${nodeId}` : '全部真实节点' }} · 与巡检记录共用同一排序与身份
      </span>
    </div>
    <nav class="tab-row">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: tab.key === activeTab }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span class="tab-count">{{ counts[tab.key] }}</span>
      </button>
    </nav>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <table class="data-table">
      <thead>
        <tr>
          <th>列表序号</th>
          <th v-for="column in activeColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in activeRows" :key="`${activeTab}-${row.id}`">
          <td>{{ row['列表序号'] ?? '—' }}</td>
          <td v-for="column in activeColumns" :key="column">{{ formatCell(row[column]) }}</td>
        </tr>
        <tr v-if="!activeRows.length">
          <td :colspan="activeColumns.length + 1" class="empty-state">{{ activeEmpty }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { fetchJson } from '@/api/client'

type Row = Record<string, unknown>
type TabKey = 'repair' | 'pending' | 'exceptions' | 'migration' | 'compare'

const props = withDefaults(
  defineProps<{
    nodeId?: number | null
    nodeCode?: string | null
    title?: string
  }>(),
  { nodeId: null, nodeCode: null, title: '节点模块巡检数据' },
)

const tabs: { key: TabKey; label: string; columns: string[]; empty: string }[] = [
  {
    key: 'repair',
    label: '修复巡检记录',
    columns: ['记录编号', '节点编号', '采样节点编号', 'status', '采样时间', '处置结果'],
    empty: '该节点暂无修复巡检记录',
  },
  {
    key: 'pending',
    label: '待处理队列',
    columns: ['记录编号', '节点编号', 'status', '采样时间', '处置状态', '真实节点可调度'],
    empty: '该节点没有待处理的巡检采样',
  },
  {
    key: 'exceptions',
    label: '巡检异常清单',
    columns: ['记录编号', '节点编号', 'status', '处置编号', '处置状态', '真实节点可调度'],
    empty: '该节点暂无巡检异常',
  },
  {
    key: 'migration',
    label: '待迁移清单',
    columns: ['记录编号', '采样节点编号', '节点主数据编号', '迁移说明', '归档'],
    empty: '当前没有因编号冲突待迁移的采样',
  },
  {
    key: 'compare',
    label: '巡检与集群核对',
    columns: ['节点编号', '集群节点状态', '巡检状态', '期望可调度', '集群可调度', '一致', '差异说明'],
    empty: '没有可核对的节点',
  },
]

const activeTab = ref<TabKey>('repair')
const data = ref<Record<TabKey, Row[]>>({
  repair: [],
  pending: [],
  exceptions: [],
  migration: [],
  compare: [],
})
const errorMessage = ref('')

const activeColumns = computed(() => tabs.find((tab) => tab.key === activeTab.value)?.columns ?? [])
const activeRows = computed(() => data.value[activeTab.value])
const activeEmpty = computed(() => tabs.find((tab) => tab.key === activeTab.value)?.empty ?? '暂无数据')
const counts = computed(() => ({
  repair: data.value.repair.length,
  pending: data.value.pending.length,
  exceptions: data.value.exceptions.length,
  migration: data.value.migration.length,
  compare: data.value.compare.filter((row) => row['一致'] === false).length,
}))

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join('；') : '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function switchTab(key: TabKey) {
  activeTab.value = key
}

function scopedQuery(extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ size: '100', ...extra })
  if (props.nodeId) params.set('node_id', String(props.nodeId))
  else if (props.nodeCode) params.set('节点编号', props.nodeCode)
  return params.toString()
}

async function loadRows(path: string): Promise<Row[]> {
  const payload = await fetchJson<{ items?: Row[] }>(`${path}?${scopedQuery()}`)
  return payload.items ?? []
}

async function reload() {
  errorMessage.value = ''
  try {
    const [repair, pending, exceptions, migration, compare] = await Promise.all([
      loadRows('/api/cluster/repair-records'),
      loadRows('/api/cluster/pending-queue'),
      loadRows('/api/cluster/exceptions'),
      loadRows('/api/nodecheck/migration'),
      loadRows('/api/nodecheck/compare'),
    ])
    const scopedCompare = props.nodeId
      ? compare.filter((row) => Number(row['节点ID']) === Number(props.nodeId))
      : compare
    data.value = { repair, pending, exceptions, migration, compare: scopedCompare }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点巡检数据读取失败'
  }
}

defineExpose({ reload })

watch(() => [props.nodeId, props.nodeCode], () => void reload())
onMounted(reload)
</script>
