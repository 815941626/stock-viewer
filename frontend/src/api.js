// /api 请求统一封装：经 Vite 代理到 FastAPI；失败抛带中文信息的 Error，调用方直接展示

async function getJson(url) {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error('HTTP ' + resp.status)
  return resp.json()
}

/**
 * 12 板块实时资金流（后端 5 秒轮询缓存）。
 * 返回完整 json（含 ok/updated_at/last_error）：
 * ok=false 但 sectors 非空 = 后端沿用了上次数据，前端继续展示并可提示。
 */
export async function fetchSectors() {
  const json = await getJson('/api/sectors')
  if (!json.ok && (!json.sectors || !json.sectors.length)) {
    throw new Error(json.last_error || '后端数据不可用')
  }
  return json
}

/**
 * 板块指数当日分时（时间/现价/均价 + 昨收），后端 30 秒 TTL 缓存。
 * 返回 json：ok=false 但 points 非空 = 降级返回的旧数据。
 */
export async function fetchSectorTrend(code) {
  const json = await getJson('/api/sector_trend?code=' + encodeURIComponent(code))
  if (!json.points) throw new Error(json.last_error || '拉取分时数据失败')
  return json
}

/**
 * 个股实时行情批量（后端 5 秒缓存）。codes：6 位代码数组。
 * 返回 json.stocks：[{code,name,price,change_pct,amount,main_net_inflow,
 * main_net_pct,turnover_rate,total_mcap}]，与 codes 同序。
 */
export async function fetchStockQuotes(codes) {
  const json = await getJson('/api/stock_quotes?codes=' + encodeURIComponent(codes.join(',')))
  if (!json.stocks) throw new Error(json.last_error || '拉取个股行情失败')
  return json
}
