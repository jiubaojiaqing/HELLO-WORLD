/* engine_offline.js — 离线引擎 (Python engine/csv_parser.py + engine/simulator.py 的 JS 移植)
 * 2026-08-30 阶段1: 真离线苹果 App 的前端引擎底座
 * 移植目标: parseOhlCsv / parseStep25Csv / mergeBars / extractPens / deriveSegments / deriveZhongshu / SimEngine
 * 对拍基线: 与 Python 引擎同输入必须结果一致 (见 tools_js_engine/check.js)
 * 兼容: 浏览器挂 window.OfflineEngine; Node 用 module.exports 供对拍
 */
(function (global) {
'use strict';

// ---------- 公共工具 ----------

// Python round() 精确等价实现: CPython 对 double 的精确十进制值做"正确舍入+半舍到偶"。
// 缩放法(x*100)会引入二次舍入误差; toFixed 在二进制精确平局(如 158.625)时取较大值而非取偶。
// 这里用 BigInt 对 double 的精确值 (m * 2^e) 做 m*10^d*2^e 的有理数半偶舍入, 与 CPython 完全一致。
function pyRound(x, d) {
  d = d || 0;
  if (!isFinite(x)) return x;
  if (x === 0) return 0;
  const sign = x < 0 ? -1n : 1n;
  const a = Math.abs(x);
  // 提取 double 精确表示: a = m * 2^e (m 为 53 位整数, 次正规数除外)
  const buf = new Float64Array(1); buf[0] = a;
  const bits = new BigUint64Array(buf.buffer)[0];
  const expField = (bits >> 52n) & 0x7FFn;
  const frac = bits & ((1n << 52n) - 1n);
  let m, e;
  if (expField === 0n) { m = frac; e = -1074; }
  else { m = frac | (1n << 52n); e = Number(expField) - 1075; }
  // 计算 roundHalfEven(a * 10^d) = roundHalfEven(m * 10^d * 2^e)
  let num = m * (10n ** BigInt(d));
  let N;
  if (e >= 0) { N = num << BigInt(e); }
  else {
    const D = 1n << BigInt(-e);
    const Q = num / D, R = num % D;
    const twice = R * 2n;
    if (twice > D) N = Q + 1n;
    else if (twice < D) N = Q;
    else N = (Q % 2n === 0n) ? Q : Q + 1n;   // 恰好半 → 取偶
  }
  return Number(sign * N) / Math.pow(10, d);
}

function uuidHex(n) {
  let s = '';
  const buf = new Uint8Array(n);
  (global.crypto || require('crypto').webcrypto).getRandomValues(buf);
  for (let i = 0; i < n; i++) s += buf[i].toString(16).padStart(2, '0');
  return s.substring(0, n);
}

// ---------- CSV 解析 (DictReader 等价) ----------
// 支持双引号包裹/转义引号/跨行字段; 返回 {header:[...], rows:[{col:val}]}
function parseCsv(text) {
  const rows = []; let cur = [''], row = [], inQ = false, i = 0;
  const s = String(text).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (s.charCodeAt(0) === 0xFEFF) pushCell('\uFEFF'); // BOM 保留进首格, 与 Python DictReader 一致(首列名带BOM)
  function pushCell(ch) { cur[0] += ch; }
  for (i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inQ) {
      if (ch === '"') { if (s[i + 1] === '"') { pushCell('"'); i++; } else { inQ = false; } }
      else pushCell(ch);
    } else if (ch === '"') { inQ = true; }
    else if (ch === ',') { row.push(cur[0]); cur = ['']; }
    else if (ch === '\n') { row.push(cur[0]); rows.push(row); row = []; cur = ['']; }
    else pushCell(ch);
  }
  if (cur[0] !== '' || row.length) { row.push(cur[0]); rows.push(row); }
  // 转结构化
  const out = [];
  let header = null;
  for (const r of rows) {
    if (header === null) {
      if (r.every(c => c.trim() === '')) continue; // 跳过空行
      header = r;
      continue;
    }
    if (r.length === 1 && r[0].trim() === '') continue; // DictReader 跳过空行
    const obj = {};
    for (let j = 0; j < header.length; j++) obj[header[j]] = j < r.length ? r[j] : '';
    out.push(obj);
  }
  return { header: header || [], rows: out };
}

const OHLC_COL_MAP = {
  open: ['open', 'openprice', 'open_price', '开盘价', '开盘'],
  high: ['high', 'highprice', 'high_price', '最高价', '最高'],
  low: ['low', 'lowprice', 'low_price', '最低价', '最低'],
  close: ['close', 'closeprice', 'close_price', '收盘价', '收盘'],
  datetime: ['datetime', 'date', 'time', 'dt', '日期', '时间', 'date_time', 'time_date'],
  idx: ['idx', 'id', '序号', 'index', 'no', 'num'],
  vol: ['vol', 'volume', '成交', '成交量', 'v'],
};
const STEP25_COL_MAP = {
  idx: ['idx', 'id', 'index', '序号', 'no'],
  datetime: ['datetime', 'date', 'time', 'dt', '日期', '时间'],
  summacd: ['summacd', 'sum_macd'],
  top_mark: ['top_mark', 'topmark', 'top'],
  bottom_mark: ['bottom_mark', 'bottommark', 'bottom'],
  top_price: ['top_price', 'topprice'],
  bottom_price: ['bottom_price', 'bottomprice'],
  in_zhongshu: ['in_zhongshu', 'inc', 'is_zhongshu'],
  is_top: ['is_top', 'istop'],
  is_bottom: ['is_bottom', 'isbottom'],
  is_XSG: ['is_xsg', 'xsg'],
  is_XXD: ['is_xxd', 'xxd'],
  is_SZBBC: ['is_szbbc', 'szbbc', 'is_szbcc'],
  is_XDBBC: ['is_xdbbc', 'xdbbc', 'is_xdbcc'],
  is_SZ5BBC: ['is_sz5bbc'],
  is_XD5BBC: ['is_xd5bbc'],
  is_SZ7BBC: ['is_sz7bbc'],
  is_XD7BBC: ['is_xd7bbc'],
  is_SZZSPZBC: ['is_szzspzbc'],
  is_XDZSPZBC: ['is_xdzspzbc'],
  is_SZQSBC: ['is_szqsbc'],
  is_XDQSBC: ['is_xdqsbc'],
  DIF: ['DIF', 'dif'],
  DEA: ['DEA', 'dea'],
  MACD: ['MACD', 'macd'],
};
const SIGNAL_COLS = ['is_top', 'is_bottom', 'is_XSG', 'is_XXD', 'is_SZBBC', 'is_XDBBC',
  'is_SZ5BBC', 'is_XD5BBC', 'is_SZ7BBC', 'is_XD7BBC', 'is_SZZSPZBC', 'is_XDZSPZBC',
  'is_SZQSBC', 'is_XDQSBC'];
const SIGNAL_NAMES = {
  is_top: '顶分型', is_bottom: '底分型', is_XSG: '新上涨', is_XXD: '新下跌',
  is_SZBBC: '上涨背驰', is_XDBBC: '下跌背驰', is_SZ5BBC: '5笔上涨背驰', is_XD5BBC: '5笔下跌背驰',
  is_SZ7BBC: '7笔上涨背驰', is_XD7BBC: '7笔下跌背驰', is_SZZSPZBC: '上涨盘整背驰', is_XDZSPZBC: '下跌盘整背驰',
  is_SZQSBC: '上涨趋势背驰', is_XDQSBC: '下跌趋势背驰',
};

function buildLookup(mapDict) {
  const lookup = {};
  for (const canonical of Object.keys(mapDict)) {
    for (const a of mapDict[canonical]) {
      lookup[a.trim().toLowerCase().replace(/ /g, '').replace(/_/g, '')] = canonical;
    }
  }
  return lookup;
}
const OHLC_LOOKUP = buildLookup(OHLC_COL_MAP);
const STEP25_LOOKUP = buildLookup(STEP25_COL_MAP);

function cleanHeader(h) {
  return String(h).trim().toLowerCase().replace(/ /g, '').replace(/_/g, '').replace(/^\uFEFF/, '');
}

// 复刻 Python _resolve_col 语义: 直接命中 canonical(后写覆盖) → 别名查表(先到先得) → 二次别名补充
function resolveCol(header, lookup, canonicalSet) {
  const result = {};
  const canonNorm = canonicalSet.map(c => [c, c.toLowerCase().replace(/_/g, '')]);
  for (const h of header) {
    const hClean = cleanHeader(h);
    let matched = null;
    for (const [c, norm] of canonNorm) { if (norm === hClean) { matched = c; break; } }
    if (matched) { result[matched] = h; continue; }
    const resolved = lookup[hClean];
    if (resolved && !(resolved in result)) result[resolved] = h;
  }
  for (const h of header) {
    const hClean = cleanHeader(h);
    if (hClean in lookup) {
      const resolved = lookup[hClean];
      if (!(resolved in result)) result[resolved] = h;
    }
  }
  return result;
}

function pyBool(v) {
  if (typeof v === 'boolean') return v;
  return ['true', '1', 'yes', '是'].includes(String(v).trim().toLowerCase());
}

function normDt(dt) {
  const s = String(dt == null ? '' : dt).trim();
  return (s && s.length >= 16) ? s.substring(0, 16) : s;
}

// datetime → unix 秒 (本地时区, 与 Python timestamp() 一致)
function dtToTs(dtStr) {
  try {
    const s = String(dtStr == null ? '' : dtStr).trim();
    let m;
    if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$/))) {
      return Math.floor(new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() / 1000);
    }
    if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/))) {
      return Math.floor(new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime() / 1000);
    }
    return 0;
  } catch (e) { return 0; }
}

