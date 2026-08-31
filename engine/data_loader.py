"""
缠论模拟交易 App — 数据层
从 data/klines/*.csv (K线) + data/step25/*.csv (缠论信号) 加载离线数据
两份文件按 idx 对齐，合并为统一的 bar 结构
"""
import csv
import json
import math
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# 2026-08-12 扁平化: 文件名 {code}_{period}_{type}.csv
def _path(period, code, suffix):
    """返回扁平化文件路径: data/{code}_{period}_{suffix}.csv"""
    return DATA_DIR / f"{code}_{period}_{suffix}.csv"

# 信号列清单（step25 中表示"信号"的布尔列）
SIGNAL_COLS = [
    "is_top", "is_bottom", "is_XSG", "is_XXD",
    "is_SZBBC", "is_XDBBC", "is_SZ5BBC", "is_XD5BBC", "is_SZ7BBC", "is_XD7BBC",
    "is_SZZSPZBC", "is_XDZSPZBC", "is_SZQSBC", "is_XDQSBC",
]

# Signal name mapping (shown in chart legend)
SIGNAL_NAMES = {
    "is_top": "Top Pattern", "is_bottom": "Bottom Pattern",
    "is_XSG": "Up New High", "is_XXD": "Down New Low",
    "is_SZBBC": "Up Divergence", "is_XDBBC": "Down Divergence",
    "is_SZ5BBC": "Up 5-Bar Divergence", "is_XD5BBC": "Down 5-Bar Divergence",
    "is_SZ7BBC": "Up 7-Bar Divergence", "is_XD7BBC": "Down 7-Bar Divergence",
    "is_SZZSPZBC": "Up Pivot Divergence", "is_XDZSPZBC": "Down Pivot Divergence",
    "is_SZQSBC": "Up Trend Divergence", "is_XDQSBC": "Down Trend Divergence",
}


def _norm_dt(dt_str):
    """标准化 datetime 字符串：去掉 :00 秒尾和 .000000 微秒后缀"""
    s = dt_str.strip()
    if s.endswith(".000000"):
        s = s[:-7]
    if s.endswith(":00") and len(s) > 16:
        s = s[:-3]
    return s


def _bool(v):
    """安全转换布尔值"""
    s = str(v).strip().lower()
    return s in ("true", "1", "yes")


def _dir(base: Path, period: str) -> Path:
    """周期目录: period 为空 → base 根目录; 否则 base/period/"""
    if not period:
        return base
    return base / period


def _safe_float(v, default=0.0):
    """安全转换 float: NaN/Inf/空值 → default"""
    if v is None or v == "":
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _sanitize_ohlc(open_, high, low, close):
    """清洗 OHLC 数据: 确保 open/close 在 [low, high] 范围内
    返回 None 表示该行数据无效应跳过
    处理: NaN/Inf → 跳过, 负数/零值 → 跳过, high<low → 交换, open/close 越界 → clamp
    """
    # 检查 NaN/Inf
    for v in (open_, high, low, close):
        if math.isnan(v) or math.isinf(v):
            return None
    # 检查负数/零值 (价格必须为正)
    if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
        return None
    # 确保 high >= low
    if high < low:
        high, low = low, high
    # clamp open/close 到 [low, high]
    open_ = max(low, min(high, open_))
    close = max(low, min(high, close))
    return open_, high, low, close


def detect_period(code: str, bars=None) -> str:
    """从 K 线时间戳粒度检测周期: 30s/3m/5min/30min/day/week
    返回 None 表示无法识别"""
    if bars is None:
        bars = load_kline(code)
    if len(bars) < 2:
        return None
    t0 = bars[0]["datetime"]
    t1 = bars[1]["datetime"]
    if " " not in t0:
        return "day"          # 2025-05-12 无时间 → 日线
    # 时间差（秒）
    try:
        # _norm_dt 会去掉 :00 秒尾，但 :30 等保留，所以每个时间分别尝试两种格式
        def _parse_dt(s):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None
        d0 = _parse_dt(t0)
        d1 = _parse_dt(t1)
        if d0 is None or d1 is None:
            return None
        secs = (d1 - d0).total_seconds()
    except Exception:
        return None
    if secs >= 24 * 3600 * 5:
        return "week"
    if secs >= 24 * 3600:
        return "day"
    if secs >= 20 * 60:
        return "30min"
    if secs >= 4 * 60:
        return "5min"
    if secs >= 2 * 60:
        return "3m"
    if secs >= 45:
        return "1min"
    if secs >= 15:
        return "30s"
    return "1min"


