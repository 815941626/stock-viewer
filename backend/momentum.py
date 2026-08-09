# -*- coding: utf-8 -*-
"""
momentum.py - 动量指标模块（V1：多窗口斜率动量 v5 vs v15）

设计决策（详见 DESIGN.md 与项目记忆）：
  - 动量做排序主体：v5（5分钟斜率）为排序基准，与 v15（15分钟斜率）对比
    得到加速信号（v5 > v15 = 流入加速，"刚启动"特征）
  - 去噪：v5 截面 z-score 归一化；死板块（成交额极低）不参与排序，
    避免漏掉小盘早期启动的同时滤掉死板块噪声
  - 指标模块化：V2 拥挤度等指标应加在本模块，main.py 只消费输出

输入约定：
  历史点序列 = [(ts unix秒, 累计主力净流入 元), ...]，按时间升序。
  启动回填的分钟点与实时 5 秒采样点可混排，最小二乘按真实时间戳处理。

单位约定：
  内部金额一律为元；对外输出的斜率换算为 亿元/分钟（展示单位）。
"""
import time


def in_trading_session(ts):
    """A股交易时段判断（周一~五 9:30-11:30 / 13:00-15:00）。

    盘外时段东财的累计净流入是冻结值（重复上一交易日收盘数），
    若让盘外采样点进入历史缓冲，窗口锚点会漂到无数据的空档，
    斜率全部失效——因此采样入库前先过这道闸（main.py 使用）。
    法定节假日暂不处理（罕见，届时界面显示 -）。"""
    t = time.localtime(ts)
    if t.tm_wday >= 5:  # 周六/周日
        return False
    m = t.tm_hour * 60 + t.tm_min
    return (9 * 60 + 30 <= m <= 11 * 60 + 30) or (13 * 60 <= m <= 15 * 60)


def slope_per_min(points, window_min, min_coverage=0.6):
    """
    累计净流入在最近 window_min 分钟窗口内的最小二乘斜率，返回 亿元/分钟。

    窗口锚定在最后一个数据点（而非墙钟）——非交易时段也能得到
    最近交易日尾盘动量；盘中二者等价。窗口内数据覆盖不足返回 None。
    """
    if not points or window_min <= 0:
        return None
    ref = points[-1][0]
    t0 = ref - window_min * 60
    xs = []
    ys = []
    for ts, v in points:
        if ts > t0 and v is not None:
            xs.append(ts)
            ys.append(v)
    if len(xs) < 3:
        return None
    # 覆盖度检查：实际时间跨度不足窗口的一定比例时（如刚启动回填不全），斜率不可信
    if xs[-1] - xs[0] < window_min * 60 * min_coverage:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / var * 60 / 1e8  # 元/秒 -> 亿元/分钟