// ---------- OHLC 清洗 ----------
function sanitizeOhlc(o, h, l, c) {
  for (const v of [o, h, l, c]) {
    if (!isFinite(v)) return null;
  }
  if (o <= 0 || h <= 0 || l <= 0 || c <= 0) return null;
  if (h < l) { const t = h; h = l; l = t; }
  o = Math.max(l, Math.min(h, o));
  c = Math.max(l, Math.min(h, c));
  return [o, h, l, c];
}

// ---------- 对外: OHLC CSV ----------
function parseOhlCsv(text) {
  const { header, rows } = parseCsv(text);
  if (!header.length) throw new Error('CSV 为空');
  const col = resolveCol(header, OHLC_LOOKUP, ['open', 'high', 'low', 'close', 'datetime', 'idx']);
  const missing = ['open', 'high', 'low', 'close', 'datetime'].filter(c => !(c in col));
  if (missing.length) throw new Error('OHLC CSV 缺少必要列: ' + missing.join(', '));
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    try {
      const dt = String(row[col.datetime] == null ? '' : row[col.datetime]).trim();
      const res = sanitizeOhlc(
        parseFloat(row[col.open]), parseFloat(row[col.high]),
        parseFloat(row[col.low]), parseFloat(row[col.close]));
      if (!res) continue; // NaN/越界行: Python 是 except 前已 float 失败抛错, 但 sanitize None 跳过
      out.push({ idx: i, datetime: normDt(dt), open: res[0], high: res[1], low: res[2], close: res[3] });
    } catch (e) {
      throw new Error('OHLC CSV 第 ' + (i + 2) + ' 行解析失败: ' + e.message);
    }
  }
  if (!out.length) throw new Error('OHLC CSV 无有效数据行');
  return out;
}

