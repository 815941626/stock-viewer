# -*- coding: utf-8 -*-
"""
read_snapshots.py - 快照档案读取工具

用法：
  python backend/read_snapshots.py                     列出所有日期 + 行数
  python backend/read_snapshots.py 20260811            倾倒该日全部记录（JSONL）
  python backend/read_snapshots.py 20260811 --tail 3   只看最后 3 行
  python backend/read_snapshots.py 20260811 --dict 1   第 1 条记录展开成字段名对照

记录结构：首行 schema {"schema":1,"fields":[...]}，
之后每行 {"ts": epoch, "s": [[板块字段数组], ...]}，字段顺序见 schema.fields。
"""
import gzip
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import SNAPSHOT_DIR  # noqa: E402


def list_days():
    if not os.path.isdir(SNAPSHOT_DIR):
        print(f"尚无快照目录：{SNAPSHOT_DIR}")
        return
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.startswith("snapshots_") or not name.endswith(".jsonl.gz"):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        with gzip.open(path, "rt", encoding="utf-8") as f:
            n = sum(1 for _ in f) - 1  # 减去 schema 行
        size_mb = os.path.getsize(path) / 1e6
        print(f"  {name[len('snapshots_'):-len('.jsonl.gz')]}: {n} 行, {size_mb:.2f} MB (gz)")


def read_lines(day):
    path = os.path.join(SNAPSHOT_DIR, f"snapshots_{day}.jsonl.gz")
    if not os.path.exists(path):
        print(f"没有 {day} 的快照文件")
        sys.exit(1)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines = f.read().strip().split("\n")
    return json.loads(lines[0]), [json.loads(l) for l in lines[1:]]


def main():
    args = sys.argv[1:]
    if not args:
        list_days()
        return

    day = args[0]
    schema, records = read_lines(day)

    if "--dict" in args:
        idx = int(args[args.index("--dict") + 1])
        rec = records[idx]
        fields = schema["fields"]
        print(f"ts={rec['ts']} 板块数={len(rec['s'])}")
        for row in rec["s"][:5]:
            print("  " + json.dumps(dict(zip(fields, row)), ensure_ascii=False))
        return

    if "--tail" in args:
        n = int(args[args.index("--tail") + 1])
        for rec in records[-n:]:
            print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        return

    for rec in records:
        print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
