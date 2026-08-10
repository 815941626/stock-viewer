# -*- coding: utf-8 -*-
"""
fetcher.py - 东方财富板块资金流数据抓取（第一步）

功能：按 config.SECTORS 配置的 12 个板块，从东财拉取
      涨跌幅、成交额、主力净流入、主力净占比，并打印验证。

接口说明（东财公开 JSON 接口，无需登录）：
  采用 ulist.np/get「批量行情」接口，用 secids 一次请求取全部 12 个板块，
  避免翻页拉全量板块列表，把每次轮询压缩成 1 个请求，降低被限流概率。
  板块 secid 形如 90.BK1036（行业/概念板块的市场号均为 90）。

字段映射：
    f12  板块代码          f14  板块名称          f2   板块指数点位
    f3   涨跌幅(%)         f6   成交额(元)        f62  主力净流入(元)
    f184 主力净占比(%)     f66  超大单净流入(元)  f72  大单净流入(元)
    其中：主力净流入 = 超大单净流入 + 大单净流入

用法：
    python fetcher.py     # 拉取并打印 12 个监控板块
"""

import sys
import time

import requests

from config import SECTORS, USE_PROXY

# 主接口（实时）失效时自动降级到备用镜像（push2delay 延迟行情）
API_HOSTS = [
    "https://push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
ULIST_PATH = "/api/qt/ulist.np/get"

# 板块指数分时接口（当日 1 分钟粒度：现价/均价），详情面板按需拉取。
# 实测（2026-08-08）：push2his 会对频繁请求的 IP 断连，push2delay 镜像稳定可用，
# 因此同 API_HOSTS 一样做 主 -> 镜像 自动降级
TREND_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
TREND_PATH = "/api/qt/stock/trends2/get"

# 板块分钟级资金流K线（累计主力净流入），动量冷启动回填用。
# 实测（2026-08-08）：push2his/push2 会断连，push2delay 稳定；行业/概念板块均有数据
FFLOW_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
FFLOW_PATH = "/api/qt/stock/fflow/kline/get"

# F10 核心题材：个股所属概念板块反推（自下而上动量池用）。
# IS_PRECISE=1 为主题概念，=0 为指数/风格成分（沪深300、融资融券等），天然过滤器。
# BOARD_CODE 为数字，转 BK 代码 = "BK" + 4位补零（实测 2026-08-08）
F10_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
F10_REPORT = "RPT_F10_CORETHEME_BOARDTYPE"

# 请求头：模拟浏览器，降低被反爬拦截的概率
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}


def _build_session():
    """构造全局复用的 Session（连接池）。
    USE_PROXY=False 时关闭 trust_env，忽略系统/环境变量代理直连东财，
    避免开 VPN 时把国内请求也转发出去导致断连。"""
    session = requests.Session()
    session.trust_env = USE_PROXY
    return session


_SESSION = _build_session()


