# -*- coding: utf-8 -*-
"""全标的批量对拍基线: 遍历 data/*_kline.csv, 固定交易序列, 输出汇总 JSON"""
import sys, json, glob, os, re
sys.path.insert(0, r"c:\Users\Administrator\Documents\trae_projects\chan-trading")
sys.stdout.reconfigure(encoding='utf-8')

from engine.csv_parser import parse_ohl_csv, parse_step25_csv, merge_bars, _extract_pens, derive_segments, derive_zhongshu
from engine.simulator import SimEngine

BASE = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\data"
OUT_PATH = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\tools_js_engine\py_baseline_all.json"

def clean(obj):
    s = json.dumps(obj, ensure_ascii=False)
    s = re.sub(r'"session_id":\s*"[0-9a-f]+"', '"session_id":"X"', s)
    s = re.sub(r'"created_at":\s*"[^"]*"', '"created_at":"X"', s)
    s = re.sub(r'"id":\s*"[0-9a-f]{8}"', '"id":"X"', s)
    return json.loads(s)

results = {}
skipped = []
for kpath in sorted(glob.glob(os.path.join(BASE, '*_kline.csv'))):
    name = os.path.basename(kpath)[:-len('_kline.csv')]
    if name.startswith('kline_'):  # 测试残留跳过
        continue
    code, period = name.rsplit('_', 1)
    spath = os.path.join(BASE, name + '_step25.csv')
    try:
        ohl = parse_ohl_csv(open(kpath, encoding='utf-8-sig').read())
        s25 = parse_step25_csv(open(spath, encoding='utf-8-sig').read()) if os.path.exists(spath) else []
        bars = merge_bars(ohl, s25)
        segments = derive_segments(bars)
        zhongshu = derive_zhongshu(bars)
        pens = _extract_pens(bars)
        # 窗口参数: bars 不足时缩小窗口, 保证有交易空间
        ws = min(150, max(100, len(bars) - 100))
        if len(bars) < ws + 60:
            skipped.append((name, 'too_short', len(bars)))
            continue
        start_idx = 60
        eng = SimEngine(code, bars, period=period, init_asset=1000000.0, fees_on=True,
                        window_size=ws, start_idx=start_idx, tplus1=True)
        eng.advance(60)
        buy1 = eng.buy(ratio=0.6)
        eng.advance(40)
        sell1 = eng.sell(ratio=0.5)
        eng.advance(10)
        buy2 = eng.buy(qty=500)
        eng.advance(100000)
        res = eng.result()
        st = eng.state()
        results[name] = {
            'ohl_count': len(ohl), 's25_count': len(s25), 'bars_count': len(bars),
            'segments': segments, 'zhongshu': zhongshu, 'pens': pens,
            'buy1': clean(buy1), 'sell1': clean(sell1), 'buy2': clean(buy2),
            'result': clean(res),
            'final_status': st['status'], 'final_position': st['position'],
            'final_cash': st['cash'], 'final_trades': clean(st['trades']),
            'first_bar': clean(st['all_bars'][0]), 'last_bar': clean(st['all_bars'][-1]),
            'window_len': len(st['window']), 'all_bars_len': len(st['all_bars']),
            'start_idx': st['start_idx'],
        }
    except Exception as e:
        skipped.append((name, repr(e), 0))

with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump({'results': results, 'skipped': skipped}, f, ensure_ascii=False, indent=1)
print('symbols:', len(results), 'skipped:', skipped)
