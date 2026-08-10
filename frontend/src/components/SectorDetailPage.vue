<template>
  <div class="page">
    <header class="pg-hd">
      <button class="back" @click="emit('close')">← 返回</button>
      <h2>{{ sector.display }}</h2>
      <div class="stats">
        <span :class="upDownClass(sector.change_pct)">涨跌 {{ fmtPct(sector.change_pct) }}</span>
        <span :class="upDownClass(sector.main_net_inflow)">净流入 {{ fmtYi(sector.main_net_inflow) }} 亿</span>
        <span class="dim">成交额 {{ fmtYi(sector.amount) }} 亿</span>
        <span v-if="m" class="dim">score {{ fmtSlope(m.score) }}</span>
        <span v-if="m" :class="upDownClass(m.v5)">v5 {{ fmtSlope(m.v5) }}</span>
        <span v-if="m" :class="upDownClass(m.v15)">v15 {{ fmtSlope(m.v15) }}</span>
        <span v-if="m && m.accel === true" class="acc-up">↑ 加速</span>
        <span v-else-if="m && m.accel === false" class="acc-dn">↓ 减速</span>
        <span v-if="congPos != null" class="chip" :class="congClass" :title="congTip">
          拥挤 {{ congPos }}%
        </span>
        <span v-if="flowLabel" class="chip" :class="flowClass" :title="flowTip">
          {{ flowLabel }}<template v-if="flowStrength != null"> · 强度 {{ flowStrength }}</template>
        </span>
      </div>
    </header>

    <p v-if="etfFlow" class="etf-line" :title="etfTip">
      ETF申赎 ≈
      <span :class="etfFlow.total_flow >= 0 ? 'up' : 'down'">{{ etfFlowText }}</span>
      <span class="dim">（{{ etfFlow.etfs.length }} 只ETF · {{ etfDates }} · 被动盘拆解·事后口径）</span>
    </p>

    <section class="pg-chart panel">
      <StockChart :trend="trend" :loading="trendLoading" :error="trendErr" />
    </section>

    <section class="pg-stocks panel">
      <StockTable :sector="sector" :stocks="stocks" :detailed="true" />
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { fetchSectorTrend, fetchStockQuotes, fetchEtfFlow } from '../api.js'
import { fmtYi, fmtPct, upDownClass } from '../format.js'
import StockChart from './StockChart.vue'
import StockTable from './StockTable.vue'

const props = defineProps({
  // App 传入的板块对象（含 momentum/members）；掉榜时为点击时的快照
  sector: { type: Object, required: true },
})
const emit = defineEmits(['close'])

const m = computed(() => props.sector.momentum || null)

// 拥挤度 V2 徽标（观察期，不参与任何排序）
const congPos = computed(() => props.sector.congestion?.position ?? null)
const congClass = computed(() => {
  const p = congPos.value
  if (p == null) return ''
  if (p < 35) return 'c-low'
  if (p < 65) return 'c-mid'
  return 'c-high'
})
const congTip = computed(() => {
  const c = props.sector.congestion || {}
  const p = c.parts || {}
  return `拥挤位置 ${c.position}%（小单 ${p.small ?? '-'} / 铺开 ${p.breadth ?? '-'} / 抬高 ${p.extension ?? '-'})`
})

// 筹码结构四象限
const flow = computed(() => props.sector.flow_pattern || {})
const flowLabel = computed(() => flow.value.pattern || '')
const flowClass = computed(() => ({
  '派发': 'c-dist',
  '吸筹': 'c-abs',
  '共振涌入': 'c-mid',
}[flow.value.pattern] || 'c-none'))
const flowStrength = computed(() => flow.value.absorption ?? flow.value.accumulate ?? null)
const flowTip = computed(() => {
  const f = flow.value
  if (!f.pattern) return ''
  let t = `${f.pattern}｜主力净占比 ${f.main_pct}% · 小单净占比 ${f.small_pct}%`
  if (f.absorption != null) t += '\n接盘强度：散户接走主力抛盘的比例'
  if (f.accumulate != null) t += '\n吸筹强度：主力吸纳散户抛盘的比例'
  return t
})

// ETF 申赎拆解（事后口径，切换板块时拉一次即可——日频数据）
const etfFlow = ref(null)
async function loadEtfFlow() {
  etfFlow.value = null
  if (!props.sector) return
  try {
    const json = await fetchEtfFlow(props.sector.code)
    if (json.ok) etfFlow.value = json
  } catch (e) { /* 无代理映射或数据不足：静默不显示 */ }
}
watch(() => props.sector?.code, loadEtfFlow, { immediate: true })

