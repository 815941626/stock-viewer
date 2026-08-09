# -*- coding: utf-8 -*-
"""
main.py - FastAPI 入口

职责：
  1. 启动后每 POLL_INTERVAL 秒调一次 fetcher 拉东财，结果写入内存缓存
  2. GET /api/sectors 返回当前板块列表（前端怎么轮询都只打缓存，不直接碰东财），
     附带动量 V1 指标（v5/v15 斜率、加速信号、截面 z-score）
  3. 某次拉取失败时沿用上次有效数据，只记录错误状态，不让柱子断
  4. 开 CORS，方便本地前端 dev server 跨域访问
  5. 维护主力净流入历史缓冲：启动时按分钟级资金流回填（冷启动），
     之后每次轮询追加 5 秒采样点，供 momentum.py 计算窗口斜率

启动：
    cd backend
    uvicorn main:app --reload
验证：
    http://127.0.0.1:8000/api/sectors      查看动量池 Top N 板块 JSON
    http://127.0.0.1:8000/docs             FastAPI 自动生成的接口文档

板块来源已切换为"自下而上"动量池（rebuild_pool.py 由龙头股名单反推生成
pool.json；缺失时回退 config.SECTORS）。接口形状方向无关：/api/sectors
从全池算动量后按 score 输出 Top N，字段与结构不变，前端零改动。
"""
import asyncio
import json
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import (CONGESTION, CONGESTION_LOG_DIR, MOMENTUM, POOL,
                    POOL_CACHE_FILE, POLL_INTERVAL, SECTORS, TREND_CACHE_TTL)
from fetcher import (fetch_sector_flow, fetch_sector_flow_history,
                     fetch_sector_trend, fetch_stock_quotes)
from congestion import compute_congestion
from momentum import compute_momentum, in_trading_session, slope_per_min

# 内存缓存：所有对东财的请求都收敛到这里，前端只读缓存
_cache = {
    "sectors": [],        # 最近一次成功拉到的板块列表
    "updated_at": None,   # 最近一次成功拉取的时间戳（秒）
    "ok": False,          # 最近一次拉取是否成功
    "last_error": None,   # 最近一次失败的错误信息（成功时清空）
}

# 动量历史缓冲：code -> deque[(ts, 当日累计主力净流入·元)]
# 启动回填的分钟点与实时 5 秒采样点同口径（日内累计，见 fetch_sector_flow_history）
HISTORY_MAXLEN = 400  # 5 秒采样约 33 分钟，覆盖 15 分钟窗口有余量
_histories = {}

# 拥挤度 V2：小单净占比（占成交额%）历史缓冲，算散户涌入斜率用
_small_histories = {}

# 观察日志节流
_last_cong_log = 0.0

# 自下而上动量池（rebuild_pool.py 生成 pool.json）；读取失败时回退固定 SECTORS
_pool_sectors = []


def _load_pool():
    """读 pool.json 转成 SECTORS 同形状 [{code, display, em_name}]。
    池子的"选哪些板块"逻辑全在这里——接口形状方向无关，前端零改动。"""
    global _pool_sectors
    try:
        with open(POOL_CACHE_FILE, encoding="utf-8") as f:
            pool = json.load(f)
        sectors = [{"code": s["code"], "display": s["name"], "em_name": s["name"],
                    "members": s.get("members") or []}
                   for s in pool.get("sectors") or []]
        if sectors:
            _pool_sectors = sectors
            print(f"[pool] 已加载自下而上动量池：{len(sectors)} 板块"
                  f"（生成于 {pool.get('generated_at')}，"
                  f"{pool.get('leader_count')} 只龙头股反推）")
            return
    except Exception as err:
        print(f"[pool] pool.json 读取失败: {err}")
    _pool_sectors = list(SECTORS)
    print(f"[pool] 回退固定 SECTORS（{len(SECTORS)} 板块）")


def _small_ratio(s):
    """小单净占比（占成交额%）：散户追高程度的核心观测量（拥挤度 V2）。"""
    small, amount = s.get("small_net_inflow"), s.get("amount")
    if small is None or not amount:
        return None
    return small / amount * 100


def _append_history(sectors):
    """每次成功拉取后写入一份实时主力净流入快照（动量斜率计算的数据源），
    以及小单净占比快照（拥挤度 V2 的散户涌入斜率）。
    盘外时段累计值冻结（重复上一交易日收盘数），采样点不入库，
    否则窗口锚点会漂到无数据的空档。"""
    now = time.time()
    if not in_trading_session(now):
        return
    for s in sectors:
        inflow = s.get("main_net_inflow")
        if inflow is None:
            continue
        buf = _histories.setdefault(s["code"], deque(maxlen=HISTORY_MAXLEN))
        if buf and buf[-1][0] >= now:  # 防御时钟回拨/重复写入
            continue
        buf.append((now, inflow))
        ratio = _small_ratio(s)
        if ratio is not None:
            sbuf = _small_histories.setdefault(s["code"], deque(maxlen=HISTORY_MAXLEN))
            if not sbuf or sbuf[-1][0] < now:
                sbuf.append((now, ratio))