// 注意: Python 版对 float() 失败的行会抛 ValueError(整包失败), 仅 sanitize None 才跳过。
// JS parseFloat 对非法串返回 NaN → sanitize 返回 null 跳过, 行为有差异; 对拍用合法数据无影响。

// ---------- 对外: step25 CSV ----------
function parseStep25Csv(text) {
  const { header, rows } = parseCsv(text);
  if (!header.length) return [];
  const canonSet = Object.keys(STEP25_COL_MAP);
  const col = resolveCol(header, STEP25_LOOKUP, canonSet);
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const signals = [];
    for (const sc of SIGNAL_COLS) {
      const h = col[sc];
      if (h && pyBool(row[h])) signals.push({ type: sc, name: SIGNAL_NAMES[sc] || sc });
    }
    const f = (k, def) => {
      const h = col[k];
      if (h && row[h]) { const v = parseFloat(row[h]); return isNaN(v) ? def : v; }
      return def;
    };
    const idxRaw = col.idx && row[col.idx] ? parseInt(row[col.idx]) : NaN;
    out.push({
      idx: !isNaN(idxRaw) ? idxRaw : i,
      datetime: col.datetime && row[col.datetime] ? normDt(row[col.datetime]) : '',
      signals: signals,
      in_zhongshu: pyBool(col.in_zhongshu ? row[col.in_zhongshu] : ''),
      summacd: f('summacd', 0.0),
      top_mark: String(col.top_mark ? (row[col.top_mark] || '') : '').trim(),
      bottom_mark: String(col.bottom_mark ? (row[col.bottom_mark] || '') : '').trim(),
      top_price: f('top_price', 0.0),
      bottom_price: f('bottom_price', 0.0),
    });
  }
  return out;
}

