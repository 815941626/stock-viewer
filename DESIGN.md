# 前端重构设计（2026-08-08 重新敲定）

背景：当日会话卡死，前端重构代码全部丢失（磁盘回滚到纯柱状图版）。
本文档是重建的唯一依据，落盘防再丢。决策均已与用户确认。

## 页面结构：单页 + 详情覆盖层，无路由

```
┌─────────────────────────────────────────────┐
│ header：标题 + 口径说明                       │
├─────────────────────────────────────────────┤
│ ① 板块资金流柱状图（全局总览）                 │
│    SectorChart，高=净流入 宽=成交额 色=涨跌    │
│    悬浮=预览联动右下面板；点击=进入详情页        │
├──────────────────────┬──────────────────────┤
│ ② SectorLaunchBoard  │ ③ StockDetailPanel   │
│    板块启动看板（排行） │    预览面板：指数分时    │
│    悬浮预览/点击进详情  │    + 成分龙头股行情     │
└──────────────────────┴──────────────────────┘

④ SectorDetailPage（全屏覆盖层，点柱子/看板行打开，ESC/返回键关闭）：
   板块动量状态（score/v5/v15/加速）+ 指数分时图 + 成分龙头股明细表
```

selectedCode（预览选中）与 openSector（详情页）均由 App.vue 持有。
预览选中悬浮即变；详情页打开瞬间存快照，板块掉出 Top N 后页面仍可用。

## 数据流

- App.vue 持有唯一 5 秒轮询（/api/sectors），数据经 props 下发，组件不各自轮询板块数据。
- 指数分时数据按需：StockDetailPanel 在选中板块变化时拉 /api/sector_trend?code=xxx。
- 预览面板（悬浮驱动）体验三件套（2026-08-09 修复卡顿/抖动）：
  ① 200ms 悬浮防抖——快速划过不发请求；② 60s 客户端缓存——来回悬浮即点即现，
  切换时不清屏（旧数据保留，新数据到了无感替换）；③ 表格区限高滚动 + 吸顶表头，
  面板高度稳定不抖动。停稳后才启动 5 秒刷新（后端有缓存，只是读）。

## 组件划分

| 组件 | 职责 | props | emit |
|---|---|---|---|
| App.vue | 布局、轮询、selectedCode/openSector 状态 | - | - |
| SectorChart.vue | 柱状图总览 + 悬浮预览/点击进页 | sectors, selectedCode | select(code), open(code) |
| SectorLaunchBoard.vue | 排行表格，悬浮预览/点击进页 | sectors, selectedCode | select(code), open(code) |
| StockDetailPanel.vue | 预览容器：分时 + 成分股行情（5秒轮询） | sector | - |
| SectorDetailPage.vue | 全屏详情页：动量状态 + 分时 + 成分股明细 | sector | close |
| StockChart.vue | 板块指数分时图 | trend, sector | - |
| StockTable.vue | 成分龙头股表（detailed=明细列全开） | stocks, sector, detailed | - |
| api.js | fetchSectors / fetchSectorTrend / fetchStockQuotes | - | - |

## 后端接口

| 接口 | 状态 | 说明 |
|---|---|---|
| GET /api/sectors | 已有 | 动量池（40 板块）5 秒轮询缓存 + 全池动量计算，按 score 输出 Top 12；每板块附 `momentum={v5,v15,score,accel,dead}`、`members`（成分龙头股代码）、另有 `pool_size` |
| GET /api/sector_trend?code=BKxxxx | 新增 | 板块指数当日分时（时间/价/均价），30 秒 TTL 缓存，按需拉取 |
| GET /api/stock_quotes?codes=600519,... | 新增 | 成分龙头股实时行情（现价/涨跌/主力净流入/换手/市值），5 秒缓存按代码集合键，失败降级旧数据 |

sector_trend 返回形状：
```json
{"ok": true, "code": "BK1036", "pre_close": 1234.56,
 "points": [{"time": "09:31", "price": 1235.1, "avg": 1234.9}, ...]}
```

## 动量 V1（已实现 2026-08-08）

