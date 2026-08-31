// 生成 static/data_bundle.js: 把 data/ 全部标的打包成前端离线可用的 JS 数据包
// 结构对齐 /api/data + _build_full: bars(含time/signals/in_zhongshu/summacd) + pens(含start/end_time)
//   + segments(含from_time/to_time) + zhongshu(含x_left_time/x_right_time)
'use strict';
const fs = require('fs');
const path = require('path');
const OE = require('../static/engine_offline.js');

const DATA = path.join(__dirname, '..', 'data');
const OUT = path.join(__dirname, '..', 'static', 'data_bundle.js');

const bundle = {};
let skipped = [];
for (const f of fs.readdirSync(DATA).filter(n => n.endsWith('_kline.csv') && !n.startsWith('kline_')).sort()) {
  const name = f.slice(0, -'_kline.csv'.length);          // e.g. 002475_30min
  const code = name.slice(0, name.lastIndexOf('_'));
  const period = name.slice(name.lastIndexOf('_') + 1);
  try {
    const ohl = OE.parseOhlCsv(fs.readFileSync(path.join(DATA, f), 'utf8'));
    const s25p = path.join(DATA, name + '_step25.csv');
    const s25 = fs.existsSync(s25p) ? OE.parseStep25Csv(fs.readFileSync(s25p, 'utf8')) : [];
    const bars = OE.mergeBars(ohl, s25);
    // idx→datetime 映射 (优先 step25 的 idx 空间; 无 step25 时用 bars 自身)
    const idx2dt = {};
    (s25.length ? s25 : bars).forEach(s => { idx2dt[s.idx] = s.datetime; });
    const ts = dt => OE.dtToTs(dt) || 0;

    const pens = OE.extractPens(s25.length ? s25 : bars).map(p => ({
      ...p,
      start_time: ts(idx2dt[p.start_idx]), end_time: ts(idx2dt[p.end_idx]),
    }));
    const segments = OE.deriveSegments(bars).map(sg => ({
      ...sg,
      from_time: ts(idx2dt[sg.from_idx]), to_time: ts(idx2dt[sg.to_idx]),
    })).filter(sg => sg.from_time && sg.to_time);
    const zhongshu = OE.deriveZhongshu(bars).map(zs => ({
      ...zs,
      x_left_time: ts(idx2dt[zs.x_left]), x_right_time: ts(idx2dt[zs.x_right]),
    })).filter(zs => zs.x_left_time && zs.x_right_time);

    bundle[name] = {
      code, period,
      total: bars.length,
      bars: bars.map(b => ({ idx: b.idx, datetime: b.datetime, time: b.time,
        open: b.open, high: b.high, low: b.low, close: b.close,
        signals: b.signals, in_zhongshu: b.in_zhongshu, summacd: b.summacd })),
      pens, segments, zhongshu,
    };
    console.log('packed', name, 'bars=' + bars.length, 'pens=' + pens.length,
      'segs=' + segments.length, 'zs=' + zhongshu.length);
  } catch (e) {
    skipped.push(name + ': ' + e.message);
  }
}

const js = '// 2026-08-30 自动生成: 预装数据包 (构建工具 tools_js_engine/build_bundle.js, 勿手改)\n'
  + 'window._DATA_BUNDLE = ' + JSON.stringify(bundle) + ';\n';
fs.writeFileSync(OUT, js, 'utf8');
const mb = (fs.statSync(OUT).size / 1024 / 1024).toFixed(2);
console.log('---');
console.log('bundle:', OUT, mb + ' MB,', Object.keys(bundle).length, 'symbols, skipped:', JSON.stringify(skipped));