// ---------- 对外: merge ----------
function mergeBars(ohlRows, step25Rows) {
  let sigMap = null;
  if (step25Rows && step25Rows.length) {
    sigMap = {};
    for (const s of step25Rows) sigMap[s.datetime] = s;
  }
  const merged = [];
  for (const b of ohlRows) {
    const ts = dtToTs(b.datetime);
    if (sigMap) {
      const s = sigMap[b.datetime] || {};
      merged.push(Object.assign({}, b, {
        time: ts,
        signals: s.signals || [],
        in_zhongshu: !!s.in_zhongshu,
        summacd: s.summacd !== undefined ? s.summacd : 0.0,
        top_mark: s.top_mark || '',
        bottom_mark: s.bottom_mark || '',
        top_price: s.top_price !== undefined ? s.top_price : 0.0,
        bottom_price: s.bottom_price !== undefined ? s.bottom_price : 0.0,
      }));
    } else {
      merged.push(Object.assign({}, b, {
        time: ts, signals: [], in_zhongshu: false, summacd: 0.0,
        top_mark: '', bottom_mark: '', top_price: 0.0, bottom_price: 0.0,
      }));
    }
  }
  return merged;
}

// ---------- 笔 / 分型 ----------
function extractPens(bars) {
  const points = [];
  for (const b of bars) {
    const tm = String(b.top_mark || '').trim();
    const bm = String(b.bottom_mark || '').trim();
    if (tm && tm.toUpperCase() === 'G') points.push([b.idx, parseFloat(b.top_price || 0), true]);
    if (bm && bm.toUpperCase() === 'D') points.push([b.idx, parseFloat(b.bottom_price || 0), false]);
  }
  if (!points.length) return [];
  points.sort((a, b) => a[0] - b[0]);
  const dedup = [], seen = new Set();
  for (const p of points) {
    if (!seen.has(p[0])) { dedup.push(p); seen.add(p[0]); }
  }
  const pens = [];
  for (let i = 1; i < dedup.length; i++) {
    const start = dedup[i - 1], end = dedup[i];
    let direction;
    if (end[2] && !start[2]) direction = 'up';
    else if (!end[2] && start[2]) direction = 'down';
    else continue;
    pens.push({
      start_idx: start[0], end_idx: end[0],
      start_price: pyRound(start[1], 2), end_price: pyRound(end[1], 2),
      direction: direction,
    });
  }
  return pens;
}

function fenxingPoints(bars) {
  const points = [];
  for (const b of bars) {
    const tm = String(b.top_mark || '').trim();
    const bm = String(b.bottom_mark || '').trim();
    if (tm.toUpperCase() === 'G') {
      const p = parseFloat(b.top_price || 0);
      if (p > 0) points.push({ idx: b.idx, mark: 'G', price: p });
    }
    if (bm.toUpperCase() === 'D') {
      const p = parseFloat(b.bottom_price || 0);
      if (p > 0) points.push({ idx: b.idx, mark: 'D', price: p });
    }
  }
  points.sort((a, b) => a.idx - b.idx);
  return points;
}