def load_symbols():
    """返回可用标的列表，每个 (code, period) 独立一条:
    [{code, name, bars, period}]
    扁平化文件 data/{code}_{period}_kline.csv → 解析 code+period"""
    result = []
    seen = set()
    # 扫描 data/*_kline.csv
    for f in sorted(DATA_DIR.glob("*_kline.csv")):
        stem = f.stem  # e.g. "300502_30min_kline" or "002475_day_kline"
        # 移除尾部 "_kline"
        base = stem[:-len("_kline")]
        # 找到最后一个 "_" 分隔 code 和 period
        idx = base.rfind("_")
        if idx < 0:
            continue
        code = base[:idx]
        period = base[idx+1:]
        # 2026-08-30 修复: 过滤自动落盘的临时标的 (kline_时间戳_周期), 避免污染标的列表
        if code.startswith("kline_"):
            continue
        if (code, period) in seen:
            continue
        seen.add((code, period))
        bars = load_kline(code, period)
        if bars:
            result.append({
                "code": code,
                "name": f"{code}({period})",
                "bars": len(bars),
                "period": period,
            })
    return result


def load_kline(code: str, period: str = ""):
    """加载 K 线: [{idx, datetime, open, high, low, close}]"""
    p = _path(period, code, "kline")
    if not p.exists():
        return []
    bars = []
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                result = _sanitize_ohlc(
                    float(row["open"]), float(row["high"]),
                    float(row["low"]), float(row["close"])
                )
                if result is None:
                    continue
                o, h, l, c = result
                bars.append({
                    "idx": int(row["idx"] if "idx" in row else row["id"]),
                    "datetime": _norm_dt(row["datetime"]),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                })
            except (ValueError, KeyError):
                continue
    return bars


def load_step25(code: str, period: str = ""):
    """加载缠论信号: list of dict, 按 idx 对齐 K 线
    注意: step25 的 idx 是原始 idx（与数据0_klines 对齐），K线可能经过包含处理（数据1）需按 datetime 映射"""
    p = _path(period, code, "step25")
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            signals = []
            for col in SIGNAL_COLS:
                if _bool(row.get(col)):
                    signals.append({"type": col, "name": SIGNAL_NAMES.get(col, col)})
            rows.append({
                "idx": int(row["idx"]),
                "datetime": _norm_dt(row["datetime"]),
                "signals": signals,
                "in_zhongshu": _bool(row.get("in_zhongshu")),
                "summacd": _safe_float(row.get("summacd")),
                "top_mark": str(row.get("top_mark", "")).strip(),
                "bottom_mark": str(row.get("bottom_mark", "")).strip(),
                "top_price": _safe_float(row.get("top_price")),
                "bottom_price": _safe_float(row.get("bottom_price")),
            })
    return rows


def load_segments(code: str, period: str = ""):
    """加载线段数据 (可选): data/{code}_{period}_segments.csv
    列: from_idx, from_price, to_idx, to_price, label
    返回 [{from_idx, from_price, to_idx, to_price, label}]"""
    p = _path(period, code, "segments")
    if not p.exists():
        return []
    segs = []
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                segs.append({
                    "from_idx": int(row["from_idx"]),
                    "from_price": _safe_float(row["from_price"]),
                    "to_idx": int(row["to_idx"]),
                    "to_price": _safe_float(row["to_price"]),
                    "label": row.get("label", "").strip(),
                })
            except (ValueError, KeyError):
                continue
    return segs


def load_zhongshu(code: str, period: str = ""):
    """加载中枢数据 (可选): data/{code}_{period}_zhongshu.csv
    列: x_left, x_right, y_bottom, y_top (+ zs_id, seg_type)
    返回 [{x_left, x_right, y_bottom, y_top, zs_id, seg_type}]"""
    p = _path(period, code, "zhongshu")
    if not p.exists():
        return []
    zs = []
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                zs.append({
                    "zs_id": int(row["zs_id"]) if row.get("zs_id") not in (None, "") else None,
                    "seg_type": row.get("seg_type", "").strip(),
                    "x_left": int(float(row["x_left"])),
                    "x_right": int(float(row["x_right"])),
                    "y_bottom": _safe_float(row["y_bottom"]),
                    "y_top": _safe_float(row["y_top"]),
                })
            except (ValueError, KeyError):
                continue
    return zs


def load_symbol_full(code: str, period: str = ""):
    """加载标的完整数据: {code, period, bars, signals, pens, segments, zhongshu, merged}
    按 datetime 对齐（K线可能经过包含处理，idx 与 step25 原始 idx 不一致）
    pens/segments/zhongshu: 由 step25 的 idx 映射到 K 线位置"""
    if not period:
        # 自动检测: 扫描 data/{code}_*_kline.csv
        for f in sorted(DATA_DIR.glob(f"{code}_*_kline.csv")):
            period = f.stem[len(code)+1:-len("_kline")]  # code 后第一个 _ 到 _kline
            break
    bars = load_kline(code, period)
    signals = load_step25(code, period)
    if not bars:
        return None
    return _build_full(code, period, bars, signals)


