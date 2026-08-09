<template>
  <div class="st-wrap">
    <div class="st-hd">
      成分龙头股
      <span v-if="stocks.length" class="st-note">名单内属于本板块的 {{ stocks.length }} 只</span>
    </div>

    <!-- 无成员：固定 SECTORS 回退（非动量池板块）或名单未覆盖 -->
    <div v-if="!stocks.length" class="st-empty">
      <template v-if="loading">加载中…</template>
      <template v-else>本板块暂无配置的龙头股（动量池板块会自动带出名单内成分股）</template>
    </div>

    <table v-else>
      <thead>
        <tr>
          <th class="c-name">名称</th>
          <th v-if="detailed">现价</th>
          <th>涨跌幅</th>
          <th>主力净流入(亿)</th>
          <th v-if="detailed">净占比</th>
          <th v-if="detailed">换手率</th>
          <th v-if="detailed">成交额(亿)</th>
          <th v-if="detailed">总市值(亿)</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in stocks" :key="s.code">
          <td class="c-name">
            {{ s.name || s.code }}
            <span v-if="s.name" class="code">{{ s.code }}</span>
          </td>
          <td v-if="detailed">{{ fmtPrice(s.price) }}</td>
          <td :class="upDownClass(s.change_pct)">{{ fmtPct(s.change_pct) }}</td>
          <td :class="upDownClass(s.main_net_inflow)">{{ fmtYi(s.main_net_inflow) }}</td>
          <td v-if="detailed" :class="upDownClass(s.main_net_pct)">{{ fmtPct(s.main_net_pct) }}</td>
          <td v-if="detailed">{{ fmtRate(s.turnover_rate) }}</td>
          <td v-if="detailed">{{ fmtYi(s.amount) }}</td>
          <td v-if="detailed">{{ fmtYi(s.total_mcap) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { fmtYi, fmtPct, upDownClass } from '../format.js'

defineProps({
  sector: { type: Object, default: null },
  // 行情数据形状：fetchStockQuotes 返回的 stocks
  stocks: { type: Array, default: () => [] },
  // 明细模式：详情页用，列全（现价/净占比/换手/市值）；面板用紧凑三列
  detailed: { type: Boolean, default: false },
  // 首次加载中：空表时显示"加载中"而非空态文案
  loading: { type: Boolean, default: false },
})

const fmtPrice = v => (v == null ? '-' : v.toFixed(2))
const fmtRate = v => (v == null ? '-' : v.toFixed(2) + '%')
</script>

<style scoped>
.st-wrap { border-top: 1px solid #232b45; padding-top: 10px; }
.st-hd { font-size: 13px; color: #a8b2cf; margin-bottom: 8px; }
.st-note { color: #5a627e; font-size: 12px; margin-left: 6px; }
.st-empty {
  color: #7b84a3; font-size: 12px; line-height: 1.8;
  border: 1px dashed #2c3550; border-radius: 8px;
  padding: 18px 14px; text-align: center;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; text-align: right; white-space: nowrap; }
th {
  color: #7b84a3; font-weight: normal; border-bottom: 1px solid #2c3550;
  /* 外层限高滚动时表头吸顶（背景须不透明才能盖住滚动行） */
  position: sticky; top: 0; background: #141b2e; z-index: 1;
}
td { border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
.c-name { text-align: left; color: #dde3ef; }
.c-name .code { color: #5a627e; font-size: 11px; margin-left: 5px; }
th.c-name { text-align: left; }
.up { color: #ff4d4f; }
.down { color: #00b578; }
</style>
