<template>
  <div class="detail">
    <div class="dt-hd">
      <h2>{{ sector ? sector.display + ' · 指数分时' : '板块详情' }}</h2>
      <p v-if="sector" class="dt-sub">
        <span :class="upDownClass(sector.change_pct)">涨跌 {{ fmtPct(sector.change_pct) }}</span>
        <span class="sep">·</span>
        <span :class="upDownClass(sector.main_net_inflow)">主力净流入 {{ fmtYi(sector.main_net_inflow) }} 亿</span>
        <span class="sep">·</span>
        <span class="hint">点击柱子/看板行进详情页</span>
      </p>
    </div>
    <StockChart :trend="trend" :loading="trendLoading" :error="trendErr" />
    <div class="st-scroll">
      <StockTable :sector="sector" :stocks="stocks" :loading="loading" />
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { fetchSectorTrend, fetchStockQuotes } from '../api.js'
import { fmtYi, fmtPct, upDownClass } from '../format.js'
import StockChart from './StockChart.vue'
import StockTable from './StockTable.vue'

const props = defineProps({
  sector: { type: Object, default: null },
})

const HOVER_SETTLE_MS = 200  // 悬浮防抖：划过不加载，停稳才拉
const CACHE_TTL_MS = 60000   // 板块数据缓存：来回悬浮即点即现、不清屏
const POLL_MS = 5000         // 停稳后的刷新频率（后端有缓存，此处只是读）

const trend = ref(null)
const trendErr = ref('')
const trendLoading = ref(false)
const stocks = ref([])
const loading = ref(false)

// code -> {trend, stocks, trendErr, ts}
const cache = new Map()

let settleTimer = null
let pollTimer = null
let active = null  // 当前停稳的板块对象（含 members）

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function applyEntry(entry) {
  trend.value = entry.trend
  trendErr.value = entry.trendErr || ''
  stocks.value = entry.stocks
  trendLoading.value = false
  loading.value = false
}

// 拉分时+成分股行情写入缓存；失败时保留该 code 的旧数据
async function fetchInto(sector) {
  const members = sector.members || []
  const [tRes, qRes] = await Promise.allSettled([
    fetchSectorTrend(sector.code),
    members.length ? fetchStockQuotes(members) : Promise.resolve({ stocks: [] }),
  ])
  const entry = cache.get(sector.code) || { trend: null, stocks: [], trendErr: '' }
  if (tRes.status === 'fulfilled') {
    entry.trend = tRes.value
    entry.trendErr = ''
  } else if (!entry.trend) {
    entry.trendErr = '分时加载失败：' + (tRes.reason?.message || '未知')
  }
  if (qRes.status === 'fulfilled') entry.stocks = qRes.value.stocks
  entry.ts = Date.now()
  cache.set(sector.code, entry)
  return entry
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    const sec = active
    if (!sec) return
    const entry = await fetchInto(sec)
    if (active?.code === sec.code) applyEntry(entry)
  }, POLL_MS)
}

// 板块切换（悬浮驱动）：防抖 + 缓存回放 + 异步替换，全程不清屏
watch(() => props.sector?.code, () => {
  if (settleTimer) { clearTimeout(settleTimer); settleTimer = null }
  stopPolling()
  const sector = props.sector
  if (!sector) { active = null; return }

  const hit = cache.get(sector.code)
  if (hit) applyEntry(hit)  // 有缓存立即渲染，无闪烁
  if (hit && Date.now() - hit.ts < CACHE_TTL_MS) {
    active = sector
    startPolling()
    return
  }

  // 无缓存或缓存过期：等悬浮停稳再拉，避免快速划过时的请求风暴
  settleTimer = setTimeout(async () => {
    active = sector
    if (!hit) {
      trendLoading.value = true
      loading.value = true
    }
    const entry = await fetchInto(sector)
    if (active?.code === sector.code) {
      applyEntry(entry)
      startPolling()
    }
  }, HOVER_SETTLE_MS)
}, { immediate: true })

onBeforeUnmount(() => {
  if (settleTimer) clearTimeout(settleTimer)
  stopPolling()
})
</script>

<style scoped>
.detail { padding: 14px 16px 12px; display: flex; flex-direction: column; gap: 10px; }
.dt-hd h2 { font-size: 16px; color: #eef2ff; margin: 0 0 4px; }
.dt-sub { margin: 0; font-size: 13px; }
.dt-sub .sep { color: #7b84a3; margin: 0 8px; }
.dt-sub .hint { color: #5a627e; font-size: 12px; }
/* 表格区限高滚动：板块切换时面板高度稳定，不抖动 */
.st-scroll { max-height: 260px; overflow-y: auto; }
.up { color: #ff4d4f; }
.down { color: #00b578; }
</style>
