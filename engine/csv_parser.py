"""CSV 上传解析: OHLC(必须) + step25(可选) → 合并成 SimEngine 可接受的 bars"""
import csv
import io
import re
from pathlib import Path

# 列名模糊匹配映射
_OHLC_COL_MAP = {
    'open': ['open', 'openprice', 'open_price', 'openprice', '开盘价', '开盘'],
    'high': ['high', 'highprice', 'high_price', 'highprice', '最高价', '最高'],
    'low': ['low', 'lowprice', 'low_price', 'lowprice', '最低价', '最低'],
    'close': ['close', 'closeprice', 'close_price', 'closeprice', '收盘价', '收盘'],
    'datetime': ['datetime', 'date', 'time', 'dt', 'datetime', '日期', '时间', 'date_time', 'time_date'],
    'idx': ['idx', 'id', '序号', 'index', 'no', 'num'],
    'vol': ['vol', 'volume', 'volume', '成交', '成交量', 'vol', 'v'],
}

_STEP25_COL_MAP = {
    'idx': ['idx', 'id', 'index', '序号', 'no'],
    'datetime': ['datetime', 'date', 'time', 'dt', '日期', '时间', 'datetime'],
    'summacd': ['summacd', 'sum_macd'],
    'top_mark': ['top_mark', 'topmark', 'top'],
    'bottom_mark': ['bottom_mark', 'bottommark', 'bottom'],
    'top_price': ['top_price', 'topprice'],
    'bottom_price': ['bottom_price', 'bottomprice'],
    'in_zhongshu': ['in_zhongshu', 'in_zhongshu', 'inc', 'is_zhongshu'],
    'is_top': ['is_top', 'is_top', 'istop'],
    'is_bottom': ['is_bottom', 'is_bottom', 'isbottom'],
    'is_XSG': ['is_xsg', 'xsg', 'is_xsg'],
    'is_XXD': ['is_xxd', 'xxd', 'is_xxd'],
    'is_SZBBC': ['is_szbbc', 'is_szbbc', 'szbbc', 'is_szbcc'],
    'is_XDBBC': ['is_xdbbc', 'is_xdbbc', 'xdbbc', 'is_xdbcc'],
    'is_SZ5BBC': ['is_sz5bbc'],
    'is_XD5BBC': ['is_xd5bbc'],
    'is_SZ7BBC': ['is_sz7bbc'],
    'is_XD7BBC': ['is_xd7bbc'],
    'is_SZZSPZBC': ['is_szzspzbc'],
    'is_XDZSPZBC': ['is_xdzspzbc'],
    'is_SZQSBC': ['is_szqsbc'],
    'is_XDQSBC': ['is_xdqsbc'],
    # MACD 列(可不传)
    'DIF': ['DIF', 'dif'],
    'DEA': ['DEA', 'dea'],
    'MACD': ['MACD', 'macd'],
}

SIGNAL_COLS = [
    'is_top', 'is_bottom', 'is_XSG', 'is_XXD',
    'is_SZBBC', 'is_XDBBC', 'is_SZ5BBC', 'is_XD5BBC',
    'is_SZ7BBC', 'is_XD7BBC', 'is_SZZSPZBC', 'is_XDZSPZBC',
    'is_SZQSBC', 'is_XDQSBC',
]
SIGNAL_NAMES = {
    'is_top': '顶分型', 'is_bottom': '底分型',
    'is_XSG': '新上涨', 'is_XXD': '新下跌',
    'is_SZBBC': '上涨背驰', 'is_XDBBC': '下跌背驰',
    'is_SZ5BBC': '5笔上涨背驰', 'is_XD5BBC': '5笔下跌背驰',
    'is_SZ7BBC': '7笔上涨背驰', 'is_XD7BBC': '7笔下跌背驰',
    'is_SZZSPZBC': '上涨盘整背驰', 'is_XDZSPZBC': '下跌盘整背驰',
    'is_SZQSBC': '上涨趋势背驰', 'is_XDQSBC': '下跌趋势背驰',
}


