# -*- coding: utf-8 -*-
"""
chipflow.py - 筹码结构四象限模块（主力 vs 小单背离）

设计定稿见 未来计划.md 第 5 节（2026-08-10）。核心思想：
  动量回答"油门加多快"，拥挤度回答"涨到哪一段"，
  筹码结构回答"现在谁在买"——派发象限就是"已经晚了"的直接定义。

状态空间（全部走净占比空间，消除日内时点影响）：
  main_pct = 主力净占比（f184）   small_pct = 小单净流入/成交额*100
  吸筹     main>+X 且 small<-Y   主力买散户卖（早期，最好的买点区域）
  共振涌入 main>+X 且 small>+Y   都在买（主升中段）
  派发     main<-X 且 small>+Y   主力出货散户接盘（晚期，危险）
  双逃     main<-X 且 small<-Y   都在跑（崩后/出清）
  其余为中性。

工程细节：
  - 迟滞带：进入用 enter_x/y，维持用 exit_x/y（更低），
    防止 5 秒轮询下标签在阈值边上来回跳
  - 开盘预热：前 warmup_min 分钟不定象限（比值极端噪声大）
  - 强度连续量：派发象限给接盘强度 = 小单净流入/|主力净流出|，
    吸筹象限给吸筹强度 = 主力净流入/|小单净流出|（比标签更值钱）

数据 caveat（与拥挤度同源）：东财大单/小单按委托单大小启发式划分，
拆单会污染；大单卖也可能是 ETF 申赎/调仓——标签是警告不是判决。
"""
import time

ABSORB = "吸筹"
SURGE = "共振涌入"
DISTRIBUTE = "派发"
PANIC = "双逃"
EARLY = "早盘"


def minutes_since_open(ts):
    """距开盘的交易分钟数（0-240）；午休/盘外/周末返回 None（无预热概念）。"""
    t = time.localtime(ts)
    if t.tm_wday >= 5:
        return None
    m = t.tm_hour * 60 + t.tm_min
    if 9 * 60 + 30 <= m < 11 * 60 + 30:
        return m - (9 * 60 + 30)
    if 13 * 60 <= m <= 15 * 60:
        return 120 + m - 13 * 60
    return None


def _quadrant(main_pct, small_pct, x, y):
    """按给定阈值判象限；不满足任何条件返回 None（中性）。"""
    if main_pct > x and small_pct < -y:
        return ABSORB
    if main_pct > x and small_pct > y:
        return SURGE
    if main_pct < -x and small_pct > y:
        return DISTRIBUTE
    if main_pct < -x and small_pct < -y:
        return PANIC
    return None


def flow_pattern(main_net, small_net, amount, main_pct,
                 prev_pattern, cfg, now_ts=None):
    """
    判定筹码结构象限（带迟滞与早盘预热）。

    main_net   主力净流入（元，f62）
    small_net  小单净流入（元，f84）
    amount     成交额（元，f6）
    main_pct   主力净占比（%，f184，东财已算好）
    prev_pattern 上一轮象限（迟滞用，首次传 None）
    返回 {pattern, absorption, accumulate, main_pct, small_pct}
      absorption 派发象限的接盘强度（小单/|主力|），其余 None
      accumulate 吸筹象限的吸筹强度（主力/|小单|），其余 None
    """
    if main_net is None or small_net is None or not amount or main_pct is None:
        return {"pattern": None, "absorption": None, "accumulate": None,
                "main_pct": main_pct, "small_pct": None}

    small_pct = small_net / amount * 100.0

    m = minutes_since_open(now_ts if now_ts is not None else time.time())
    if m is not None and m < cfg["warmup_min"]:
        return {"pattern": EARLY, "absorption": None, "accumulate": None,
                "main_pct": round(main_pct, 3), "small_pct": round(small_pct, 3)}

    cur = _quadrant(main_pct, small_pct, cfg["enter_x"], cfg["enter_y"])
    if cur is None and prev_pattern and prev_pattern != EARLY:
        # 未达进入阈值：若仍满足维持阈值（迟滞带内），保持原象限
        if _quadrant(main_pct, small_pct, cfg["exit_x"], cfg["exit_y"]) == prev_pattern:
            cur = prev_pattern

    absorption = None
    accumulate = None
    if cur == DISTRIBUTE and main_net < 0:
        absorption = small_net / abs(main_net)
    if cur == ABSORB and small_net < 0:
        accumulate = main_net / abs(small_net)

    return {
        "pattern": cur,
        "absorption": None if absorption is None else round(absorption, 3),
        "accumulate": None if accumulate is None else round(accumulate, 3),
        "main_pct": round(main_pct, 3),
        "small_pct": round(small_pct, 3),
    }


# ----------------------------------------------------------------------
# 自校验：python chipflow.py（含真实案例：2026-08-10 数据中心派发日）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    cfg = {"enter_x": 1.5, "enter_y": 1.5, "exit_x": 1.0, "exit_y": 1.0,
           "warmup_min": 15}
    # 周一盘中时间戳（避开早盘预热）
    mid_session = time.mktime(time.strptime("2026-08-10 10:30", "%Y-%m-%d %H:%M"))
    early_session = time.mktime(time.strptime("2026-08-10 09:40", "%Y-%m-%d %H:%M"))

    # 场景1 真实案例：数据中心 2026-08-10 主力-88.8亿 小单+66.6亿 → 派发
    r = flow_pattern(-88.82e8, 66.62e8, 1373e8, -6.47, None, cfg, mid_session)
    print("数据中心案例:", r)
    assert r["pattern"] == DISTRIBUTE and abs(r["absorption"] - 0.75) < 0.01

    # 场景2 吸筹：主力买散户卖（small_pct=-2.0% 越过阈值）
    r2 = flow_pattern(30e8, -16e8, 800e8, 3.75, None, cfg, mid_session)
    print("吸筹案例:", r2)
    assert r2["pattern"] == ABSORB and abs(r2["accumulate"] - 1.875) < 0.01

    # 场景3 中性：占比不够
    r3 = flow_pattern(5e8, -2e8, 800e8, 0.6, None, cfg, mid_session)
    assert r3["pattern"] is None

    # 场景4 迟滞：派发态下回落到进入/维持阈值之间 → 保持派发
    r4 = flow_pattern(-16e8, 13e8, 1000e8, -1.2, DISTRIBUTE, cfg, mid_session)
    assert r4["pattern"] == DISTRIBUTE, "迟滞带内应保持原象限"
    # 继续回落到维持阈值以下 → 退出为中性
    r5 = flow_pattern(-8e8, 6e8, 1000e8, -0.8, DISTRIBUTE, cfg, mid_session)
    assert r5["pattern"] is None, "跌破维持阈值应退出"

    # 场景5 直接翻转：吸筹 → 派发（符号整体反转，不需经过中性）
    r6 = flow_pattern(-30e8, 20e8, 800e8, -3.75, ABSORB, cfg, mid_session)
    assert r6["pattern"] == DISTRIBUTE

    # 场景6 早盘预热
    r7 = flow_pattern(-30e8, 20e8, 100e8, -30.0, None, cfg, early_session)
    assert r7["pattern"] == EARLY

    # 场景7 数据缺失
    r8 = flow_pattern(None, None, None, None, None, cfg, mid_session)
    assert r8["pattern"] is None

    print("\n自校验通过：象限/强度/迟滞/预热/翻转/缺省均符合预期")
