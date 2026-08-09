# -*- coding: utf-8 -*-
"""
congestion.py - 拥挤度指标模块（V2.0：拥挤位置代理）

设计共识（2026-08-09 讨论，详见 DESIGN.md）：
  - 拥挤 = 散户涌入 + 价格铺开抬高；动量定"现在多热"，拥挤位置定"烧到第几段"
  - 位置做徽标不参与排序（先观察两三周，有区分度再谈降权）
  - 三分量（对应用户定义）：
      small     小单净占比位置——小单≈散户；吸筹期小单净流出（负），
                散户追高转正，由负转正是"10%→80%"的核心前兆
      breadth   铺开度——上涨家数占比；启动期龙头窄幅领涨，散户进场后全线铺开
      extension 抬高度——已涨掉的空间，max(60日涨幅, 年初至今涨幅)
  - 成交额过热分量暂缺（板块日K源在本IP不可用），接口可用后在 weights 补位
  - 所有映射锚点是初值猜测，靠观察日志（logs/congestion_*.jsonl）校准

数据质量 caveat：东财大单/小单按委托单大小启发式划分，主力拆单会污染，
故本模块输出是"温度计"不是"判决书"。
"""


def interp(x, anchors):
    """分段线性插值。anchors=[(x0,y0),(x1,y1),...] 按 x 升序；两端截断。"""
    if x is None:
        return None
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return anchors[-1][1]


def small_position(small_net_pct, anchors):
    """小单净占比（占成交额%）→ 拥挤位置。负=散户在卖（吸筹），正=散户追高。"""
    return interp(small_net_pct, anchors)


def breadth_position(up_count, down_count, anchors):
    """上涨家数占比 → 铺开度位置。None 家数无效时返回 None。"""
    if up_count is None or down_count is None:
        return None
    total = up_count + down_count
    if total <= 0:
        return None
    return interp(up_count / total, anchors)


def extension_position(chg_60d, chg_ytd, full_pct):
    """抬高度位置：涨幅基准取 max(60日, 年初至今)，达到 full_pct 记满。
    负涨幅截断为 0（跌出来的空间不算拥挤）。"""
    cands = [v for v in (chg_60d, chg_ytd) if v is not None]
    if not cands:
        return None
    base = max(max(cands), 0.0)
    return min(base / full_pct * 100.0, 100.0)


def compute_congestion(small_net_pct, up_count, down_count,
                       chg_60d, chg_ytd, cfg):
    """
    汇总三分量为拥挤位置（0-100）。输入均来自实时快照（含 None）。
    返回 {position, parts:{small,breadth,extension},
          small_net_pct, breadth_pct, extension_base}
    position 对可得权重归一；三分量全缺时为 None。
    """
    parts = {
        "small": small_position(small_net_pct, cfg["small_anchors"]),
        "breadth": breadth_position(up_count, down_count, cfg["breadth_anchors"]),
        "extension": extension_position(chg_60d, chg_ytd, cfg["extension_full_pct"]),
    }
    weights = cfg["weights"]
    wsum = sum(weights[k] for k, v in parts.items() if v is not None)
    if wsum <= 0:
        position = None
    else:
        position = sum(weights[k] * v for k, v in parts.items() if v is not None) / wsum

    total = None
    if up_count is not None and down_count is not None and up_count + down_count > 0:
        total = up_count + down_count
    cands = [v for v in (chg_60d, chg_ytd) if v is not None]
    return {
        "position": None if position is None else round(position),
        "parts": {k: (None if v is None else round(v)) for k, v in parts.items()},
        "small_net_pct": small_net_pct,
        "breadth_pct": None if total is None else round(up_count / total * 100, 1),
        "extension_base": max(cands) if cands else None,
    }


# ----------------------------------------------------------------------
# 自校验：python congestion.py（合成场景验证分量方向与合成序）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    cfg = {
        "weights": {"small": 0.45, "breadth": 0.30, "extension": 0.25},
        "small_anchors": [(-1.0, 10.0), (0.0, 40.0), (1.0, 75.0), (2.0, 100.0)],
        "breadth_anchors": [(0.5, 20.0), (0.7, 50.0), (0.85, 80.0), (0.95, 100.0)],
        "extension_full_pct": 60.0,
    }

    # 场景1 吸筹期：小单净流出、宽度分化、涨幅不大 → 位置应低
    early = compute_congestion(-1.2, 90, 60, 3.0, 8.0, cfg)
    # 场景2 散户涌入：小单转正、全线铺开、涨幅已大 → 位置应高
    late = compute_congestion(1.5, 170, 12, -2.0, 45.0, cfg)
    # 场景3 数据全缺 → None
    empty = compute_congestion(None, None, None, None, None, cfg)

    for name, r in (("吸筹期", early), ("散户涌入", late), ("全缺", empty)):
        print(f"{name}: position={r['position']} parts={r['parts']} "
              f"breadth_pct={r['breadth_pct']} ext_base={r['extension_base']}")

    assert early["position"] is not None and early["position"] < 40, "吸筹期位置应低"
    assert late["position"] is not None and late["position"] > 70, "散户涌入位置应高"
    assert late["position"] > early["position"], "拥挤序应正确"
    assert empty["position"] is None, "全缺应为 None"
    assert interp(-5, cfg["small_anchors"]) == 10.0, "左端截断"
    assert interp(99, cfg["small_anchors"]) == 100.0, "右端截断"
    print("\n自校验通过：分量方向/合成序/截断/缺省均符合预期")
