<template>
  <div class="sc-wrap">
    <div ref="chartEl" class="sc-chart"></div>
    <div v-if="loading" class="sc-status">加载中…</div>
    <div v-else-if="error" class="sc-status err">{{ error }}</div>
    <div v-else-if="!trend" class="sc-status">选择板块后显示分时图</div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  // {pre_close, points: [{time, price, avg}]}；ok=false 时可能是降级的旧数据
  trend: { type: Object, default: null },
  loading: Boolean,
  error: String,
})

const chartEl = ref(null)
let chart = null

function buildOption(trend) {
  const times = trend.points.map(p => p.time)
  const prices = trend.points.map(p => p.price)
  const avgs = trend.points.map(p => p.avg)

  // Y 轴范围：覆盖现价/均价/昨收并上下留白 10%
  const all = [...prices, ...avgs, trend.pre_close].filter(v => v != null)
  const max = Math.max(...all)
  const min = Math.min(...all)
  const span = (max - min) || max * 0.01 || 1

  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 60, right: 16, top: 28, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(18,24,38,0.95)',
      borderColor: '#2c3550',
      textStyle: { color: '#dde3ef', fontSize: 12 },
      formatter: params => {
        const lines = [`<b>${params[0]?.axisValue ?? ''}</b>`]
        for (const p of params) {
          if (p.value == null) continue
          const diff = (p.value / trend.pre_close - 1) * 100
          lines.push(
            `${p.marker}${p.seriesName}：${p.value.toFixed(2)}` +
            `（${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%）`
          )
        }
        return lines.join('<br/>')
      }
    },
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      // 241 个分钟点，每 60 个点打一个刻度（约一小时一格）
      axisLabel: { color: '#7b84a3', fontSize: 11, interval: 59 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#3a4260' } }
    },
    yAxis: {
      type: 'value',
      min: min - span * 0.1,
      max: max + span * 0.1,
      axisLabel: { color: '#7b84a3', fontSize: 11 },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.07)' } }
    },
    series: [
      {
        name: '指数',
        type: 'line',
        data: prices,
        symbol: 'none',
        lineStyle: { color: '#e8edfb', width: 1.5 },
        // 昨收参考线：判断当日强弱的基准
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#98a2c0', type: 'dashed', width: 1 },
          label: {
            formatter: '昨收 ' + trend.pre_close.toFixed(2),
            color: '#98a2c0', fontSize: 11, position: 'insideEndTop'
          },
          data: [{ yAxis: trend.pre_close }]
        }
      },
      {
        name: '均价',
        type: 'line',
        data: avgs,
        symbol: 'none',
        lineStyle: { color: '#f5b041', width: 1 }
      }
    ]
  }
}

watch(
  () => props.trend,
  t => {
    if (!chart || !t) return
    chart.setOption(buildOption(t), true)
  }
)

function onResize() {
  if (chart) chart.resize()
}

onMounted(() => {
  chart = echarts.init(chartEl.value)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
})
</script>

<style scoped>
.sc-wrap { position: relative; width: 100%; height: 240px; }
.sc-chart { width: 100%; height: 100%; }
.sc-status {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #7b84a3; font-size: 13px; pointer-events: none;
}
.sc-status.err { color: #ff7875; }
</style>
