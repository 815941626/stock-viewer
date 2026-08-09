<template>
  <div class="app">
    <header class="hd">
      <div>
        <h1>A股板块资金流观测</h1>
        <p class="sub">柱高 = 主力净流入 · 颜色 = 涨跌（红涨绿跌） · 宽度 = 成交额 · 点击柱子进入板块详情</p>
      </div>
      <p v-if="warn" class="warn">⚠ {{ warn }}（展示最近成功数据）</p>
    </header>

    <!-- ① 板块资金流柱状图：全局总览，悬浮预览、点击进详情页 -->
    <section class="panel">
      <SectorChart
        :sectors="sectors"
        :selected-code="selectedCode"
        :loading="loading"
        :error="error"
        @select="onSelect"
        @open="onOpen"
      />
    </section>

    <div class="row">
      <!-- ② 板块启动看板：排行，悬浮预览、点击进详情页 -->
      <section class="panel">
        <SectorLaunchBoard
          :sectors="sectors"
          :selected-code="selectedCode"
          @select="onSelect"
          @open="onOpen"
        />
      </section>
      <!-- ③ 预览面板：悬浮/最近查看板块的分时 + 成分龙头股 -->
      <section class="panel">
        <StockDetailPanel :sector="selectedSector" />
      </section>
    </div>

    <!-- ④ 板块详情页（全屏覆盖层，点击柱子/看板行进入） -->
    <SectorDetailPage
      v-if="openSector"
      :sector="openSector"
      @close="onClose"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { fetchSectors } from './api.js'
import SectorChart from './components/SectorChart.vue'
import SectorLaunchBoard from './components/SectorLaunchBoard.vue'
import StockDetailPanel from './components/StockDetailPanel.vue'
import SectorDetailPage from './components/SectorDetailPage.vue'

const POLL_MS = 5000

const sectors = ref([])
const loading = ref(true)
const error = ref('')   // 致命错误：完全没有数据可展示
const warn = ref('')    // 轮询失败但有旧数据：顶部提示，不打断展示
const selectedCode = ref(null)

const selectedSector = computed(
  () => sectors.value.find(s => s.code === selectedCode.value) || null
)

function onSelect(code) {
  selectedCode.value = code
}

// 详情页：点击柱子/看板行进入。sector 存点击时的快照——该板块随后掉出
// Top N 时页面仍可用；若仍在榜则用榜上的活对象（momentum 保持刷新）。
const openSectorSnapshot = ref(null)
const openSector = computed(() => {
  if (!openSectorSnapshot.value) return null
  const code = openSectorSnapshot.value.code
  return sectors.value.find(s => s.code === code) || openSectorSnapshot.value
})

function onOpen(code) {
  const s = sectors.value.find(x => x.code === code)
  if (!s) return
  selectedCode.value = code
  openSectorSnapshot.value = s
}

function onClose() {
  openSectorSnapshot.value = null
}

async function poll() {
  try {
    const json = await fetchSectors()
    sectors.value = json.sectors
    warn.value = json.ok ? '' : (json.last_error || '数据可能已过期')
    error.value = ''
    // 选中板块不在榜（首连/动量池轮换）时自动切到榜首，保证详情面板不留白
    const stillIn = sectors.value.some(s => s.code === selectedCode.value)
    if (!stillIn && sectors.value.length) {
      selectedCode.value = sectors.value[0].code
    }
  } catch (e) {
    if (sectors.value.length) warn.value = e.message
    else error.value = '加载失败：' + e.message
  } finally {
    loading.value = false
  }
}

let timer = null
onMounted(() => {
  poll()
  timer = setInterval(poll, POLL_MS)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.app { max-width: 1440px; margin: 0 auto; padding: 24px 20px 40px; }
.hd {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; margin-bottom: 18px;
}
.hd h1 { font-size: 22px; color: #eef2ff; margin: 0 0 6px; letter-spacing: 1px; }
.hd .sub { color: #7b84a3; font-size: 13px; margin: 0; }
.warn { color: #f5b041; font-size: 12px; margin: 0; }
.panel {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid #232b45;
  border-radius: 10px;
  overflow: hidden;
}
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
@media (max-width: 1000px) { .row { grid-template-columns: 1fr; } }
</style>