目标：排行依据从"当日累计净流入（滞后）"换成"资金流入动量（领先）"，抓刚启动的题材。

**指标定义**（`backend/momentum.py`，模块化，V2 拥挤度加在这里）：
- `v5` / `v15`：累计主力净流入在最近 5 / 15 分钟窗口的最小二乘斜率，单位 亿元/分钟。
  窗口锚定在序列最后一个数据点（非墙钟）。
- `accel`：v5 > v15 = 流入加速（"刚启动"特征）；反之为减速（"已霸榜"预警）。
- `score`：v5 的截面 z-score（去噪归一化），看板排序主体。
- `dead`：当日成交额 < `MOMENTUM.min_amount_yi`（10 亿）= 死板块，不参与排序、置底展示。

**数据链路**：
- 实时：现有 5 秒 ulist 轮询快照逐次追加进历史缓冲（deque，400 点 ≈ 33 分钟）。
- 冷启动回填：启动时按东财分钟级资金流接口（`fflow/kline/get` klt=1，push2his→push2delay
  降级）拉最近 30 分钟填窗，v15 开机即用。回填的累计值与 ulist f62 同口径，直接拼接。
- 交易时段闸（`in_trading_session`）：盘外累计值冻结，采样点不入库，
  否则窗口锚点漂到无数据空档导致斜率全 None。

**看板排序**（`SectorLaunchBoard.vue` 的单个 computed）：死板块置底 → score 降序。
后端换排序口径只动这一处。

**已知粗边**：v5/v15 都接近 0 时 accel 会因浮点抖动随机翻转（如 0.00 vs -0.01），
V1 不处理；后续可加死区阈值。

## 预留位（本次不做，架构留口）

1. **拥挤度（V2）**：看板表格预留徽标位，暂不实现。

## 板块详情页（已实现 2026-08-09）

点击柱子/看板行 → 全屏 SectorDetailPage：动量状态（score/v5/v15/加减速）+
指数分时图（30 秒轮询）+ 成分龙头股明细表（5 秒轮询，列：现价/涨跌幅/
主力净流入/净占比/换手率/成交额/市值）。成分股 = pool.json 的 members
（名单内属于该板块的票），经 /api/sectors 的 members 字段下发。
实现为全屏覆盖层而非 vue-router 页面，保持单页架构；ESC/返回键关闭。

## 自下而上动量池（已实现 2026-08-08）

龙头股名单（LEADER_STOCKS）→ 东财 F10 核心题材反推（IS_PRECISE=1，
沪深300/融资融券等指数风格成分天然滤掉）→ 概念频次聚合 → pool.json（40 板块）
→ 全池动量计算 → /api/sectors 按 score 输出 Top 12。前端零改动。

- **生成**：`python backend/rebuild_pool.py`（改 LEADER_STOCKS 后手动重跑，
  55 次 F10 请求约 1.5 分钟；不占启动/轮询）。
- **筛选规则**（config.POOL）：freq≥2 共振下限（freq=1 是单票伪板块）；
  无频次上限；风格/地域/宏观箩筐（央国企改革/超级品牌/深圳特区等 17 个）
  靠 exclude 黑名单显式剔除；排序 = 频次降序 + BOARD_RANK 升序，截 40 板块。
- **加载**：main.py `_load_pool()` 启动读 pool.json；缺失/损坏回退固定 SECTORS。
- **教训**：小样本（55 票）下用频次上限猜箩筐会失灵——存储芯片/半导体概念/
  人工智能等真题材因名单集中而高频，被误杀；箩筐必须显式黑名单。
- **已知边界**：Top 12 随动量轮换，选中板块掉榜时前端自动切榜首；
  赛轮轮胎 F10 无核心题材记录，在名单中但不派生板块。

## 明确不做

- 不引入 vue-router（单页足够）、不引入状态管理库（props/emit 足够）
- 柱状图暂不开动画（保持现状，稳定性优先）
- 配色沿用现有深色主题（红涨绿跌，style.css）

## 验证

`npm run build` 通过 + 浏览器手动核对三区域联动（点柱子/点行 → 详情切换）。
