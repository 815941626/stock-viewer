<template>
  <div class="chart-wrap">
    <div ref="chartEl" class="chart"></div>
    <div v-if="loading" class="status">加载中…</div>
    <div v-else-if="error && !sectors.length" class="status err">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

// A股习惯：红涨绿跌
const UP_COLOR = '#ff4d4f'
const DOWN_COLOR = '#00b578'

const props = defineProps({
  sectors: { type: Array, default: () => [] },
  selectedCode: { type: String, default: null },
  loading: Boolean,
  error: String,
})
const emit = defineEmits(['select', 'open'])

const chartEl = ref(null)
let chart = null
let currentList = []  // 点击回调用它索引柱子对应的板块

function buildOption(list, selectedCode) {
  const names = list.map(s => s.display)

  // 高度映射：主力净流入（元 -> 亿元），可为负
  const inflows = list.map(s => (s.main_net_inflow ?? 0) / 1e8)

  // 宽度映射：成交额归一化到 [0,1]；开方压缩量级差，避免最大/最小相差悬殊导致细柱不可见
  const amounts = list.map(s => s.amount ?? 0)
  const minA = Math.min(...amounts)
  const maxA = Math.max(...amounts)
  const widthNorms = amounts.map(a =>
    maxA > minA ? Math.sqrt((a - minA) / (maxA - minA)) : 0.5
  )

  // Y 轴范围：覆盖正负值并上下留白 15%
  const maxV = Math.max(...inflows, 0)
  const minV = Math.min(...inflows, 0)
  const span = (maxV - minV) || 1
  const yMax = maxV + span * 0.15
  const yMin = minV - span * 0.15

  // 每根柱的数据：[类目下标, 净流入(亿), 宽度归一, 是否涨(1/0), 是否选中(1/0)]
  const data = list.map((s, i) => [
    i, inflows[i], widthNorms[i],
    (s.change_pct ?? 0) >= 0 ? 1 : 0,
    s.code === selectedCode ? 1 : 0
  ])

  return {
    backgroundColor: 'transparent',
    animation: false, // 静态步骤先关动画；实时平滑动画在下一步单独加
    grid: { left: 64, right: 16, top: 48, bottom: 44 },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(18,24,38,0.95)',
      borderColor: '#2c3550',
      textStyle: { color: '#dde3ef' },
      formatter: (params) => {
        const s = list[params.dataIndex]
        const inflow = ((s.main_net_inflow ?? 0) / 1e8).toFixed(2)
        const amount = ((s.amount ?? 0) / 1e8).toFixed(1)
        const chg = (s.change_pct >= 0 ? '+' : '') + (s.change_pct ?? 0).toFixed(2) + '%'
        return `<b>${s.display}</b>（${s.name}）<br/>` +
          `涨跌幅：${chg}<br/>` +
          `主力净流入：${inflow} 亿<br/>` +
          `成交额：${amount} 亿`
      }
    },
    xAxis: {
      type: 'category',
      data: names,
      axisLabel: { color: '#a8b2cf', fontSize: 13, interval: 0 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#3a4260' } }
    },
    yAxis: {
      type: 'value',
      min: yMin,
      max: yMax,
      name: '主力净流入（亿）',
      nameTextStyle: { color: '#7b84a3' },
      axisLabel: { color: '#7b84a3' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.07)' } }
    },
    series: [{
      type: 'custom',
      encode: { x: 0, y: 1 },
      data,
      renderItem: (params, api) => {
        const catIdx = api.value(0)
        const inflow = api.value(1)
        const widthNorm = api.value(2)
        const up = api.value(3) === 1
        const selected = api.value(4) === 1

        // 一个类目带的像素宽度；柱宽按 22%~82% 带宽映射
        const band = api.size([1, 0])[0]
        const barW = Math.max(10, band * (0.22 + 0.6 * widthNorm))

        // 零轴与数值的像素坐标，据此算出柱体矩形
        const zero = api.coord([catIdx, 0])
        const val = api.coord([catIdx, inflow])
        const x = zero[0] - barW / 2
        const y = Math.min(zero[1], val[1])
        const h = Math.max(1.5, Math.abs(val[1] - zero[1]))
        const color = up ? UP_COLOR : DOWN_COLOR

        // 数值标签：正值标在柱上方，负值标在柱下方
        const labelY = inflow >= 0 ? y - 4 : y + h + 4
        return {
          type: 'group',
          children: [
            {
              type: 'rect',
              shape: { x, y, width: barW, height: h },
              style: {
                fill: color,
                opacity: 0.92,
                // 选中板块描边高亮，与看板/详情联动
                stroke: selected ? '#eef2ff' : 'transparent',
                lineWidth: selected ? 2 : 0
              }
            },
            {
              type: 'text',
              style: {
                text: (inflow >= 0 ? '+' : '') + inflow.toFixed(1),
                x: zero[0], y: labelY,
                fill: '#e8edfb', fontSize: 11,
                textAlign: 'center',
                textVerticalAlign: inflow >= 0 ? 'bottom' : 'top'
              }
            }
          ]
        }
      },
      // 零轴参考线：分隔净流入（上）与净流出（下）
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#98a2c0', width: 1 },
        label: { show: false },
        data: [{ yAxis: 0 }]
      }
    }]
  }
}

function render() {
  if (!chart || !props.sectors.length) return
  currentList = props.sectors
  // notMerge=true：数据每 5 秒整体换新，全量重建最省心
  chart.setOption(buildOption(props.sectors, props.selectedCode), true)
}

watch(() => [props.sectors, props.selectedCode], render)

function onResize() {
  if (chart) chart.resize()
}

onMounted(() => {
  chart = echarts.init(chartEl.value)
  window.addEventListener('resize', onResize)
  // 悬浮=预览联动右侧面板；点击=进入板块详情页
  chart.on('mouseover', params => {
    if (params.componentType !== 'series') return
    const s = currentList[params.dataIndex]
    if (s) emit('select', s.code)
  })
  chart.on('click', params => {
    if (params.componentType !== 'series') return
    const s = currentList[params.dataIndex]
    if (s) emit('open', s.code)
  })
  render()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
})
</script>

<style scoped>
.chart-wrap { position: relative; width: 100%; height: 460px; padding: 8px 8px 0; }
.chart { width: 100%; height: 100%; cursor: pointer; }
.status {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #a8b2cf; font-size: 15px; pointer-events: none;
}
.status.err { color: #ff7875; }
</style>
