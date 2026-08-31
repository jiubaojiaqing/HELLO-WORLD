"""
转换器：从 stockone 的 current_drawing.json → chan-trading 数据 CSV
不改 stockone 任何代码，只读取其产物。

用法:
    python engine/convert_drawing.py <drawing.json> <目标周期目录> <code>
    例如:
    python engine/convert_drawing.py /root/Downloads/学习副本/股票/stockone/data/current_drawing.json data/ 002475 --period 5min

产物:
    data/klines/{period}/{code}.csv       K线 idx,datetime,open,high,low,close
    data/step25/{period}/{code}.csv       信号(布尔列 + summacd + top/bottom mark)
    data/segments/{period}/{code}.csv     线段 from_idx,from_price,to_idx,to_price,label
    data/zhongshu/{period}/{code}.csv     中枢 zs_id,seg_type,x_left,x_right,y_bottom,y_top
"""
import csv
import json
import sys
from pathlib import Path

# 信号列（与 data_loader.SIGNAL_COLS 一致）
SIGNAL_COLS = [
    "is_top", "is_bottom", "is_XSG", "is_XXD",
    "is_SZBBC", "is_XDBBC", "is_SZ5BBC", "is_XD5BBC", "is_SZ7BBC", "is_XD7BBC",
    "is_SZZSPZBC", "is_XDZSPZBC", "is_SZQSBC", "is_XDQSBC",
]

# bbc label → 信号列
BBC_MAP = {
    "SZBBC": "is_SZBBC", "XDBBC": "is_XDBBC",
    "SZ5BBC": "is_SZ5BBC", "XD5BBC": "is_XD5BBC",
    "SZ7BBC": "is_SZ7BBC", "XD7BBC": "is_XD7BBC",
}

# extra signal → 信号列
EXTRA_MAP = {
    "XSG": "is_XSG", "XXD": "is_XXD",
    "SZZSPZBC": "is_SZZSPZBC", "XDZSPZBC": "is_XDZSPZBC",
    "SZQSBC": "is_SZQSBC", "XDQSBC": "is_XDQSBC",
}