def _build_lookup(map_dict):
    """构建 {normalized_name -> canonical_col} 的查找表"""
    lookup = {}
    for canonical, aliases in map_dict.items():
        for a in aliases:
            k = a.strip().lower().replace(' ', '').replace('_', '')
            lookup[k] = canonical
    return lookup


_OHLC_LOOKUP = _build_lookup(_OHLC_COL_MAP)
_STEP25_LOOKUP = _build_lookup(_STEP25_COL_MAP)


def _resolve_col(header, lookup, canonical_set):
    """从 header 中找到属于 canonical_set 的列，返回 {canonical: header_raw}"""
    result = {}
    for h in header:
        h_clean = h.strip().lower().replace(' ', '').replace('_', '').lstrip('ufeff')
        for c in canonical_set:
            if c.lower().replace('_', '') == h_clean:
                result[c] = h
                break
        if c not in result:
            resolved = lookup.get(h_clean)
            if resolved and resolved not in result:
                result[resolved] = h
    # 二次尝试: 用 lookup 补充
    for h in header:
        h_clean = h.strip().lower().replace(' ', '').replace('_', '').lstrip('ufeff')
        if h_clean in lookup:
            resolved = lookup[h_clean]
            if resolved not in result:
                result[resolved] = h
    return result


def _bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('true', '1', 'yes', '是')


def _norm_dt(dt):
    """统一 datetime 格式: YYYY-MM-DD HH:MM（去掉秒）"""
    return dt.strip()[:16] if dt and len(dt.strip()) >= 16 else dt.strip()

def _sanitize_ohlc(open_, high, low, close):
    """清洗 OHLC 数据: 确保 open/close 在 [low, high] 范围内
    返回 None 表示该行数据无效应跳过
    处理: NaN/Inf → 跳过, 负数/零值 → 跳过, high<low → 交换, open/close 越界 → clamp
    """
    import math
    for v in (open_, high, low, close):
        if math.isnan(v) or math.isinf(v):
            return None
    if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
        return None
    if high < low:
        high, low = low, high
    open_ = max(low, min(high, open_))
    close = max(low, min(high, close))
    return open_, high, low, close


def parse_ohl_csv(text):
    """解析 OHLC CSV → list of {idx, datetime, open, high, low, close}"""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError('CSV 为空')
    col = _resolve_col(reader.fieldnames, _OHLC_LOOKUP, {'open', 'high', 'low', 'close', 'datetime', 'idx'})
    missing = [c for c in ['open', 'high', 'low', 'close', 'datetime'] if c not in col]
    if missing:
        raise ValueError(f'OHLC CSV 缺少必要列: {", ".join(missing)}')
    rows = []
    for i, row in enumerate(reader):
        try:
            dt = row[col['datetime']].strip()
            result = _sanitize_ohlc(
                float(row[col['open']]), float(row[col['high']]),
                float(row[col['low']]), float(row[col['close']])
            )
            if result is None:
                continue
            o, h, l, c = result
            rows.append({
                'idx': i,
                'datetime': _norm_dt(dt),
                'open': o,
                'high': h,
                'low': l,
                'close': c,
            })
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f'OHLC CSV 第 {i+2} 行解析失败: {e}')
    if not rows:
        raise ValueError('OHLC CSV 无有效数据行')
    return rows


def parse_step25_csv(text):
    """解析 step25 CSV → list of dict (同 data_loader 格式)"""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    col = _resolve_col(reader.fieldnames, _STEP25_LOOKUP, _STEP25_COL_MAP.keys())
    rows = []
    for i, row in enumerate(reader):
        signals = []
        for sc in SIGNAL_COLS:
            h = col.get(sc)
            if h and _bool(row.get(h)):
                signals.append({'type': sc, 'name': SIGNAL_NAMES.get(sc, sc)})
        def _f(k, default=0.0):
            h = col.get(k)
            if h and row.get(h):
                try:
                    return float(row[h])
                except ValueError:
                    return default
            return default
        rows.append({
            'idx': int(row[col['idx']]) if col.get('idx') and row.get(col['idx']) else i,
            'datetime': _norm_dt(row[col['datetime']]) if col.get('datetime') and row.get(col['datetime']) else '',
            'signals': signals,
            'in_zhongshu': bool(_bool(row.get(col.get('in_zhongshu', '')))),
            'summacd': _f('summacd'),
            'top_mark': str(row.get(col.get('top_mark', ''), '')).strip(),
            'bottom_mark': str(row.get(col.get('bottom_mark', ''), '')).strip(),
            'top_price': _f('top_price'),
            'bottom_price': _f('bottom_price'),
        })
    return rows


