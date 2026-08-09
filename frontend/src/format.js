// A股展示约定：亿元换算、带符号百分比、红涨绿跌样式类

/** 元 -> 亿元（两位小数），null 显示 - */
export const fmtYi = v => (v == null ? '-' : (v / 1e8).toFixed(2))

/** 百分比带符号（两位小数），null 显示 - */
export const fmtPct = v => (v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%')

/** 红涨绿跌的 class 名，null 返回空 */
export const upDownClass = v => (v == null ? '' : v >= 0 ? 'up' : 'down')