def convert(drawing: dict, out_dir: Path, code: str, period: str = ""):
    """从 drawing json 生成 4 个 CSV（扁平化: {code}_{period}_kline.csv 等）"""
    out_dir.mkdir(parents=True, exist_ok=True)

    bars = drawing.get("klines", {}).get("bars", [])
    if not bars:
        print(f"❌ {code} [{period}] drawing 无 K线，跳过")
        return False

    # 1. K线 CSV
    kline_path = out_dir / f"{code}_{period}_kline.csv"
    with open(kline_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx", "datetime", "open", "high", "low", "close"])
        for b in bars:
            w.writerow([b["idx"], b["datetime"], b["open"], b["high"], b["low"], b["close"]])

    # 2. step25 CSV（信号）
    # 汇总: idx → 信号集
    signals_by_idx = {b["idx"]: set() for b in bars}
    mark_by_idx = {}
    price_by_idx = {}
    summacd_by_idx = {}

    # 分型 → top/bottom mark + 价格
    for p in drawing.get("fenxing", {}).get("points", []):
        idx = p["idx"]
        if idx not in signals_by_idx:
            signals_by_idx[idx] = set()
        ftype = p.get("ftype", "")
        mark = p.get("mark", "")
        if ftype == "top":
            signals_by_idx[idx].add("is_top")
        elif ftype == "bottom":
            signals_by_idx[idx].add("is_bottom")
        mark_by_idx[idx] = mark
        # 价格: 顶用 high，底用 low（与旧 step25 top_price/bottom_price 一致）
        price_by_idx[idx] = (p.get("high"), p.get("low"))

    # summacd
    for lb in drawing.get("summacd_labels", []):
        idx = lb.get("idx")
        if idx is None or idx not in signals_by_idx:
            continue
        summacd_by_idx[idx] = lb.get("summacd_value", 0.0)
        if lb.get("is_top"):
            signals_by_idx[idx].add("is_top")
        if lb.get("is_bottom"):
            signals_by_idx[idx].add("is_bottom")

    # 背驰
    for lb in drawing.get("bbc_labels", []):
        idx = lb.get("idx")
        bt = lb.get("bbc_type", "")
        col = BBC_MAP.get(bt)
        if col and idx in signals_by_idx:
            signals_by_idx[idx].add(col)

    # 扩展信号
    for lb in drawing.get("extra_signal_labels", []):
        idx = lb.get("idx")
        if idx is None or idx not in signals_by_idx:
            continue
        for st in lb.get("signal_type", []):
            col = EXTRA_MAP.get(st)
            if col:
                signals_by_idx[idx].add(col)

    step25_path = out_dir / f"{code}_{period}_step25.csv"
    with open(step25_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        header = ["idx", "datetime", "summacd", "top_mark", "bottom_mark",
                  "top_price", "bottom_price", "in_zhongshu"] + SIGNAL_COLS
        w.writerow(header)
        for b in bars:
            idx = b["idx"]
            sigs = signals_by_idx.get(idx, set())
            mark = mark_by_idx.get(idx, "")
            tp, bp = price_by_idx.get(idx, (None, None))
            row = [
                idx, b["datetime"],
                f"{summacd_by_idx.get(idx, 0.0):.6f}",
                mark if mark in ("G", "g") else "",
                mark if mark in ("D", "d") else "",
                f"{tp:.4f}" if tp is not None else "",
                f"{bp:.4f}" if bp is not None else "",
                "",  # in_zhongshu（可后续由中枢区间推导）
            ]
            for col in SIGNAL_COLS:
                row.append("true" if col in sigs else "false")
            w.writerow(row)

    # 3. 线段 CSV
    seg_path = out_dir / f"{code}_{period}_segments.csv"
    with open(seg_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_idx", "from_price", "to_idx", "to_price", "label"])
        segs = drawing.get("segments", {})
        if isinstance(segs, dict):
            for lines in (segs.get("blue_lines", []), segs.get("yellow_lines", [])):
                for s in lines:
                    w.writerow([s["from_idx"], s["from_price"], s["to_idx"], s["to_price"], s.get("label", "")])
        elif isinstance(segs, list):
            for s in segs:
                w.writerow([s["from_idx"], s["from_price"], s["to_idx"], s["to_price"], s.get("label", "")])

    # 4. 中枢 CSV
    zs_path = out_dir / f"{code}_{period}_zhongshu.csv"
    with open(zs_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zs_id", "seg_type", "x_left", "x_right", "y_bottom", "y_top"])
        for zs in drawing.get("zhongshu", {}).get("boxes", []):
            w.writerow([zs.get("zs_id", ""), zs.get("direction", ""),
                        zs["x_left"], zs["x_right"], zs["y_bottom"], zs["y_top"]])

    print(f"✅ {code} [{period or '默认'}] 转换完成: K线 {len(bars)} 根, "
          f"信号 {len([i for i,s in signals_by_idx.items() if s])} 根有信号, "
          f"线段 {(len(drawing.get('segments',{}).get('blue_lines',[])) if isinstance(drawing.get('segments'),dict) else len(drawing.get('segments',[]))) + (len(drawing.get('segments',{}).get('yellow_lines',[])) if isinstance(drawing.get('segments'),dict) else 0)}, "
          f"中枢 {len(drawing.get('zhongshu',{}).get('boxes',[]))}")
    return True


if __name__ == "__main__":
    drawing_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    code = sys.argv[3]
    period = sys.argv[4] if len(sys.argv) > 4 else ""
    d = json.load(open(drawing_path, encoding="utf-8"))
    ok = convert(d, out_dir, code, period)
    sys.exit(0 if ok else 1)