def _to_float(value):
    """接口对无数据的字段会返回 '-'，统一转 float，无效值返回 None"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(item):
    """把接口原始字段转成带中文语义的 dict"""
    return {
        "code": item.get("f12"),                        # 板块代码，如 BK1036
        "name": item.get("f14"),                        # 东财板块名
        "change_pct": _to_float(item.get("f3")),        # 涨跌幅 %
        "amount": _to_float(item.get("f6")),            # 成交额（元）
        "main_net_inflow": _to_float(item.get("f62")),  # 主力净流入（元）
        "main_net_pct": _to_float(item.get("f184")),    # 主力净占比 %
        "small_net_inflow": _to_float(item.get("f84")),  # 小单净流入（散户，拥挤度V2）
        "up_count": _to_float(item.get("f104")),         # 上涨家数（铺开度）
        "down_count": _to_float(item.get("f105")),       # 下跌家数
        "chg_60d": _to_float(item.get("f24")),           # 60日涨幅（抬高度）
        "chg_ytd": _to_float(item.get("f25")),           # 年初至今涨幅（抬高度）
        "_super_big": _to_float(item.get("f66")),       # 超大单净流入（校验用）
        "_big": _to_float(item.get("f72")),             # 大单净流入（校验用）
    }


def fetch_sector_flow(sectors=None, retries=2):
    """
    拉取板块列表的实时资金流数据（默认 config.SECTORS，也可传动量池板块）。
    一次请求取全部板块，返回顺序与传入列表一致。

    容错策略：
      - 依次尝试 API_HOSTS 里的主机（主接口 -> 备用镜像）
      - 每个主机失败重试 retries 次，仍失败则切换下一个主机
    """
    sectors = sectors if sectors is not None else SECTORS
    secids = ",".join("90." + s["code"] for s in sectors)
    params = {
        "fltt": 2,                                  # 数值字段直接返回浮点数
        "invt": 2,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",   # 东财公开接口通用 token
        # 拥挤度 V2 数据源并入同一请求（f84小单/f104-105涨跌家数/f24-25区间涨幅）
        "fields": "f12,f14,f2,f3,f6,f24,f25,f62,f66,f72,f84,f104,f105,f184",
        "secids": secids,
    }

    last_err = None
    for host in API_HOSTS:
        for attempt in range(1, retries + 1):
            try:
                resp = _SESSION.get(host + ULIST_PATH, params=params,
                                    headers=HEADERS, timeout=10)
                resp.raise_for_status()
                diff = (resp.json().get("data") or {}).get("diff") or []
                if not diff:
                    raise RuntimeError("接口返回空数据")
                # 按 code 建索引，再按传入列表顺序回填
                by_code = {it.get("f12"): _normalize(it) for it in diff}
                result = []
                for cfg in sectors:
                    item = by_code.get(cfg["code"])
                    if item is None:
                        # 个别板块缺失时保留占位，保证展示位置稳定
                        result.append({"code": cfg["code"],
                                       "name": cfg.get("em_name") or cfg["display"],
                                       "change_pct": None, "amount": None,
                                       "main_net_inflow": None, "main_net_pct": None,
                                       "small_net_inflow": None, "up_count": None,
                                       "down_count": None, "chg_60d": None,
                                       "chg_ytd": None,
                                       "_super_big": None, "_big": None,
                                       "display": cfg["display"]})
                    else:
                        item["display"] = cfg["display"]
                        result.append(item)
                return result
            except Exception as err:  # 网络异常/限流/解析失败，重试或换主机
                last_err = err
                if attempt < retries:
                    time.sleep(1)
    raise RuntimeError(f"拉取东财板块数据失败（所有主机均不可用）: {last_err}")


def fetch_sector_trend(code, retries=2):
    """
    拉取板块指数当日分时（1 分钟粒度），返回：
      {"code", "pre_close", "points": [{"time": "HH:MM", "price", "avg"}, ...]}

    trends2 每行格式 "YYYY-MM-DD HH:MM,现价,均价,..."，取第 2/3 列。
    （已实测：开盘首分钟两列相等、随后分化，符合现价/均线的定义。）
    """
    params = {
        "secid": "90." + code,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53",           # 时间 / 现价 / 均价
        "iscr": 0,
        "ndays": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    last_err = None
    for host in TREND_HOSTS:
        for attempt in range(1, retries + 1):
            try:
                resp = _SESSION.get(host + TREND_PATH, params=params,
                                    headers=HEADERS, timeout=10)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                trends = data.get("trends") or []
                pre_close = _to_float(data.get("preClose") or data.get("prePrice"))
                if not trends or pre_close is None:
                    raise RuntimeError("trends2 返回空数据")
                points = []
                for line in trends:
                    parts = line.split(",")
                    if len(parts) < 3:
                        continue
                    price = _to_float(parts[1])
                    avg = _to_float(parts[2])
                    if price is None:
                        continue
                    points.append({
                        "time": parts[0].split(" ")[-1],  # 只留 HH:MM
                        "price": price,
                        "avg": avg,
                    })
                if not points:
                    raise RuntimeError("trends 解析后无有效分时点")
                return {"code": code, "pre_close": pre_close, "points": points}
            except Exception as err:
                last_err = err
                if attempt < retries:
                    time.sleep(1)
    raise RuntimeError(f"拉取板块指数分时失败（{code}，所有主机均不可用）: {last_err}")


def fetch_sector_flow_history(code, retries=2):
    """
    拉取板块当日分钟级累计主力净流入（动量冷启动回填用）。

    klines 每行（klt=1）："YYYY-MM-DD HH:MM,主力净流入,小单,中单,大单,超大单"
    （已实测自洽：大单+超大单 == 主力净流入；且与 ulist f62 快照同为日内累计口径，
    故回填的分钟点与实时 5 秒采样点可直接拼接进同一历史缓冲。）

    返回 [{"ts": unix 秒, "inflow": 元}, ...]，按时间升序。
    """
    params = {
        "secid": "90." + code,
        "klt": 1,
        "lmt": 0,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    last_err = None
    for host in FFLOW_HOSTS:
        for attempt in range(1, retries + 1):
            try:
                resp = _SESSION.get(host + FFLOW_PATH, params=params,
                                    headers=HEADERS, timeout=10)
                resp.raise_for_status()
                klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
                if not klines:
                    raise RuntimeError("fflow kline 返回空数据")
                points = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 2:
                        continue
                    inflow = _to_float(parts[1])
                    if inflow is None:
                        continue
                    try:
                        ts = time.mktime(time.strptime(parts[0], "%Y-%m-%d %H:%M"))
                    except ValueError:
                        continue
                    points.append({"ts": ts, "inflow": inflow})
                if not points:
                    raise RuntimeError("klines 解析后无有效分钟点")
                return points
            except Exception as err:
                last_err = err
                if attempt < retries:
                    time.sleep(1)
    raise RuntimeError(f"拉取分钟级资金流失败（{code}，所有主机均不可用）: {last_err}")


def _secucode(code):
    """A股代码 -> 东财 SECUCODE（6xx 沪，0xx/3xx 深；名单内无北交所票）"""
    return code + (".SH" if code.startswith("6") else ".SZ")


def _market_prefix(code):
    """A股代码 -> ulist secid 市场前缀（6xx=1.沪，0xx/3xx=0.深）"""
    return "1." if code.startswith("6") else "0."


def fetch_stock_quotes(codes, retries=2):
    """
    批量拉个股实时行情（详情页成分龙头股用），一次请求取全部。
    codes：6 位代码列表。返回按传入顺序的列表，字段与板块同源（元）：
      code/name/price/change_pct/amount/main_net_inflow/main_net_pct/
      turnover_rate/total_mcap
    """
    secids = ",".join(_market_prefix(c) + c for c in codes)
    params = {
        "fltt": 2,
        "invt": 2,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fields": "f2,f3,f6,f8,f12,f14,f20,f62,f184",
        "secids": secids,
    }
    last_err = None
    for host in API_HOSTS:
        for attempt in range(1, retries + 1):
            try:
                resp = _SESSION.get(host + ULIST_PATH, params=params,
                                    headers=HEADERS, timeout=10)
                resp.raise_for_status()
                diff = (resp.json().get("data") or {}).get("diff") or []
                if not diff:
                    raise RuntimeError("接口返回空数据")
                by_code = {it.get("f12"): it for it in diff}
                result = []
                for c in codes:
                    it = by_code.get(c)
                    if it is None:
                        result.append({"code": c, "name": None, "price": None,
                                       "change_pct": None, "amount": None,
                                       "main_net_inflow": None, "main_net_pct": None,
                                       "turnover_rate": None, "total_mcap": None})
                    else:
                        result.append({
                            "code": c,
                            "name": it.get("f14"),
                            "price": _to_float(it.get("f2")),
                            "change_pct": _to_float(it.get("f3")),
                            "amount": _to_float(it.get("f6")),
                            "main_net_inflow": _to_float(it.get("f62")),
                            "main_net_pct": _to_float(it.get("f184")),
                            "turnover_rate": _to_float(it.get("f8")),
                            "total_mcap": _to_float(it.get("f20")),
                        })
                return result
            except Exception as err:
                last_err = err
                if attempt < retries:
                    time.sleep(1)
    raise RuntimeError(f"拉取个股行情失败（所有主机均不可用）: {last_err}")


def fetch_stock_boards(code, retries=2):
    """
    F10 核心题材反推：个股所属的主题概念板块（只留 IS_PRECISE=1，
    沪深300/融资融券等指数风格成分天然滤掉）。
    返回 [{"code": "BK0xxx", "name": 板块名, "rank": BOARD_RANK}]，
    rank 越小该概念对个股越有代表性（池子排序的次级依据）。
    """
    params = {
        "reportName": F10_REPORT,
        "columns": "ALL",
        "filter": f'(SECUCODE="{_secucode(code)}")',
        "pageNumber": 1,
        "pageSize": 100,
    }
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = _SESSION.get(F10_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            rows = ((resp.json() or {}).get("result") or {}).get("data") or []
            boards = []
            for row in rows:
                if str(row.get("IS_PRECISE")) != "1":
                    continue
                try:
                    bk = f"BK{int(row.get('BOARD_CODE')):04d}"
                except (TypeError, ValueError):
                    continue
                boards.append({
                    "code": bk,
                    "name": row.get("BOARD_NAME"),
                    "rank": row.get("BOARD_RANK") or 99,
                })
            return boards
        except Exception as err:
            last_err = err
            if attempt < retries:
                time.sleep(1)
    raise RuntimeError(f"F10 反推概念板块失败（{code}）: {last_err}")


def _fund_secid(code):
    """ETF 代码 -> ulist secid（5xx 沪市 1.，1xx 深市 0.）"""
    return ("1." if code.startswith("5") else "0.") + code


def fetch_etf_quotes(codes, retries=2):
    """
    ETF 实时行情：价格 + 总市值。份额 = f20 ÷ f2（已实测 2026-08-11）。
    返回与传入顺序对齐的列表：[{code, name, price, mcap, shares}]，
    缺失项占位（shares=None）。
    """
    params = {
        "fltt": 2,
        "invt": 2,
        "np": 1,
        "fields": "f2,f3,f12,f14,f20",
        "secids": ",".join(_fund_secid(c) for c in codes),
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    last_err = None
    for host in API_HOSTS:
        for attempt in range(1, retries + 1):
            try:
                resp = _SESSION.get(host + ULIST_PATH, params=params,
                                    headers=HEADERS, timeout=10)
                resp.raise_for_status()
                diff = (resp.json().get("data") or {}).get("diff") or []
                if not diff:
                    raise RuntimeError("ETF 行情返回空数据")
                by_code = {it.get("f12"): it for it in diff}
                result = []
                for c in codes:
                    it = by_code.get(c)
                    price = _to_float(it.get("f2")) if it else None
                    mcap = _to_float(it.get("f20")) if it else None
                    result.append({
                        "code": c,
                        "name": it.get("f14") if it else None,
                        "price": price,
                        "mcap": mcap,
                        "shares": (mcap / price) if (price and mcap) else None,
                    })
                return result
            except Exception as err:
                last_err = err
                if attempt < retries:
                    time.sleep(1)
    raise RuntimeError(f"拉取 ETF 行情失败（所有主机均不可用）: {last_err}")


def _fmt_yi(value):
    """「元」转「亿元」显示，None 显示 -"""
    return "-" if value is None else f"{value / 1e8:.2f}"


def main():
    print("正在从东方财富拉取 12 个板块资金流数据...\n")
    sectors = fetch_sector_flow()

    # 打印表格验证（金额单位：亿元）
    print(f"{'序':<3} {'显示名':<6} {'东财板块名':<8} {'代码':<8} "
          f"{'涨跌幅':>8} {'成交额(亿)':>11} {'主力净流入(亿)':>13} {'净占比':>8}")
    print("-" * 88)
    for i, s in enumerate(sectors, 1):
        pct = f"{s['change_pct']:+.2f}%" if s["change_pct"] is not None else "-"
        mpct = f"{s['main_net_pct']:+.2f}%" if s["main_net_pct"] is not None else "-"
        print(f"{i:<4} {s['display']:<8} {s['name']:<10} {s['code']:<8} "
              f"{pct:>9} {_fmt_yi(s['amount']):>13} {_fmt_yi(s['main_net_inflow']):>16} {mpct:>9}")

    # 数据自洽校验：主力净流入 应约等于 超大单 + 大单
    top = sectors[0]
    if all(v is not None for v in (top["_super_big"], top["_big"], top["main_net_inflow"])):
        calc = (top["_super_big"] + top["_big"]) / 1e8
        real = top["main_net_inflow"] / 1e8
        print(f"\n校验：[{top['display']}] 超大单+大单 = {calc:.2f} 亿，"
              f"接口主力净流入 = {real:.2f} 亿（两者应基本一致）")


if __name__ == "__main__":
    main()
