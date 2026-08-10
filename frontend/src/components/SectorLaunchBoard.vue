<template>
  <div class="board">
    <div class="bd-hd">
      <h2>板块启动看板</h2>
      <p class="bd-sub">
        排序 = v5 短期资金流斜率（截面归一化） ·
        <span class="acc-up">↑</span> 加速 = v5 &gt; v15 ·
        拥挤度为徽标不参与排序（观察期）· 点击行进详情
      </p>
    </div>
    <div v-if="!sectors.length" class="bd-empty">加载中…</div>
    <table v-else>
      <thead>
        <tr>
          <th class="c-rank">#</th>
          <th class="c-name">板块</th>
          <th>涨跌幅</th>
          <th>v5短期(亿/分)</th>
          <th>v15长期(亿/分)</th>
          <th>净流入(亿)</th>
          <th>拥挤度</th>
          <th>筹码</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(s, i) in ranked"
          :key="s.code"
          :class="{ sel: s.code === selectedCode, dead: isDead(s) }"
          @click="emit('open', s.code)"
          @mouseenter="emit('select', s.code)"
        >
          <td class="c-rank">{{ i + 1 }}</td>
          <td class="c-name">
            {{ s.display }}
            <span v-if="isAccel(s)" class="badge acc-up" title="加速：v5 > v15">↑</span>
            <span v-if="isDead(s)" class="badge tag-dead">死</span>
          </td>
          <td :class="upDownClass(s.change_pct)">{{ fmtPct(s.change_pct) }}</td>
          <td :class="upDownClass(v5(s))">{{ fmtSlope(v5(s)) }}</td>
          <td :class="upDownClass(v15(s))">{{ fmtSlope(v15(s)) }}</td>
          <td :class="upDownClass(s.main_net_inflow)">{{ fmtYi(s.main_net_inflow) }}</td>
          <td>
            <span class="chip" :class="congClass(s)" :title="congTip(s)">{{ congText(s) }}</span>
          </td>
          <td>
            <span v-if="chipLabel(s)" class="cf" :class="chipClass(s)" :title="chipTip(s)">{{ chipLabel(s) }}</span>
            <span v-else class="cf-none">-</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtYi, fmtPct, upDownClass } from '../format.js'

const props = defineProps({
  sectors: { type: Array, default: () => [] },
  selectedCode: { type: String, default: null },
})
const emit = defineEmits(['select', 'open'])

const v5 = s => s.momentum?.v5
const v15 = s => s.momentum?.v15
const isAccel = s => s.momentum?.accel === true
const isDead = s => s.momentum?.dead === true

// 排序规则集中在此（动量 V1）：
//   死板块置底；其余按 momentum.score（v5 截面 z-score）降序；
//   数据未就绪（score=null）排在存活板块末尾。
//   后端换排序口径时只动这一个 computed。
const ranked = computed(() => {
  const arr = [...props.sectors]
  arr.sort((a, b) => {
    if (isDead(a) !== isDead(b)) return isDead(a) ? 1 : -1
    const sa = a.momentum?.score ?? -Infinity
    const sb = b.momentum?.score ?? -Infinity
    return sb - sa
  })
  return arr
})

// 斜率展示：带符号两位小数（亿元/分钟）
const fmtSlope = v => (v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(2))

// 拥挤度徽标（V2 观察期：只提示不干预排序）
const cong = s => s.congestion || {}
const congText = s => (cong(s).position == null ? '-' : cong(s).position + '%')
const congClass = s => {
  const p = cong(s).position
  if (p == null) return 'c-none'
  if (p < 35) return 'c-low'
  if (p < 65) return 'c-mid'
  return 'c-high'
}
const congTip = s => {
  const c = cong(s)
  if (c.position == null) return '拥挤度数据不足'
  const p = c.parts || {}
  return `拥挤位置 ${c.position}%\n分量：小单 ${p.small ?? '-'} / 铺开 ${p.breadth ?? '-'} / 抬高 ${p.extension ?? '-'}\n` +
    `小单净占比 ${c.small_net_pct == null ? '-' : c.small_net_pct.toFixed(2) + '%'} · ` +
    `上涨占比 ${c.breadth_pct == null ? '-' : c.breadth_pct + '%'}`
}

// 筹码结构四象限（主力 vs 小单背离）：派发=主力出货散户接盘，吸筹=主力买散户卖
const FLOW_STYLE = {
  '派发': 'cf-dist',
  '吸筹': 'cf-abs',
  '共振涌入': 'cf-surge',
  '双逃': 'cf-panic',
  '早盘': 'cf-early',
}
const chip = s => s.flow_pattern || {}
const chipLabel = s => chip(s).pattern || ''
const chipClass = s => FLOW_STYLE[chip(s).pattern] || 'cf-none'
const chipTip = s => {
  const f = chip(s)
  if (!f.pattern) return '中性：主力/小单净占比均在阈值内'
  let t = `${f.pattern}｜主力净占比 ${f.main_pct}% · 小单净占比 ${f.small_pct}%`
  if (f.absorption != null) t += `\n接盘强度 ${f.absorption}（散户接走主力抛盘的比例）`
  if (f.accumulate != null) t += `\n吸筹强度 ${f.accumulate}`
  return t
}
</script>

<style scoped>
.board { padding: 14px 16px 10px; }
.bd-hd h2 { font-size: 16px; color: #eef2ff; margin: 0 0 4px; }
.bd-sub { color: #7b84a3; font-size: 12px; margin: 0 0 10px; line-height: 1.6; }
.bd-empty { color: #7b84a3; padding: 32px 0; text-align: center; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 7px 6px; text-align: right; white-space: nowrap; }
th { color: #7b84a3; font-weight: normal; border-bottom: 1px solid #2c3550; }
td { border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
.c-rank { width: 28px; text-align: center; color: #7b84a3; }
.c-name { text-align: left; color: #dde3ef; }
th.c-name { text-align: left; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: rgba(255, 255, 255, 0.04); }
tbody tr.sel { background: rgba(91, 132, 255, 0.14); }
tbody tr.sel .c-name { color: #9db4ff; }
tbody tr.dead td { color: #5a627e; }
.badge { font-size: 11px; margin-left: 4px; }
.acc-up { color: #ff4d4f; font-weight: bold; }
.tag-dead {
  color: #5a627e; border: 1px solid #3a4260; border-radius: 4px;
  padding: 0 4px; font-size: 10px;
}
.up { color: #ff4d4f; }
.down { color: #00b578; }
.chip {
  display: inline-block; padding: 1px 9px; border-radius: 10px;
  font-size: 12px; border: 1px solid transparent; cursor: help;
}
.c-low { color: #00b578; border-color: rgba(0, 181, 120, 0.45); }
.c-mid { color: #f5b041; border-color: rgba(245, 176, 65, 0.45); }
.c-high { color: #ff4d4f; border-color: rgba(255, 77, 79, 0.5); }
.c-none { color: #5a627e; }
/* 筹码结构徽标 */
.cf {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 12px; border: 1px solid transparent; cursor: help;
}
.cf-dist { color: #ff4d4f; border-color: rgba(255, 77, 79, 0.5); background: rgba(255, 77, 79, 0.08); }
.cf-abs { color: #00b578; border-color: rgba(0, 181, 120, 0.45); background: rgba(0, 181, 120, 0.08); }
.cf-surge { color: #f5b041; border-color: rgba(245, 176, 65, 0.45); }
.cf-panic { color: #7b84a3; border-color: #3a4260; }
.cf-early { color: #5a627e; }
.cf-none { color: #5a627e; }
</style>