// ---------- 线段推导 ----------
function deriveSegments(bars) {
  const points = fenxingPoints(bars);
  const conns = [];
  for (const b of bars) {
    for (const s of (b.signals || [])) {
      if (s.type === 'is_XSG') {
        const p = parseFloat(b.top_price || 0);
        if (p > 0) conns.push({ idx: b.idx, mark: 'XSG', price: p });
      } else if (s.type === 'is_XXD') {
        const p = parseFloat(b.bottom_price || 0);
        if (p > 0) conns.push({ idx: b.idx, mark: 'XXD', price: p });
      }
    }
  }
  conns.sort((a, b) => a.idx - b.idx);
  const segs = [];
  for (let i = 1; i < conns.length; i++) {
    const a = conns[i - 1], e = conns[i];
    if (a.mark === 'XSG' && e.mark === 'XXD') segs.push([a, e, 'XSG->XXD (蓝色实线，向下线段)']);
    else if (a.mark === 'XXD' && e.mark === 'XSG') segs.push([a, e, 'XXD->XSG (蓝色实线，向上线段)']);
  }
  if (conns.length) {
    const first = conns[0], last = conns[conns.length - 1];
    if (first.mark === 'XSG') {
      const cands = points.filter(p => p.mark === 'D' && p.idx < first.idx);
      if (cands.length) {
        const s = cands.reduce((m, p) => p.price < m.price ? p : m);
        segs.unshift([s, first, 'D@k' + s.idx + '->XSG (黄色虚线，向上线段)']);
      }
    } else if (first.mark === 'XXD') {
      const cands = points.filter(p => p.mark === 'G' && p.idx < first.idx);
      if (cands.length) {
        const s = cands.reduce((m, p) => p.price > m.price ? p : m);
        segs.unshift([s, first, 'G@k' + s.idx + '->XXD (黄色虚线，向下线段)']);
      }
    }
    if (last.mark === 'XSG') {
      const cands = points.filter(p => p.mark === 'D' && p.idx > last.idx);
      if (cands.length) {
        const e = cands.reduce((m, p) => p.price < m.price ? p : m);
        segs.push([last, e, 'XSG->D@k' + e.idx + ' (黄色实线，向下线段)']);
      }
    } else if (last.mark === 'XXD') {
      const cands = points.filter(p => p.mark === 'G' && p.idx > last.idx);
      if (cands.length) {
        const e = cands.reduce((m, p) => p.price > m.price ? p : m);
        segs.push([last, e, 'XXD->G@k' + e.idx + ' (黄色实线，向上线段)']);
      }
    }
  }
  return segs.map(([s, e, label]) => ({
    from_idx: s.idx, from_price: pyRound(s.price, 2),
    to_idx: e.idx, to_price: pyRound(e.price, 2),
    label: label,
  }));
}

// ---------- 中枢推导 ----------
function deriveZhongshu(bars) {
  const windows = [];
  let cur = null;
  for (const b of bars) {
    if (b.in_zhongshu) {
      if (!cur) cur = [];
      cur.push(b);
    } else if (cur) {
      windows.push(cur); cur = null;
    }
  }
  if (cur) windows.push(cur);
  const boxes = [];
  for (const w of windows) {
    const pts = [];
    for (const b of w) {
      const tm = String(b.top_mark || '').trim();
      const bm = String(b.bottom_mark || '').trim();
      if (tm.toUpperCase() === 'G') {
        const p = parseFloat(b.top_price || 0);
        if (p > 0) pts.push(['G', b.idx, p]);
      }
      if (bm.toUpperCase() === 'D') {
        const p = parseFloat(b.bottom_price || 0);
        if (p > 0) pts.push(['D', b.idx, p]);
      }
    }
    if (pts.length < 4) continue;
    const gs = pts.filter(p => p[0] === 'G').map(p => p[2]);
    const ds = pts.filter(p => p[0] === 'D').map(p => p[2]);
    if (!gs.length || !ds.length) continue;
    const xs = pts.map(p => p[1]);
    boxes.push({
      zs_id: boxes.length + 1,
      seg_type: pts[0][0] === 'D' ? 'down' : 'up',
      x_left: Math.min.apply(null, xs), x_right: Math.max.apply(null, xs),
      y_bottom: Math.max.apply(null, ds), y_top: Math.min.apply(null, gs),
    });
  }
  return boxes;
}