def _dt_to_ts(dt_str):
    """datetime 字符串 → unix 秒时间戳"""
    from datetime import datetime
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


def merge_bars(ohl_rows, step25_rows=None):
    """按 datetime 合并 OHLC + step25 → list of bar (含 signals)"""
    if step25_rows:
        sig_map = {s['datetime']: s for s in step25_rows}
    merged = []
    for b in ohl_rows:
        ts = _dt_to_ts(b['datetime'])
        if step25_rows:
            s = sig_map.get(b['datetime'], {})
            bar = {
                **b,
                'time': ts,
                'signals': s.get('signals', []),
                'in_zhongshu': s.get('in_zhongshu', False),
                'summacd': s.get('summacd', 0.0),
                'top_mark': s.get('top_mark', ''),
                'bottom_mark': s.get('bottom_mark', ''),
                'top_price': s.get('top_price', 0.0),
                'bottom_price': s.get('bottom_price', 0.0),
            }
        else:
            bar = {**b, 'time': ts, 'signals': [], 'in_zhongshu': False, 'summacd': 0.0,
                   'top_mark': '', 'bottom_mark': '', 'top_price': 0.0, 'bottom_price': 0.0}
        merged.append(bar)
    return merged


def _extract_pens(bars):
    """从 top_mark/bottom_mark 提取笔 (同 data_loader._extract_pens)"""
    points = []
    for b in bars:
        tm = str(b.get('top_mark', '')).strip()
        bm = str(b.get('bottom_mark', '')).strip()
        if tm and tm.upper() in ('G',):
            points.append((b['idx'], float(b.get('top_price', 0) or 0), True))
        if bm and bm.upper() in ('D',):
            points.append((b['idx'], float(b.get('bottom_price', 0) or 0), False))
    if not points:
        return []
    points.sort(key=lambda p: p[0])
    dedup = []; seen = set()
    for p in points:
        if p[0] not in seen:
            dedup.append(p); seen.add(p[0])
    pens = []
    for i in range(1, len(dedup)):
        start, end = dedup[i-1], dedup[i]
        if end[2] and not start[2]:
            direction = 'up'
        elif not end[2] and start[2]:
            direction = 'down'
        else:
            continue
        pens.append({
            'start_idx': start[0], 'end_idx': end[0],
            'start_price': round(start[1], 2), 'end_price': round(end[1], 2),
            'direction': direction,
        })
    return pens


def _fenxing_points(bars):
    """从 bars 提取全量 G/D 分型极值点 [{idx, mark, price}]"""
    points = []
    for b in bars:
        tm = str(b.get('top_mark', '')).strip()
        bm = str(b.get('bottom_mark', '')).strip()
        if tm.upper() == 'G':
            p = float(b.get('top_price', 0) or 0)
            if p > 0:
                points.append({'idx': b['idx'], 'mark': 'G', 'price': p})
        if bm.upper() == 'D':
            p = float(b.get('bottom_price', 0) or 0)
            if p > 0:
                points.append({'idx': b['idx'], 'mark': 'D', 'price': p})
    points.sort(key=lambda x: x['idx'])
    return points


