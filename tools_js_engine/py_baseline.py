# -*- coding: utf-8 -*-
"""对拍基线: 用 Python 引擎跑 002475 数据 + 固定交易序列, 输出 JSON 供 check.js 比对"""
import sys, json, io
sys.path.insert(0, r"c:\Users\Administrator\Documents\trae_projects\chan-trading")
sys.stdout.reconfigure(encoding='utf-8')

from engine.csv_parser import parse_ohl_csv, parse_step25_csv, merge_bars, _extract_pens, derive_segments, derive_zhongshu
from engine.simulator import SimEngine

BASE = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\data"
kline = open(BASE + r"\002475_30min_kline.csv", encoding='utf-8-sig').read()
step25 = open(BASE + r"\002475_30min_step25.csv", encoding='utf-8-sig').read()

ohl = parse_ohl_csv(kline)
s25 = parse_step25_csv(step25)
bars = merge_bars(ohl, s25)
segments = derive_segments(bars)
zhongshu = derive_zhongshu(bars)
pens = _extract_pens(bars)

# 固定参数会话: start_idx=100, window=150, 全流程交易
eng = SimEngine('002475', bars, period='30min', init_asset=1000000.0, fees_on=True,
                window_size=150, start_idx=100, tplus1=True)
eng.advance(60)
buy1 = eng.buy(ratio=0.6)
eng.advance(40)
sell1 = eng.sell(ratio=0.5)
eng.advance(10)
buy2 = eng.buy(qty=500)
# 推进到结束
eng.advance(10000)
res = eng.result()
st = eng.state()

def clean(obj):
    """剔除随机字段 (session_id/trade id/created_at)"""
    s = json.dumps(obj, ensure_ascii=False)
    import re
    s = re.sub(r'"session_id":\s*"[0-9a-f]+"', '"session_id":"X"', s)
    s = re.sub(r'"created_at":\s*"[^"]*"', '"created_at":"X"', s)
    s = re.sub(r'"id":\s*"[0-9a-f]{8}"', '"id":"X"', s)
    return json.loads(s)

out = {
    'ohl_count': len(ohl), 's25_count': len(s25), 'bars_count': len(bars),
    'segments': segments, 'zhongshu': zhongshu, 'pens': pens,
    'buy1': clean(buy1), 'sell1': clean(sell1), 'buy2': clean(buy2),
    'result': clean(res),
    'final_status': st['status'],
    'final_position': st['position'],
    'final_cash': st['cash'],
    'final_trades_n': len(st['trades']),
    'final_trades': clean(st['trades']),
    'first_bar': clean(st['all_bars'][0]),
    'last_bar': clean(st['all_bars'][-1]),
    'window_len': len(st['window']),
    'all_bars_len': len(st['all_bars']),
    'start_idx': st['start_idx'],
}
out_path = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\tools_js_engine\py_baseline.json"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(json.dumps(out, ensure_ascii=False, indent=1))
print('baseline written:', out_path)
print(json.dumps({k: out[k] for k in ('ohl_count', 'bars_count', 'final_status', 'final_cash')}, ensure_ascii=False))