// ---------- 模拟交易引擎 ----------
function SimEngine(opts) {
  // opts: {code, bars, period, initAsset, feesOn, commissionRate, minCommission,
  //        stampRate, transferRate, windowSize, histN, startIdx, tplus1, seedRandom}
  const o = Object.assign({
    period: '', initAsset: 100000.0, feesOn: true,
    commissionRate: 0.00025, minCommission: 5.0, stampRate: 0.001, transferRate: 0.00002,
    windowSize: 100, histN: 50, startIdx: null, tplus1: true,
  }, opts);
  this.bars = o.bars;
  this.windowSize = o.windowSize;
  this.histN = o.histN;
  this.initAsset = parseFloat(o.initAsset);
  this.feesOn = o.feesOn;
  this.commissionRate = o.commissionRate;
  this.minCommission = o.minCommission;
  this.stampRate = o.stampRate;
  this.transferRate = o.transferRate;

  let startIdx = o.startIdx;
  if (startIdx === null || startIdx === undefined) {
    const total = this.bars.length;
    let maxStart = total - 10 - this.windowSize;
    if (maxStart < 1) maxStart = 1;
    startIdx = Math.floor(Math.random() * (maxStart + 1)); // randint(0, maxStart) 含端点
  }
  this.startIdx = startIdx;
  this.window = this.bars.slice(startIdx, startIdx + this.windowSize);

  this.sessionId = uuidHex(16).substring(0, 12);
  this.code = o.code;
  this.period = o.period;
  this.tradeable = ['', 'day', '30min', '30s', '3m'].includes(this.period);
  this.tplus1 = o.tplus1;
  this.pos = 0;
  this.avgCost = 0.0;
  this.closeProfit = 0.0;
  this.cash = this.initAsset;
  this.curPos = this.histN - 1;
  this.holdings = [];
  this.status = 'PLAYING';
  this.trades = [];
  this.createdAt = new Date().toISOString();
}

SimEngine.prototype.barView = function (bar) {
  return {
    idx: bar.idx, datetime: bar.datetime, time: dtToTs(bar.datetime),
    open: bar.open, high: bar.high, low: bar.low, close: bar.close,
    signals: bar.signals || [], in_zhongshu: !!bar.in_zhongshu,
    summacd: pyRound(bar.summacd || 0, 4),
  };
};

SimEngine.prototype.state = function () {
  const cur = this.curPos < this.window.length ? this.window[this.curPos] : this.window[this.window.length - 1];
  return {
    session_id: this.sessionId, status: this.status, code: this.code, period: this.period,
    tradeable: this.tradeable, hist_n: this.histN,
    init_asset: pyRound(this.initAsset, 2), cash: pyRound(this.cash, 2),
    position: this.pos,
    avg_cost: this.pos ? pyRound(this.avgCost, 3) : 0,
    total_asset: pyRound(this.totalAsset(cur.close), 2),
    float_profit: pyRound(this.floatProfit(cur.close), 2),
    close_profit: pyRound(this.closeProfit, 2),
    available: pyRound(this.cash, 2),
    cur_pos: this.curPos, total_bars: this.window.length,
    start_idx: this.startIdx,
    start_date: this.window[0].datetime, end_date: this.window[this.window.length - 1].datetime,
    cur_bar: this.barView(cur),
    trades: this.status === 'FINISHED' ? this.trades : this.trades.slice(-20),
    fees_on: this.feesOn, tplus1: this.tplus1,
    window: this.window.slice(0, this.curPos + 1).map(b => this.barView(b)),
    all_bars: this.window.map(b => this.barView(b)),
  };
};

SimEngine.prototype.sellableQty = function () {
  if (!this.holdings.length) return 0;
  if (!this.tplus1) return this.pos;
  const curDt = this.window[this.curPos].datetime.substring(0, 10);
  let sum = 0;
  for (const h of this.holdings) if (h.dt.substring(0, 10) !== curDt) sum += h.qty;
  return sum;
};

SimEngine.prototype.totalAsset = function (price) { return this.cash + this.pos * price; };
SimEngine.prototype.floatProfit = function (price) {
  if (this.pos <= 0) return 0.0;
  return (price - this.avgCost) * this.pos;
};

SimEngine.prototype.calcFee = function (amount, isBuy) {
  if (!this.feesOn) return 0.0;
  const commission = Math.max(amount * this.commissionRate, this.minCommission);
  const transfer = amount * this.transferRate;
  let total = commission + transfer;
  if (!isBuy) total += amount * this.stampRate;
  return pyRound(total, 2);
};