def derive_segments(bars):
    """从合并后的 bars 推导线段（复刻上游 step21 规则）
    蓝色实线: XSG/XXD 按 idx 排序后相邻配对
    首段黄虚线: 首个 XSG 前的最低 D（或首个 XXD 前的最高 G）
    尾段黄实线: 最后一个 XSG 后的最低 D（或最后一个 XXD 后的最高 G）"""
    points = _fenxing_points(bars)
    conns = []
    for b in bars:
        for s in b.get('signals', []):
            if s.get('type') == 'is_XSG':
                p = float(b.get('top_price', 0) or 0)
                if p > 0:
                    conns.append({'idx': b['idx'], 'mark': 'XSG', 'price': p})
            elif s.get('type') == 'is_XXD':
                p = float(b.get('bottom_price', 0) or 0)
                if p > 0:
                    conns.append({'idx': b['idx'], 'mark': 'XXD', 'price': p})
    conns.sort(key=lambda x: x['idx'])
    segs = []
    for a, e in zip(conns, conns[1:]):
        if a['mark'] == 'XSG' and e['mark'] == 'XXD':
            segs.append((a, e, 'XSG->XXD (蓝色实线，向下线段)'))
        elif a['mark'] == 'XXD' and e['mark'] == 'XSG':
            segs.append((a, e, 'XXD->XSG (蓝色实线，向上线段)'))
    if conns:
        first, last = conns[0], conns[-1]
        if first['mark'] == 'XSG':
            cands = [p for p in points if p['mark'] == 'D' and p['idx'] < first['idx']]
            if cands:
                s = min(cands, key=lambda x: x['price'])
                segs.insert(0, (s, first, f'D@k{s["idx"]}->XSG (黄色虚线，向上线段)'))
        elif first['mark'] == 'XXD':
            cands = [p for p in points if p['mark'] == 'G' and p['idx'] < first['idx']]
            if cands:
                s = max(cands, key=lambda x: x['price'])
                segs.insert(0, (s, first, f'G@k{s["idx"]}->XXD (黄色虚线，向下线段)'))
        if last['mark'] == 'XSG':
            cands = [p for p in points if p['mark'] == 'D' and p['idx'] > last['idx']]
            if cands:
                e = min(cands, key=lambda x: x['price'])
                segs.append((last, e, f'XSG->D@k{e["idx"]} (黄色实线，向下线段)'))
        elif last['mark'] == 'XXD':
            cands = [p for p in points if p['mark'] == 'G' and p['idx'] > last['idx']]
            if cands:
                e = max(cands, key=lambda x: x['price'])
                segs.append((last, e, f'XXD->G@k{e["idx"]} (黄色实线，向上线段)'))
    return [{'from_idx': s['idx'], 'from_price': round(s['price'], 2),
             'to_idx': e['idx'], 'to_price': round(e['price'], 2),
             'label': label} for s, e, label in segs]


def derive_zhongshu(bars):
    """从合并后的 bars 推导中枢
    in_zhongshu 连续 True 区间 → 窗口；窗内 GD 四点反推:
    ZG = min(G价格), ZD = max(D价格), x范围 = GD点 idx 的 min/max"""
    windows = []
    cur = None
    for b in bars:
        if b.get('in_zhongshu'):
            if cur is None:
                cur = []
            cur.append(b)
        elif cur is not None:
            windows.append(cur)
            cur = None
    if cur is not None:
        windows.append(cur)
    boxes = []
    for w in windows:
        pts = []
        for b in w:
            tm = str(b.get('top_mark', '')).strip()
            bm = str(b.get('bottom_mark', '')).strip()
            if tm.upper() == 'G':
                p = float(b.get('top_price', 0) or 0)
                if p > 0:
                    pts.append(('G', b['idx'], p))
            if bm.upper() == 'D':
                p = float(b.get('bottom_price', 0) or 0)
                if p > 0:
                    pts.append(('D', b['idx'], p))
        if len(pts) < 4:
            continue
        gs = [p[2] for p in pts if p[0] == 'G']
        ds = [p[2] for p in pts if p[0] == 'D']
        if not gs or not ds:
            continue
        xs = [p[1] for p in pts]
        boxes.append({
            'zs_id': len(boxes) + 1,
            'seg_type': 'down' if pts[0][0] == 'D' else 'up',
            'x_left': min(xs), 'x_right': max(xs),
            'y_bottom': max(ds), 'y_top': min(gs),
        })
    return boxes
