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
      </div>
    </header>

    <section class="pg-chart panel">
      <StockChart :trend="trend" :loading="trendLoading" :error="trendErr" />
    </section>

    <section class="pg-stocks panel">
      <StockTable :sector="sector" :stocks="stocks" :detailed="true" />
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { fetchSectorTrend, fetchStockQuotes } from '../api.js'
import { fmtYi, fmtPct, upDownClass } from '../format.js'
import StockChart from './StockChart.vue'
import StockTable from './StockTable.vue'

const props = defineProps({
  // App 传入的板块对象（含 momentum/members）；掉榜时为点击时的快照
  sector: { type: Object, required: true },
})
const emit = defineEmits(['close'])

const m = computed(() => props.sector.momentum || null)

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