SimEngine.prototype.buy = function (ratio, qty) {
  ratio = ratio === undefined ? 1.0 : ratio;
  if (this.status !== 'PLAYING') return { ok: false, msg: 'Cannot trade in current state' };
  if (!this.tradeable) return { ok: false, msg: 'Reference only, not tradeable' };
  if (this.curPos < this.histN) return { ok: false, msg: 'Observation period (first ' + this.histN + ' bars) — advance to trade' };
  const cur = this.window[this.curPos];
  const price = cur.close;
  if (qty && qty > 0) { qty = parseInt(qty); }
  else if (ratio > 0) {
    let maxAmount = this.cash;
    if (this.feesOn) maxAmount = this.cash / (1 + this.commissionRate + this.transferRate);
    qty = Math.floor(maxAmount * ratio / price / 100) * 100;
  } else return { ok: false, msg: 'Specify quantity or ratio' };
  if (qty <= 0) return { ok: false, msg: 'Insufficient funds' };
  let amount = qty * price;
  let fee = this.calcFee(amount, true);
  if (amount + fee > this.cash) {
    qty -= 100;
    if (qty <= 0) return { ok: false, msg: 'Insufficient funds (incl. fees)' };
    amount = qty * price;
    fee = this.calcFee(amount, true);
  }
  const oldCost = this.avgCost * this.pos;
  this.cash -= (amount + fee);
  this.pos += qty;
  const totalCost = oldCost + amount + fee;
  this.avgCost = this.pos ? totalCost / this.pos : 0;
  const dtKey = cur.datetime.substring(0, 10);
  if (this.holdings.length && this.holdings[this.holdings.length - 1].dt.substring(0, 10) === dtKey) {
    const h = this.holdings[this.holdings.length - 1];
    // Python 语义: 先 h.qty += qty, 再 cost = (旧cost*(新qty-qty) + amount)/新qty → 即 (旧cost*旧qty + amount)/新qty
    const oldQty = h.qty;
    h.cost = (h.cost * oldQty + amount) / (oldQty + qty);
    h.qty = oldQty + qty;
    h.fee = pyRound((h.fee || 0) + fee, 2);
  } else {
    this.holdings.push({ qty: qty, cost: amount / qty, fee: fee, dt: cur.datetime });
  }
  this.trades.push({
    id: uuidHex(8), type: 'buy', idx: cur.idx, datetime: cur.datetime,
    price: pyRound(price, 3), qty: qty, amount: pyRound(amount, 2),
    fee: fee, status: 'holding',
  });
  return { ok: true, msg: 'Bought ' + qty + ' shares @ ' + price.toFixed(2), qty: qty, price: price,
    fee: fee, position: this.pos, cash: pyRound(this.cash, 2),
    avg_cost: this.pos ? pyRound(this.avgCost, 3) : 0,
    float_profit: pyRound(this.floatProfit(price), 2),
    total_asset: pyRound(this.totalAsset(price), 2) };
};

// 注: 买入同日合并已按 Python 语义实现 (见上)

SimEngine.prototype.sell = function (ratio, qty) {
  ratio = ratio === undefined ? 1.0 : ratio;
  if (this.status !== 'PLAYING') return { ok: false, msg: 'Cannot trade in current state' };
  if (!this.tradeable) return { ok: false, msg: 'Reference only, not tradeable' };
  if (this.curPos < this.histN) return { ok: false, msg: 'Observation period (first ' + this.histN + ' bars) — advance to trade' };
  if (this.pos <= 0) return { ok: false, msg: 'No position' };
  const cur = this.window[this.curPos];
  const price = cur.close;
  const sellable = this.sellableQty();
  if (sellable <= 0) return { ok: false, msg: 'T+1: Today\'s buy cannot be sold today' };
  if (qty && qty > 0) { qty = Math.min(parseInt(qty), sellable); }
  else if (ratio > 0) {
    qty = Math.floor(sellable * ratio / 100) * 100;
    if (qty <= 0) qty = sellable;
  } else return { ok: false, msg: 'Specify quantity or ratio' };
  if (qty <= 0) return { ok: false, msg: 'Invalid sell quantity' };
  let remain = qty, soldCost = 0.0, soldFee = 0.0;
  const newHoldings = [];
  for (const h of this.holdings) {
    if (remain <= 0) { newHoldings.push(h); continue; }
    if (h.dt.substring(0, 10) === cur.datetime.substring(0, 10) && this.tplus1) { newHoldings.push(h); continue; }
    const take = Math.min(h.qty, remain);
    const feePortion = take * (h.fee || 0) / h.qty;
    soldCost += take * h.cost;
    soldFee += feePortion;
    h.fee = pyRound((h.fee || 0) - feePortion, 2);
    remain -= take;
    h.qty -= take;
    if (h.qty > 0) newHoldings.push(h);
  }
  this.holdings = newHoldings;
  const amount = qty * price;
  const fee = this.calcFee(amount, false);
  const profit = amount - soldCost - fee - soldFee;
  this.closeProfit = pyRound(this.closeProfit + profit, 2);
  this.cash += (amount - fee);
  this.pos -= qty;
  if (this.pos <= 0) { this.pos = 0; this.avgCost = 0.0; }
  else {
    let totalCost = 0;
    for (const h of this.holdings) totalCost += h.cost * h.qty + (h.fee || 0);
    this.avgCost = totalCost / this.pos;
  }
  this.trades.push({
    id: uuidHex(8), type: 'sell', idx: cur.idx, datetime: cur.datetime,
    price: pyRound(price, 3), qty: qty, amount: pyRound(amount, 2),
    fee: fee, profit: pyRound(profit, 2), status: 'closed',
  });
  return { ok: true, msg: 'Sold ' + qty + ' shares @ ' + price.toFixed(2), qty: qty, price: price,
    fee: fee, profit: pyRound(profit, 2), position: this.pos,
    cash: pyRound(this.cash, 2), avg_cost: this.pos ? pyRound(this.avgCost, 3) : 0,
    float_profit: pyRound(this.floatProfit(price), 2),
    close_profit: pyRound(this.closeProfit, 2),
    total_asset: pyRound(this.totalAsset(price), 2) };
};