def _maybe_log_congestion(sectors):
    """观察日志：每 log_interval 秒为每个板块写一行 jsonl，
    复盘用——"位置标了 70% 的板块，T+1/T+3 是不是真退了"。
    失败只打印不阻塞主流程。"""
    global _last_cong_log
    now = time.time()
    if now - _last_cong_log < CONGESTION["log_interval"]:
        return
    _last_cong_log = now
    try:
        mom = compute_momentum(sectors, _histories, MOMENTUM)
        os.makedirs(CONGESTION_LOG_DIR, exist_ok=True)
        path = os.path.join(
            CONGESTION_LOG_DIR, "congestion_" + time.strftime("%Y%m%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for s in sectors:
                ratio = _small_ratio(s)
                cong = compute_congestion(ratio, s.get("up_count"),
                                          s.get("down_count"), s.get("chg_60d"),
                                          s.get("chg_ytd"), CONGESTION)
                m = mom.get(s["code"]) or {}
                f.write(json.dumps({
                    "ts": int(now), "code": s["code"], "name": s.get("display"),
                    "position": cong["position"], "parts": cong["parts"],
                    "small_pct": None if ratio is None else round(ratio, 3),
                    "v5": m.get("v5"), "score": m.get("score"),
                    "chg": s.get("change_pct"),
                }, ensure_ascii=False) + "\n")
    except Exception as err:
        print(f"[congestion-log] 写入失败: {err}")


async def _do_refresh():
    """拉一次东财并写缓存。fetch 是同步阻塞 IO，丢线程池跑，别堵住事件循环。
    失败时不动 sectors（沿用旧数据），只把 ok 置 False 并记录错误。"""
    try:
        sectors = await asyncio.to_thread(fetch_sector_flow, _pool_sectors)
        _cache["sectors"] = sectors
        _cache["updated_at"] = time.time()
        _cache["ok"] = True
        _cache["last_error"] = None
        _append_history(sectors)
        _maybe_log_congestion(sectors)
    except Exception as err:  # 网络/限流/解析失败：保留旧数据，记录状态
        _cache["ok"] = False
        _cache["last_error"] = str(err)


async def _refresh_loop():
    """后台任务：先睡 POLL_INTERVAL 再拉，循环往复（首次拉取在 lifespan 里已做）。"""
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        await _do_refresh()


async def _backfill_histories():
    """冷启动：为每个板块拉最近 backfill_minutes 的分钟级资金流填窗，
    避免启动后干等 15 分钟才有 v15。
    串行 + 间隔 0.5 秒：并发突发容易触发东财断连；单板块失败只影响该板块。"""
    for cfg_sector in _pool_sectors:
        code = cfg_sector["code"]
        try:
            points = await asyncio.to_thread(fetch_sector_flow_history, code)
        except Exception as err:
            print(f"[backfill] {code} 失败: {err}")
            continue
        # 截止线锚定在数据自身的最新时间戳（而非墙钟）：
        # 盘外/周末启动时最新点是上一交易日收盘，按墙钟算会把有效点全滤掉
        cutoff = points[-1]["ts"] - MOMENTUM["backfill_minutes"] * 60
        buf = _histories.setdefault(code, deque(maxlen=HISTORY_MAXLEN))
        for p in points:
            if p["ts"] >= cutoff:
                buf.append((p["ts"], p["inflow"]))
        # 回填点早于任何实时点；排序一次，防御性保证斜率计算要求的时间升序
        _histories[code] = deque(sorted(buf), maxlen=HISTORY_MAXLEN)
        print(f"[backfill] {code}: 缓冲 {len(_histories[code])} 个分钟级点")
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先定池子（决定轮询/回填/动量算哪些板块），再回填再开轮询：
    # 回填的分钟点早于任何实时采样点，历史缓冲保持时间升序
    _load_pool()
    await _backfill_histories()
    # 启动即先拉一次，让前端一连上就有数据；随后交给后台轮询
    await _do_refresh()
    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="板块资金流观测后端", lifespan=lifespan)

# 本地开发放开跨域；上线时可把 allow_origins 收紧到前端实际来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _public_sector(s):
    """只暴露前端需要的字段，去掉内部校验位（_super_big/_big 等）。"""
    return {
        "code": s.get("code"),
        "name": s.get("name"),                 # 东财板块名
        "display": s.get("display"),           # 柱子上显示的叫法
        "change_pct": s.get("change_pct"),     # 涨跌幅 %（映射柱子颜色）
        "amount": s.get("amount"),             # 成交额·元（映射柱子宽度）
        "main_net_inflow": s.get("main_net_inflow"),  # 主力净流入·元（映射柱子高度）
        "main_net_pct": s.get("main_net_pct"),        # 主力净占比 %
    }


@app.get("/")
def root():
    """服务自检：是否就绪、数据新鲜度。"""
    return {
        "service": "sector-flow-backend",
        "ok": _cache["ok"],
        "updated_at": _cache["updated_at"],
        "poll_interval": POLL_INTERVAL,
        "sector_count": len(_cache["sectors"]),
    }


@app.get("/api/sectors")
def get_sectors():
    """板块动量榜：自下而上动量池全池计算动量，按 score 降序输出 top_n
    （死板块不上榜）。接口形状方向无关，前端零改动。"""
    sectors = _cache["sectors"]
    mom = compute_momentum(sectors, _histories, MOMENTUM)
    # 成分龙头股在池配置里（拉取快照不含此字段），按 code 映射
    members_of = {c["code"]: c.get("members") or [] for c in _pool_sectors}
    items = []
    for s in sectors:
        item = _public_sector(s)
        item["momentum"] = mom.get(s["code"])
        # 池内成分龙头股代码（详情页展示用；固定 SECTORS 回退时为空）
        item["members"] = members_of.get(s["code"]) or []
        # 拥挤度 V2：位置徽标（不参与排序）+ 分量明细 + 小单涌入斜率（观察量）
        ratio = _small_ratio(s)
        cong = compute_congestion(ratio, s.get("up_count"), s.get("down_count"),
                                  s.get("chg_60d"), s.get("chg_ytd"), CONGESTION)
        cong["small_slope"] = slope_per_min(
            list(_small_histories.get(s["code"]) or []),
            CONGESTION["small_slope_window_min"])
        item["congestion"] = cong
        items.append(item)

    def _score(it):
        v = (it["momentum"] or {}).get("score")
        return v if v is not None else float("-inf")

    alive = [it for it in items if not (it["momentum"] or {}).get("dead")]
    alive.sort(key=_score, reverse=True)
    return {
        "ok": _cache["ok"],
        "updated_at": _cache["updated_at"],
        "poll_interval": POLL_INTERVAL,
        "last_error": _cache["last_error"],
        "pool_size": len(sectors),
        "sectors": alive[:POOL["top_n"]],
    }


# ------------------------------------------------------------------
# 个股实时行情（详情页成分龙头股）
# ------------------------------------------------------------------

# 按代码集合缓存（不同详情页各一个 key），5 秒内重复请求只读缓存
_quote_cache = {}
_QUOTE_CACHE_MAX_KEYS = 20


@app.get("/api/stock_quotes")
def get_stock_quotes(codes: str):
    """个股实时行情。codes = 逗号分隔的 6 位代码；5 秒缓存；
    拉取失败且有旧缓存时降级返回旧数据（stale=True）。"""
    code_list = [c.strip() for c in codes.split(",")]
    code_list = [c for c in code_list if re.match(r"^\d{6}$", c)]
    if not code_list:
        return {"ok": False, "last_error": "无效的股票代码"}
    key = ",".join(sorted(code_list))
    now = time.time()
    hit = _quote_cache.get(key)
    if hit and now - hit["ts"] < POLL_INTERVAL:
        return {"ok": True, "stocks": hit["stocks"]}
    try:
        stocks = fetch_stock_quotes(code_list)
    except Exception as err:
        if hit:
            return {"ok": False, "stale": True, "last_error": str(err),
                    "stocks": hit["stocks"]}
        return {"ok": False, "last_error": str(err)}
    if len(_quote_cache) >= _QUOTE_CACHE_MAX_KEYS:
        oldest = min(_quote_cache, key=lambda k: _quote_cache[k]["ts"])
        _quote_cache.pop(oldest, None)
    _quote_cache[key] = {"ts": now, "stocks": stocks}
    return {"ok": True, "stocks": stocks}


# ------------------------------------------------------------------
# 板块指数分时（详情面板按需拉取）
# ------------------------------------------------------------------

# code -> {"data": {...}, "ts": 秒}。按需拉取 + TTL 缓存，把对东财的请求收敛
_trend_cache = {}

# 只放行 BKxxxx 形式的板块代码，避免接口被当作任意 secid 代理
_BK_CODE = re.compile(r"^BK\d{4}$")


@app.get("/api/sector_trend")
def get_sector_trend(code: str):
    """板块指数当日分时（时间/现价/均价 + 昨收）。
    TTL 内读缓存；拉取失败且有旧缓存时降级返回旧数据（stale=True）。
    同步 def，FastAPI 自动丢线程池，不堵事件循环。"""
    if not _BK_CODE.match(code):
        return {"ok": False, "last_error": "无效的板块代码"}
    now = time.time()
    hit = _trend_cache.get(code)
    if hit and now - hit["ts"] < TREND_CACHE_TTL:
        return {"ok": True, **hit["data"]}
    try:
        data = fetch_sector_trend(code)
    except Exception as err:
        if hit:
            return {"ok": False, "stale": True, "last_error": str(err), **hit["data"]}
        return {"ok": False, "last_error": str(err)}
    _trend_cache[code] = {"data": data, "ts": now}
    return {"ok": True, **data}