const etfFlowText = computed(() => {
  const v = etfFlow.value?.total_flow
  if (v == null) return ''
  return (v >= 0 ? '+' : '') + (v / 1e8).toFixed(1) + '亿'
})
const etfDates = computed(() => {
  const d = etfFlow.value?.dates
  if (!d) return ''
  return `${d[0].slice(4, 6)}/${d[0].slice(6)}→${d[1].slice(4, 6)}/${d[1].slice(6)}`
})
const etfTip = computed(() => {
  if (!etfFlow.value) return ''
  return etfFlow.value.etfs.map(e =>
    `${e.name}：份额 ${(e.d_shares / 1e8).toFixed(1)} 亿份变化 → ` +
    `${(e.flow / 1e8) >= 0 ? '+' : ''}${(e.flow / 1e8).toFixed(1)} 亿`
  ).join('\n')
})

const TREND_POLL_MS = 30000
const QUOTE_POLL_MS = 5000

const trend = ref(null)
const trendErr = ref('')
const trendLoading = ref(false)
const stocks = ref([])
let trendTimer = null
let quoteTimer = null

async function loadTrend() {
  try {
    if (!trend.value) trendLoading.value = true
    trend.value = await fetchSectorTrend(props.sector.code)
    trendErr.value = ''
  } catch (e) {
    trendErr.value = '分时加载失败：' + e.message
  } finally {
    trendLoading.value = false
  }
}

async function loadQuotes() {
  const members = props.sector.members || []
  if (!members.length) return
  try {
    const json = await fetchStockQuotes(members)
    stocks.value = json.stocks
  } catch (e) {
    // 行情失败沿用旧值
  }
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  loadTrend()
  loadQuotes()
  trendTimer = setInterval(loadTrend, TREND_POLL_MS)
  quoteTimer = setInterval(loadQuotes, QUOTE_POLL_MS)
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  if (trendTimer) clearInterval(trendTimer)
  if (quoteTimer) clearInterval(quoteTimer)
  window.removeEventListener('keydown', onKey)
})

const fmtSlope = v => (v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(2))
</script>

<style scoped>
.page {
  position: fixed; inset: 0; z-index: 50;
  background: radial-gradient(1200px 600px at 50% -10%, #16213a 0%, #0d1117 60%) #0d1117;
  overflow-y: auto;
  padding: 20px;
}
.pg-hd {
  max-width: 1200px; margin: 0 auto 16px;
  display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
}
.back {
  background: rgba(255, 255, 255, 0.06); color: #a8b2cf;
  border: 1px solid #2c3550; border-radius: 8px;
  padding: 6px 14px; font-size: 13px; cursor: pointer;
}
.back:hover { background: rgba(255, 255, 255, 0.12); color: #eef2ff; }
.pg-hd h2 { font-size: 20px; color: #eef2ff; margin: 0; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; }
.stats .dim { color: #7b84a3; }
.acc-up { color: #ff4d4f; font-weight: bold; }
.acc-dn { color: #00b578; }
.chip {
  padding: 1px 9px; border-radius: 10px; font-size: 12px;
  border: 1px solid transparent; cursor: help;
}
.c-low { color: #00b578; border-color: rgba(0, 181, 120, 0.45); }
.c-mid { color: #f5b041; border-color: rgba(245, 176, 65, 0.45); }
.c-high { color: #ff4d4f; border-color: rgba(255, 77, 79, 0.5); }
.c-dist { color: #ff4d4f; border-color: rgba(255, 77, 79, 0.5); background: rgba(255, 77, 79, 0.08); }
.c-abs { color: #00b578; border-color: rgba(0, 181, 120, 0.45); background: rgba(0, 181, 120, 0.08); }
.c-none { color: #5a627e; }
.etf-line { margin: 0; font-size: 12px; color: #a8b2cf; }
.etf-line .dim { color: #5a627e; margin-left: 6px; }
.up { color: #ff4d4f; }
.down { color: #00b578; }
.panel {
  max-width: 1200px; margin: 0 auto 16px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid #232b45; border-radius: 10px;
}
.pg-chart { padding: 8px; }
.pg-chart :deep(.sc-wrap) { height: 340px; }
.pg-stocks { padding: 14px 16px; }
.up { color: #ff4d4f; }
.down { color: #00b578; }
</style>