SimEngine.prototype.advance = function (n) {
  n = n || 1;
  if (this.status !== 'PLAYING') return { ok: false, msg: 'State: ' + this.status };
  this.curPos += n;
  if (this.curPos >= this.window.length - 1) {
    this.curPos = this.window.length - 1;
    this.autoClose();
    this.status = 'FINISHED';
  }
  return { ok: true, cur_pos: this.curPos, status: this.status };
};

SimEngine.prototype.autoClose = function () {
  if (this.pos <= 0) return;
  const cur = this.window[this.curPos];
  const price = cur.close;
  const qty = this.pos;
  const amount = qty * price;
  const fee = this.calcFee(amount, false);
  let soldCost = 0, soldFee = 0;
  for (const h of this.holdings) { soldCost += h.cost * h.qty; soldFee += (h.fee || 0); }
  const profit = amount - soldCost - fee - soldFee;
  this.closeProfit = pyRound(this.closeProfit + profit, 2);
  this.cash += (amount - fee);
  this.pos = 0; this.avgCost = 0.0; this.holdings = [];
  this.trades.push({
    id: uuidHex(8), type: 'sell', idx: cur.idx, datetime: cur.datetime,
    price: pyRound(price, 3), qty: qty, amount: pyRound(amount, 2),
    fee: fee, profit: pyRound(profit, 2), status: 'closed', note: 'Auto-closed',
  });
};

SimEngine.prototype.result = function () {
  const finalPrice = this.window[this.window.length - 1].close;
  const totalAsset = this.totalAsset(finalPrice);
  const profit = totalAsset - this.initAsset;
  const profitRate = this.initAsset ? profit / this.initAsset * 100 : 0;
  const buys = this.trades.filter(t => t.type === 'buy');
  const sells = this.trades.filter(t => t.type === 'sell');
  const closed = sells.filter(t => 'profit' in t);
  const win = closed.filter(t => t.profit > 0);
  const losses = closed.filter(t => t.profit <= 0);
  return {
    session_id: this.sessionId, status: this.status,
    init_asset: pyRound(this.initAsset, 2),
    final_asset: pyRound(totalAsset, 2),
    total_profit: pyRound(profit, 2),
    profit_rate: pyRound(profitRate, 2),
    trade_count: buys.length, sell_count: closed.length,
    win_count: win.length, loss_count: losses.length,
    max_win: pyRound(win.length ? Math.max.apply(null, win.map(t => t.profit)) : 0, 2),
    max_loss: pyRound(losses.length ? Math.min.apply(null, losses.map(t => t.profit)) : 0, 2),
    remain_position: this.pos,
    end_date: this.window[this.window.length - 1].datetime,
  };
};

// ---------- 导出 ----------
const OfflineEngine = {
  pyRound, dtToTs, parseCsv, parseOhlCsv, parseStep25Csv, mergeBars,
  extractPens, fenxingPoints, deriveSegments, deriveZhongshu, SimEngine,
};

if (typeof module !== 'undefined' && module.exports) module.exports = OfflineEngine;
if (global) global.OfflineEngine = OfflineEngine;

})(typeof window !== 'undefined' ? window : globalThis);