def zscores(values):
    """截面 z-score。输入输出同长列表，None 保持 None 且不参与统计。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return [None] * len(values)
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    if sd == 0:
        return [0.0 if v is not None else None for v in values]
    return [None if v is None else (v - mean) / sd for v in values]


def compute_momentum(sectors, histories, cfg):
    """
    为全部板块计算动量 V1 指标。

    sectors:   fetcher.fetch_sector_flow() 的输出（用其 code/amount）
    histories: {code: [(ts, 累计主力净流入 元), ...]}
    cfg:       config.MOMENTUM

    返回 {code: {...}}，每项：
      v5/v15   短/长窗口斜率（亿元/分钟），None = 数据不足
      accel    True = 加速（v5 > v15）；None = 无法比较
      score    v5 的截面 z-score（排序主体）；死板块与数据不足为 None
      dead     True = 成交额低于阈值，不参与排序
    """
    min_amount = cfg["min_amount_yi"] * 1e8
    coverage = cfg["min_window_coverage"]

    codes = [s["code"] for s in sectors]
    dead = {c: (s.get("amount") or 0) < min_amount
            for s, c in zip(sectors, codes)}

    v5 = {c: slope_per_min(histories.get(c) or [], cfg["short_window_min"], coverage)
          for c in codes}
    v15 = {c: slope_per_min(histories.get(c) or [], cfg["long_window_min"], coverage)
           for c in codes}

    # z-score 只在存活板块间做，死板块不参与、不扭曲截面基准
    live_codes = [c for c in codes if not dead[c]]
    score = dict(zip(live_codes, zscores([v5[c] for c in live_codes])))

    result = {}
    for c in codes:
        a, b = v5[c], v15[c]
        result[c] = {
            "v5": a,
            "v15": b,
            "accel": None if (a is None or b is None) else a > b,
            "score": None if dead[c] else score.get(c),
            "dead": dead[c],
        }
    return result


# ----------------------------------------------------------------------
# 自校验：python momentum.py（不依赖网络，合成数据验证指标逻辑）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import time

    def series(segments):
        """按分段斜率（亿元/分钟）生成 5 秒间隔的累计净流入点列"""
        pts = []
        t = time.time() - sum(m for m, _ in segments) * 60
        v = 0.0
        for minutes, yi_per_min in segments:
            step = yi_per_min * 1e8 / 60 * 5  # 亿元/分钟 -> 每5秒增量(元)
            for _ in range(int(minutes * 60 / 5)):
                t += 5
                v += step
                pts.append((t, v))
        return pts

    # 场景1 恒定流入 2亿/分钟：v5 ≈ v15 ≈ 2，不加速不减速
    flat = series([(20, 2.0)])
    # 场景2 刚启动：前17分钟持平，最近3分钟 6亿/分钟涌入：v5 远大于 v15，加速
    launch = series([(17, 0.0), (3, 6.0)])
    # 场景3 冲高回落：前15分钟 6亿/分钟，最近5分钟持平：v5 ≈ 0 < v15，减速
    fade = series([(15, 6.0), (5, 0.0)])

    sectors = [
        {"code": "FLAT", "amount": 100e8},
        {"code": "LAUNCH", "amount": 50e8},
        {"code": "FADE", "amount": 100e8},
        {"code": "DEAD", "amount": 3e8},   # 成交额低于阈值 -> 死板块
    ]
    histories = {"FLAT": flat, "LAUNCH": launch, "FADE": fade, "DEAD": list(launch)}

    cfg = {"short_window_min": 5, "long_window_min": 15,
           "min_window_coverage": 0.6, "min_amount_yi": 10.0}
    result = compute_momentum(sectors, histories, cfg)

    def f(x):
        return "-" if x is None else f"{x:+.2f}"

    print(f"{'板块':<8}{'v5':>8}{'v15':>8}{'score':>8}  accel   dead")
    for s in sectors:
        m = result[s["code"]]
        print(f"{s['code']:<8}{f(m['v5']):>8}{f(m['v15']):>8}{f(m['score']):>8}  "
              f"{str(m['accel']):<7} {m['dead']}")

    r = result
    assert abs(r["FLAT"]["v5"] - 2.0) < 0.05, "恒定流入 v5 应 ≈ 2亿/分钟"
    assert abs(r["FLAT"]["v15"] - 2.0) < 0.05, "恒定流入 v15 应 ≈ 2亿/分钟"
    assert r["LAUNCH"]["accel"] is True, "刚启动板块应加速（v5 > v15）"
    assert r["FADE"]["accel"] is False, "冲高回落板块应减速（v5 < v15）"
    assert r["LAUNCH"]["v5"] > r["FLAT"]["v5"], "启动中的 v5 应超过恒定流入板块"
    assert r["DEAD"]["dead"] and r["DEAD"]["score"] is None, "死板块不应参与排序"
    assert all(r[c]["score"] is not None for c in ("FLAT", "LAUNCH", "FADE"))

    # 交易时段闸：2026-08-07 为周五（东财接口确认的最近交易日）
    def ts(s):
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))
    assert in_trading_session(ts("2026-08-07 10:00")), "周五盘中应在交易时段"
    assert in_trading_session(ts("2026-08-07 14:59")), "尾盘应在交易时段"
    assert not in_trading_session(ts("2026-08-07 12:00")), "午休不在交易时段"
    assert not in_trading_session(ts("2026-08-08 10:00")), "周六不在交易时段"

    print("\n自校验通过：斜率/加速判断/z-score/死板块/交易时段逻辑均符合预期")
