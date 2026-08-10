# -*- coding: utf-8 -*-
"""
etfflow.py - ETF 申赎拆解（把超大单资金流里的被动盘分出来）

背景（2026-08-11）：半导体概念出现"主力 -100亿但价格站回均价"，
其中超大单档位被 ETF 实物申赎的篮子买卖污染——赎回时做市商卖出篮子股，
全部计入超大单流出。不拆开就分不清是主动出货还是被动赎回。

方法：
  1. 每个交易日 15:05 后快照一次各代理 ETF 的份额（市值÷价格）
  2. Δ份额 × 当日价格 ≈ 当日 ETF 驱动的篮子买卖额（创建为正、赎回为负）
  3. 主动资金 ≈ 超大单总流出 − ETF 申赎估算（解读时人工相减）

已知误差（诚实标注）：
  - ETF 篮子权重 ≠ 板块指数权重，申赎存在现金替代模式 → 估算误差约 ±20-30%
  - 份额收盘后落定，只能事后解释，不能盘中实时
"""
import json
import os
import time


def load_shares(path):
    """读份额档案 {YYYYMMDD: {etf_code: {name, price, shares, board}}}"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_shares(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _snapshot_day(now):
    """当前行情归属的交易日标签 = 最近一个已过 15:05 的工作日收盘日。
    收盘后到次日开盘前，行情都显示该日收盘值（跨午夜重启也不会错标）。"""
    import datetime as _dt
    t = time.localtime(now)
    day = _dt.date(t.tm_year, t.tm_mon, t.tm_mday)
    if t.tm_hour * 60 + t.tm_min < 15 * 60 + 5:
        day -= _dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= _dt.timedelta(days=1)
    return day.strftime("%Y%m%d")


def record_daily_shares(path, fetch_quotes, proxies, now=None):
    """收盘后快照当日份额（盘中不写：行情是实时值不是收盘值）；
    已有该交易日标签则跳过。返回是否写入了新快照。
    fetch_quotes(codes) 抛异常时向上抛，由调用方兜底。"""
    now = now if now is not None else time.time()
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    # 盘中（含集合竞价）：行情实时变动，不能当收盘份额用
    if 9 * 60 + 15 <= m < 15 * 60 + 5:
        return False
    day = _snapshot_day(now)
    data = load_shares(path)
    if day in data:
        return False
    entry = {}
    for board, codes in proxies.items():
        for q in fetch_quotes(codes):
            if q.get("shares"):
                entry[q["code"]] = {"name": q["name"], "price": q["price"],
                                    "shares": q["shares"], "board": board}
    if not entry:
        return False
    data[day] = entry
    for d in sorted(data)[:-500]:  # 只留约两年
        data.pop(d, None)
    save_shares(path, data)
    return True


def compute_etf_flow(path, board_code, proxies):
    """最近两个快照日的 ETF 申赎金额拆解。
    创建（份额增）为正 = 篮子买压；赎回（份额减）为负 = 篮子卖压。"""
    codes = set(proxies.get(board_code) or [])
    if not codes:
        return {"ok": False, "message": "该板块未配置 ETF 代理"}
    data = load_shares(path)
    days = sorted(d for d in data if any(c in data[d] for c in codes))
    if len(days) < 2:
        return {"ok": False, "message": "份额快照积累中（需至少两个交易日）"}
    d0, d1 = days[-2], days[-1]
    rows, total = [], 0.0
    for c in sorted(codes):
        q0, q1 = data[d0].get(c), data[d1].get(c)
        if not q0 or not q1:
            continue
        d_shares = q1["shares"] - q0["shares"]
        flow = d_shares * q1["price"]
        total += flow
        rows.append({"code": c, "name": q1["name"],
                     "d_shares": round(d_shares), "flow": round(flow),
                     "shares": round(q1["shares"])})
    return {"ok": True, "dates": [d0, d1], "etfs": rows, "total_flow": round(total)}


# ----------------------------------------------------------------------
# 自校验：python etfflow.py（临时档案，合成两日份额）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    proxies = {"BK0917": ["512480", "159995"]}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "etf_shares.json")
        save_shares(path, {
            "20260810": {
                "512480": {"name": "半导体ETF国联安", "price": 1.10,
                           "shares": 190e8, "board": "BK0917"},
                "159995": {"name": "芯片ETF华夏", "price": 1.20,
                           "shares": 234e8, "board": "BK0917"},
            },
            "20260811": {
                "512480": {"name": "半导体ETF国联安", "price": 1.08,
                           "shares": 185e8, "board": "BK0917"},  # 赎回5亿份
                "159995": {"name": "芯片ETF华夏", "price": 1.21,
                           "shares": 236e8, "board": "BK0917"},  # 创建2亿份
            },
        })
        r = compute_etf_flow(path, "BK0917", proxies)
        assert r["ok"] and r["dates"] == ["20260810", "20260811"]
        total_check = -5e8 * 1.08 + 2e8 * 1.21
        assert abs(r["total_flow"] - round(total_check)) < 2, r
        by_code = {e["code"]: e for e in r["etfs"]}
        assert by_code["512480"]["flow"] < 0, "赎回应为负（卖压）"
        assert by_code["159995"]["flow"] > 0, "创建应为正（买压）"
        # 未配置板块 / 单日档案的边界
        assert not compute_etf_flow(path, "BK9999", proxies)["ok"]
        print("自校验通过：赎回为负/创建为正/边界正常")
        print(f"示例：赎回5亿份+创建2亿份 → 合计 {r['total_flow']/1e8:.2f} 亿")

    # 快照闸：盘中不写；收盘后写；跨午夜/周末标签归属正确；当日不重写
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        fake = lambda codes: [{"code": c, "name": "X", "price": 1.0,
                               "mcap": 1e8, "shares": 1e8} for c in codes]
        mon_1510 = time.mktime(time.strptime("2026-08-10 15:10", "%Y-%m-%d %H:%M"))
        mon_1400 = time.mktime(time.strptime("2026-08-10 14:00", "%Y-%m-%d %H:%M"))
        sat_1510 = time.mktime(time.strptime("2026-08-08 15:10", "%Y-%m-%d %H:%M"))
        wed_0047 = time.mktime(time.strptime("2026-08-12 00:47", "%Y-%m-%d %H:%M"))
        assert _snapshot_day(mon_1510) == "20260810"
        assert _snapshot_day(sat_1510) == "20260807", "周六应归属周五收盘"
        assert _snapshot_day(wed_0047) == "20260811", "周三凌晨应归属周二收盘"
        assert record_daily_shares(path, fake, proxies, mon_1400) is False, "盘中不写"
        assert record_daily_shares(path, fake, proxies, mon_1510) is True
        assert record_daily_shares(path, fake, proxies, mon_1510) is False, "当日不重写"
        assert record_daily_shares(path, fake, proxies, wed_0047) is True, "周三凌晨补周二"
        assert set(load_shares(path)) == {"20260810", "20260811"}
        print("快照闸通过：盘中不写/收盘写/跨午夜标签正确/不重写")
