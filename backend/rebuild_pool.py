# -*- coding: utf-8 -*-
"""
rebuild_pool.py - 重建自下而上动量池（修改 LEADER_STOCKS 后手动运行）

用法：python backend/rebuild_pool.py
流程：龙头股名单 → F10 核心题材反推（IS_PRECISE=1）→ 概念频次聚合
      → 按 config.POOL 过滤（共振频带 + 手动增删）→ 写 backend/pool.json
main.py 启动时读 pool.json；文件缺失/损坏时自动回退固定 SECTORS。

为什么离线重建而非启动时算：55 只 × F10 请求 约 1 分钟且名单按季度维护，
没必要每次启动都打一遍东财。
"""
import io
import json
import sys
import time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import LEADER_STOCKS, POOL, POOL_CACHE_FILE  # noqa: E402
from fetcher import fetch_stock_boards  # noqa: E402


def main():
    codes = sorted({c for lst in LEADER_STOCKS.values() for c in lst})
    print(f"龙头股去重 {len(codes)} 只，开始 F10 反推（间隔 0.8 秒防限流）...")

    boards_of = {}
    for i, code in enumerate(codes, 1):
        for attempt in range(1, 4):
            try:
                boards_of[code] = fetch_stock_boards(code)
                print(f"[{i:>2}/{len(codes)}] {code}: {len(boards_of[code])} 个概念")
                break
            except Exception as err:
                print(f"[{i:>2}/{len(codes)}] {code} 第{attempt}次失败: {err}")
                time.sleep(2)
        else:
            boards_of[code] = []  # 3 次都失败：该票缺席，不影响整体
        time.sleep(0.8)

    failed = [c for c in codes if not boards_of.get(c)]
    if failed:
        print(f"!! 以下股票未取到概念（缺席）: {failed}")

    # 聚合：概念 -> {名称, 成员(带 BOARD_RANK), rank 和}
    # members 保留 rank：main 加载时按 POOL.member_max_rank 过滤边缘成员
    # （BYD/国电南瑞挂在光伏概念 rank 35-37 的教训：只看"有没有"会混入边缘业务）
    agg = {}
    for code, boards in boards_of.items():
        for b in boards:
            e = agg.setdefault(b["code"], {"name": b["name"], "members": [], "rank_sum": 0})
            e["members"].append({"code": code, "rank": b["rank"]})
            e["rank_sum"] += b["rank"]

    exclude = set(POOL["exclude"])
    extra = set(POOL["extra_include"])

    candidates, buckets = [], []
    for code, e in agg.items():
        freq = len(e["members"])
        item = {"code": code, "name": e["name"], "freq": freq,
                "rank_sum": e["rank_sum"],
                "members": sorted(e["members"], key=lambda m: m["rank"])}
        if code in exclude:
            continue
        if code in extra or POOL["min_freq"] <= freq <= POOL["max_freq"]:
            candidates.append(item)
        elif freq > POOL["max_freq"]:
            buckets.append(item)  # 大箩筐单独记录，供审查

    # 频次降序（共振强的优先），同频按 BOARD_RANK 和升序（代表性强的优先）
    candidates.sort(key=lambda x: (-x["freq"], x["rank_sum"]))
    selected = candidates[:POOL["max_sectors"]]
    dropped = candidates[POOL["max_sectors"]:]

    pool = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "leader_count": len(codes),
        "params": {k: POOL[k] for k in ("min_freq", "max_freq", "max_sectors", "top_n")},
        "sectors": [{"code": s["code"], "name": s["name"],
                     "freq": s["freq"], "members": s["members"]} for s in selected],
        "excluded_buckets": [{"code": s["code"], "name": s["name"], "freq": s["freq"]}
                             for s in sorted(buckets, key=lambda x: -x["freq"])],
        "dropped_by_cap": [{"code": s["code"], "name": s["name"], "freq": s["freq"]}
                           for s in dropped],
        "single_stock_board_count": sum(1 for e in agg.values() if len(e["members"]) == 1),
    }
    with open(POOL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)

    print(f"\n== 池子已写入 {POOL_CACHE_FILE}：入选 {len(selected)} 板块 ==")
    for s in selected:
        shown = "/".join(m["code"] for m in s["members"][:5]) + \
                ("..." if len(s["members"]) > 5 else "")
        print(f"  {s['code']} {s['name']:<14} freq={s['freq']}  {shown}")
    bucket_str = ", ".join(f"{b['name']}({b['freq']})" for b in pool["excluded_buckets"])
    print(f"\n被滤的大箩筐(freq>{POOL['max_freq']}): {bucket_str or '(无)'}")
    print(f"超上限截断: {len(dropped)} 个; 单票板块(未入池): {pool['single_stock_board_count']} 个")
    if dropped:
        print("截断名单: " + ", ".join(f"{d['name']}({d['freq']})" for d in dropped))


if __name__ == "__main__":
    main()
