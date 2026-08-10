# -*- coding: utf-8 -*-
"""
snapshot.py - 全量快照落盘（回测数据积累）

设计（2026-08-10）：
  - 盘中每次成功轮询写一行：{"ts": epoch, "s": [[板块字段数组], ...]}
  - 按天 gzip 滚动：backend/snapshots/snapshots_YYYYMMDD.jsonl.gz
  - 每个文件首行是 schema 行 {"schema":1,"fields":[...]}，自描述
  - 每分钟 flush 一次（崩溃最多丢一分钟）
  - 盘外闸由 main.py 的 in_trading_session 控制，本模块不管

字段数组顺序（每个板块一行，与 FIELDS 对应）：
  行情:   code, chg, amt, mni, mnp, sni, up, dn, c60, cytd
  动量:   v5, v15, score, accel, dead
  拥挤度: cg_pos, cg_small, cg_breadth, cg_ext
  筹码:   flow, absorb, s_slope

单位：amt/mni/sni 为元；chg/mnp/c60/cytd/cg_* 为 %；v5/v15 为亿元/分钟；
     absorb 为比值（0-1+）；s_slope 为百分点/分钟。None 原样落盘。
"""
import gzip
import json
import os
import time

SCHEMA = 1

FIELDS = [
    "code", "chg", "amt", "mni", "mnp", "sni", "up", "dn", "c60", "cytd",
    "v5", "v15", "score", "accel", "dead",
    "cg_pos", "cg_small", "cg_breadth", "cg_ext",
    "flow", "absorb", "s_slope",
]


class SnapshotWriter:
    """gzip 按天滚动的 JSONL 写入器（append 模式，跨重启续写同一天的文件）。"""

    def __init__(self, directory, flush_every=12):
        self.dir = directory
        self.flush_every = flush_every  # 12 行 ≈ 1 分钟
        self._fh = None
        self._day = None
        self._writes = 0
        self.today_count = 0  # 今日已写行数（health 展示用）

    def _ensure(self, day):
        if day == self._day and self._fh:
            return
        if self._fh:
            self._fh.close()
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"snapshots_{day}.jsonl.gz")
        is_new = not os.path.exists(path)
        self._fh = gzip.open(path, "ab", compresslevel=6)
        self._day = day
        if is_new:
            head = json.dumps({"schema": SCHEMA, "fields": FIELDS}, ensure_ascii=False)
            self._fh.write((head + "\n").encode("utf-8"))

    def write(self, record):
        day = time.strftime("%Y%m%d", time.localtime(record["ts"]))
        self._ensure(day)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self._fh.write((line + "\n").encode("utf-8"))
        self._writes += 1
        if self._writes % self.flush_every == 0:
            self._fh.flush()
        if day == time.strftime("%Y%m%d"):
            self.today_count += 1

    def close(self):
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None


def build_record(sectors, mom, congestions, flow_patterns, small_slopes, ts):
    """把一次轮询的全部指标拼成一行快照记录。"""
    rows = []
    for s in sectors:
        code = s["code"]
        m = mom.get(code) or {}
        c = congestions.get(code) or {}
        parts = c.get("parts") or {}
        f = flow_patterns.get(code) or {}
        rows.append([
            code, s.get("change_pct"), s.get("amount"),
            s.get("main_net_inflow"), s.get("main_net_pct"),
            s.get("small_net_inflow"), s.get("up_count"), s.get("down_count"),
            s.get("chg_60d"), s.get("chg_ytd"),
            m.get("v5"), m.get("v15"), m.get("score"), m.get("accel"), m.get("dead"),
            c.get("position"), parts.get("small"), parts.get("breadth"),
            parts.get("extension"),
            f.get("pattern"), f.get("absorption"), small_slopes.get(code),
        ])
    return {"ts": ts, "s": rows}


# ----------------------------------------------------------------------
# 自校验：python snapshot.py（临时目录写读回环）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        w = SnapshotWriter(d, flush_every=1)
        ts = time.time()
        rec = {"ts": ts, "s": [[
            "BK0917", 1.23, 1e10, -1e9, -2.5, 6e8, 120, 60, -3.2, 40.1,
            0.11, -0.25, 1.68, True, False,
            51, 30, 77, 47, "派发", 0.63, None,
        ]]}
        w.write(rec)
        w.write(rec)
        w.close()

        day = time.strftime("%Y%m%d", time.localtime(ts))
        path = os.path.join(d, f"snapshots_{day}.jsonl.gz")
        with gzip.open(path, "rt", encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        head = json.loads(lines[0])
        body = json.loads(lines[1])
        assert head["schema"] == SCHEMA and head["fields"] == FIELDS
        assert len(lines) == 3, "schema 行 + 2 条记录"
        assert body["s"][0][0] == "BK0917"
        assert len(body["s"][0]) == len(FIELDS)
        assert body["s"][0][FIELDS.index("flow")] == "派发"
        # append 模式：重开同一天的文件应续写而非覆盖
        w2 = SnapshotWriter(d, flush_every=1)
        w2.write(rec)
        w2.close()
        with gzip.open(path, "rt", encoding="utf-8") as f:
            assert len(f.read().strip().split("\n")) == 4, "续写不应重复 schema 行"
        print("自校验通过：schema/记录/gzip 回环/同日续写均正常")