def _dt_to_ts(dt_str):
    """datetime 字符串 → unix 秒时间戳
    2026-08-14 BUG-B修复: 30s/3m 期货 K 线 datetime 带 .000000 微秒后缀
    """
    try:
        s = str(dt_str).strip()
        if len(s) > 16:
            try:
                return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").timestamp())
            except ValueError:
                return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
        return int(datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp())
    except Exception:
        return 0


def _build_full(code, period, bars, signals):
    """合并 K 线 + step25 信号，映射笔/线段/中枢 idx 到 K 线 pos
    方案A(v10.13): segments/zhongshu/pens 携带 time 值，前端用 time 而非 idx 匹配"""
    idx2dt = {s["idx"]: s["datetime"] for s in signals}
    dt2pos = {b["datetime"]: i for i, b in enumerate(bars)}
    sig_map = {s["datetime"]: s for s in signals}
    merged = []
    for b in bars:
        s = sig_map.get(b["datetime"], {})
        merged.append({
            **b,
            "time": _dt_to_ts(b["datetime"]),  # 2026-08-13 对齐8765: merged bar 带 time
            "signals": s.get("signals", []),
            "in_zhongshu": s.get("in_zhongshu", False),
            "summacd": s.get("summacd", 0.0),
        })
    pens = _extract_pens(signals)
    # 为笔加 time 值
    for p in pens:
        fd = idx2dt.get(p["start_idx"])
        td = idx2dt.get(p["end_idx"])
        p["start_time"] = _dt_to_ts(fd) if fd else 0
        p["end_time"] = _dt_to_ts(td) if td else 0
    segments = []
    for sg in load_segments(code, period):
        f_dt = idx2dt.get(sg["from_idx"])
        t_dt = idx2dt.get(sg["to_idx"])
        if f_dt is None or t_dt is None:
            continue
        segments.append({
            "from_idx": sg["from_idx"], "to_idx": sg["to_idx"],
            "from_time": _dt_to_ts(f_dt), "to_time": _dt_to_ts(t_dt),
            "from_price": sg["from_price"], "to_price": sg["to_price"],
            "label": sg["label"],
        })
    zhongshu = []
    for zs in load_zhongshu(code, period):
        l_dt = idx2dt.get(zs["x_left"])
        r_dt = idx2dt.get(zs["x_right"])
        if l_dt is None or r_dt is None:
            continue
        zhongshu.append({
            **zs,
            "x_left": zs["x_left"], "x_right": zs["x_right"],
            "x_left_time": _dt_to_ts(l_dt), "x_right_time": _dt_to_ts(r_dt),
        })
    return {"code": code, "period": period, "bars": merged, "total": len(merged),
            "pens": pens, "segments": segments, "zhongshu": zhongshu}


def _extract_pens(signals):
    """从 step25 的 top_mark/bottom_mark 提取笔连线
    top_mark='G'/'g' 或 bottom_mark='D'/'d' 表示笔端点; D=底, G=顶, 交替连接
    注意: 大小写表示不同级别分型，全部纳入，按 idx 排序后同 idx 去重"""
    points = []  # (idx, price, is_top)
    for s in signals:
        tm = str(s.get("top_mark", "")).strip()
        bm = str(s.get("bottom_mark", "")).strip()
        if tm and tm.upper() in ("G",):
            price = _safe_float(s.get("top_price"))
            if price > 0:
                points.append((s["idx"], price, True))
        if bm and bm.upper() in ("D",):
            price = _safe_float(s.get("bottom_price"))
            if price > 0:
                points.append((s["idx"], price, False))
    if not points:
        return []
    # 排序去重（同 idx 取第一个）
    points.sort(key=lambda p: p[0])
    dedup = []
    seen = set()
    for p in points:
        if p[0] not in seen:
            dedup.append(p)
            seen.add(p[0])
    # 交替连接: 确保第一个点后每个点方向交替
    pens = []
    for i in range(1, len(dedup)):
        start, end = dedup[i - 1], dedup[i]
        direction = "up" if end[2] and not start[2] else ("down" if not end[2] and start[2] else None)
        if direction is None:
            continue  # 同向连续（如顶顶），跳过
        pens.append({
            "start_idx": start[0], "end_idx": end[0],
            "start_price": round(start[1], 2), "end_price": round(end[1], 2),
            "direction": direction,
        })
    return pens


if __name__ == "__main__":
    # 快速自检
    syms = load_symbols()
    print(f"可用标的: {len(syms)}")
    for s in syms:
        print(f"  {s['code']} bars={s['bars']}")
    full = load_symbol_full("002475")
    if full:
        print(f"002475 合并后: {full['total']} 根")
        # 打印有信号的 bar
        sig_bars = [b for b in full["bars"] if b["signals"]]
        print(f"含信号 bar: {len(sig_bars)} 根")
        for b in sig_bars[:5]:
            print(f"  idx={b['idx']} {b['datetime']} {[s['name'] for s in b['signals']]}")
