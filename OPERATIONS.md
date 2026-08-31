# 大富翁之生财有道 (8769) — 数据采集与处理流程

> 版本: 1.0.0 | 更新: 2026-08-08

## 概述

8769 项目（大富翁之生财有道，chan-trading）是一个缠论模拟交易App，支持9个标的（股票+期权）的缠论K线+笔/线段/中枢+背驰信号的完整加载。

## 9个标的数据源

| 标的 | 周期 | 目录 | 缠论数据来源 |
|:----|:----:|:----|:----|
| 002475 | day | `data/klines/day/002475.csv` | 8765 workspace (pytdx) |
| 603986 | day | `data/klines/day/603986.csv` | 8765 workspace |
| 300502 | 30min | `data/klines/30min/300502.csv` | 8765 workspace |
| 688353 | 30min | `data/klines/30min/688353.csv` | 8765 workspace |
| 688661 | 30min | `data/klines/30min/688661.csv` | 8765 workspace |
| MO2608-C-7500_30s | 30s | `data/klines/30s/MO2608-C-7500_30s.csv` | **8765 pipeline运行** |
| MO2608-P-7500_30s | 30s | `data/klines/30s/MO2608-P-7500_30s.csv` | **8765 pipeline运行** |
| MO2608-C-7500_3m | 3m | `data/klines/3m/MO2608-C-7500_3m.csv` | **8765 pipeline运行** |
| MO2608-P-7500_3m | 3m | `data/klines/3m/MO2608-P-7500_3m.csv` | **8765 pipeline运行** |

---

## 一、股票数据采集流程

### 1.1 K线数据

**来源**: pytdx 直连通达信服务器，实时拉取。
**存储**: `data/klines/{周期子目录}/{代码}.csv`（day/30min/5min/1min等）
**格式**: `idx,datetime,open,high,low,close`

```csv
0,2026-06-11 06:00:00,1.234,1.250,1.220,1.240
1,2026-06-11 06:15:00,1.240,1.260,1.235,1.255
```

### 1.2 缠论数据（笔/线段/中枢/背驰）

**来源**: 8765 项目（realtrade）的 workspace 目录，其 pipeline 已离线计算完毕。
**Workspace 位置**: `realtrade/data/workspace_{周期}/`（如 workspace_30m/ workspace_15m/ workspace_1h/）
**关键文件**: `step24_macd_with_markers.csv`（含完整信号列）+ `数据10_pens.csv`（笔端点）+ `数据22/23_zhongshu.csv`（中枢）

**加载逻辑**（在启动时或数据刷新时）：
1. 从 `workspace_{周期}/step24_macd_with_markers.csv` 提取 `top_mark/bottom_mark/summacd/in_zhongshu/is_*` 列
2. 按 datetime 对齐到 8769 K线
3. 写入 `data/step25/{周期}/{代码}.csv`
4. 从 `数据10_pens.csv` 写入 `data/segments/{周期}/{代码}.csv`
5. 从 `数据22_zhongshu.csv`/`数据23_zhongshu.csv` 写入 `data/zhongshu/{周期}/{代码}.csv`

**输出格式（step25）**:
```csv
idx,datetime,summacd,top_mark,bottom_mark,top_price,bottom_price,in_zhongshu,is_top,is_bottom,is_XSG,is_XXD,is_SZBBC,is_XDBBC,is_SZ5BBC,is_XD5BBC,is_SZ7BBC,is_XD7BBC,is_SZZSPZBC,is_XDZSPZBC,is_SZQSBC,is_XDQSBC
```

---

## 二、期权数据采集流程

### 2.1 K线数据

**来源**: 8769 内置（tqsdk或预置CSV），非pytdx。
**存储**: `data/klines/{30s|3m}/{code}.csv`
**格式**: `idx,datetime,open,high,low,close`（与股票相同）

```csv
0,2026-08-05 06:19:00,157.6,158.2,155.4,156.2
1,2026-08-05 06:19:30,156.2,159.2,155.8,159.0
```

### 2.2 缠论数据（需运行8765 pipeline生成）

**关键区别**: 期权K线与8765 workspace不同步（时间段不同），不能直接从workspace文件复制。必须将8769的K线注入8765 pipeline重新计算。

**完整流程**（每次期权K线更新后需运行）：

#### 步骤A: 备份8765当前数据
```bash
cp realtrade/data/current_kline.csv /tmp/8765_current_kline_backup.csv
cp realtrade/data/current_drawing.json /tmp/8765_current_drawing_backup.json
```

#### 步骤B: 8769 K线 → 8765 格式
```python
# 读8769 K线
with open('chan-trading/data/klines/{period}/{code}.csv') as f:
    rows = list(csv.DictReader(f))

# 写8765 current_kline.csv
with open('realtrade/data/current_kline.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['idx','time','open','high','low','close','symbol'])
    for r in rows:
        dt = datetime.strptime(r['datetime'].strip(), '%Y-%m-%d %H:%M:%S')
        ts = int(dt.timestamp())
        symbol = 'CFFEX.' + code.replace('_30s','').replace('_3m','')  # CFFEX.MO2608-C-7500
        w.writerow([r['idx'], ts, r['open'], r['high'], r['low'], r['close'], symbol])
```

#### 步骤C: 运行8765 25步 pipeline
```python
import os, sys
os.chdir('realtrade/00-24step/current')
sys.path.insert(0, 'realtrade/00-24step/current')
os.environ['KLINE_DURATION'] = '30'    # 30s→30, 3m→180
from build_drawing_table import main
main()
```

**pipeline产出**: `realtrade/data/current_drawing.json`（包含：klines/pens/segments/zhongshu/summacd_labels/bbc_labels/extra_signal_labels）

#### 步骤D: 提取drawing.json → 8769格式
**关键坑**: 8765 pipeline 过滤NaN K线，drawing中bar的idx与8769 K线idx不一致（偏移）。必须用 `datetime` 做桥梁：
```
8765 drawing.from_time (unix) → datetime → 8769 K线 datetime → 8769 idx
```

**笔(pens)**: 从 `pen_lines.GD_connections` 提取，笔端点写入step25的`top_mark/bottom_mark/top_price/bottom_price`

**线段(segments)**: 从 `segments.blue_lines` + `segments.yellow_lines` 提取，写入 `data/segments/{period}/{code}.csv`

**中枢(zhongshu)**: 从 `zhongshu.boxes` 提取，写入 `data/zhongshu/{period}/{code}.csv`

**summacd**: 从 `summacd_labels` 按datetime对齐到8769 K线

**背驰信号(BBC)**: 从 `bbc_labels` 提取，`XDBBC`/`SZBBC` 写入 `is_XDBBC`/`is_SZBBC`

**额外信号**: 从 `extra_signal_labels` 提取，`SZZSPZBC`/`XDZSPZBC`/`SZQSBC`/`XDQSBC` 写入对应列

#### 步骤E: 恢复8765原始数据
```bash
cp /tmp/8765_current_kline_backup.csv realtrade/data/current_kline.csv
cp /tmp/8765_current_drawing_backup.json realtrade/data/current_drawing.json
```

**重要**: 8765 pipeline 不改8765代码，只临时替换current_kline.csv输入文件。

---

## 三、8769 数据加载与处理流程

### 3.1 加载入口

`app.py` 路由 → `engine/data_loader.py` 各函数：

```
GET /api/data/{code}?period={period}
  → load_symbol_full(code, period)
    → load_kline(code, period)     → data/klines/{period}/{code}.csv
    → load_step25(code, period)    → data/step25/{period}/{code}.csv
    → load_segments(code, period)  → data/segments/{period}/{code}.csv
    → load_zhongshu(code, period)  → data/zhongshu/{period}/{code}.csv
    → _extract_pens(signals)       → 从step25的top_mark/bottom_mark重建笔
    → 按idx/datetime合并为bar结构
  → 返回 {code, period, total, bars, pens, segments, zhongshu}
```

### 3.2 周期检测

`data_loader.detect_period(code)`：从K线时间戳间隔自动推断周期（30s/3m/5min/30min/day/week）。
路由使用 `{code:path}` 支持含`-`的MO期权代码。

### 3.3 合并逻辑

1. `load_kline()` 读K线，提取bars数组
2. `load_step25()` 读信号CSV，提取 signals列表、summacd、in_zhongshu、top_mark、bottom_mark、top_price、bottom_price
3. 按idx对齐合并：bar.idx 匹配 signal.idx，bar内合并所有信号字段
4. `load_segments()`/`load_zhongshu()` 按idx→datetime→窗口内position重映射坐标
5. `_extract_pens()` 从top_mark/bottom_mark的交替端点重建笔连线（方向up/down）

### 3.4 关键路径常量

```python
BASE_DIR = chan-trading/
DATA_DIR = BASE_DIR/data/
KLINE_DIR = DATA_DIR/klines/
STEP25_DIR = DATA_DIR/step25/
# segments/zhongshu 同DATA_DIR下
```

**周期目录映射**：`period=""` → 根目录；`period="30min"` → `data/klines/30min/`；`period="30s"` → `data/klines/30s/`

### 3.5 前端渲染

8769 前端从API读取数据，Lightweight Charts v5.2.0 渲染K线，笔/线段/中枢在图上叠加。
笔/线段/中枢的idx查找用 `symbolData.bars`（全量），K线显示用 `s.all_bars`（窗口）。
图表创建时传入 `localization: {locale: 'zh-CN'/'en-US'}` 控制时间轴日期语言。

---

## 四、股票 vs 期权 关键差异

| 维度 | 股票 | 期权 |
|:----|:----|:----|
| K线来源 | pytdx通达信 | tqsdk/预置CSV |
| 缠论数据源 | 8765 workspace静态文件 | 8765 pipeline动态运行 |
| 8769 K线 vs 8765 K线 | 同时间段，可直接对齐 | **不同时间段**，需datetime桥接 |
| 周期 | day/30min | 30s/3m |
| 代码格式 | 纯数字(002475) | 含`-`和`_`(MO2608-C-7500_30s) |
| 路由 | `{code}` | `{code:path}` |
| 笔/线段/中枢比例 | 58笔/8段/7枢(461K) | 59笔/10段/7枢(600K,30s粒度更细) |
| 更新频率 | 盘后批量 | 实时(K线持续更新，pipeline需按需重跑) |

## 五、数据文件位置一览

### 8769 (chan-trading)
```
data/
├── klines/
│   ├── day/002475.csv  603986.csv
│   ├── 30min/300502.csv 688353.csv 688661.csv
│   ├── 30s/MO2608-C-7500_30s.csv  MO2608-P-7500_30s.csv
│   └── 3m/MO2608-C-7500_3m.csv  MO2608-P-7500_3m.csv
├── step25/
│   ├── 30min/ (股票)
│   ├── 30s/ (期权-30s)
│   └── 3m/ (期权-3m)
├── segments/ (同上子目录)
└── zhongshu/ (同上子目录)
```

### 8765 (realtrade)
```
data/
├── current_kline.csv          ← 临时输入(运行pipeline前需备份)
├── current_drawing.json       ← pipeline输出(运行pipeline后提取)
└── workspace_{周期}/
    ├── current_kline.csv      ← workspace的K线
    ├── step24_macd_with_markers.csv  ← 完整信号数据
    ├── 数据10_pens.csv          ← 笔端点
    ├── 数据22_zhongshu.csv     ← 中枢(向下)
    └── 数据23_zhongshu.csv     ← 中枢(向上)
```

## 六、期权缠论数据重跑触发条件

当期权K线数据更新（新交易日数据追加）后，需要重跑8765 pipeline，步骤如下：
1. 备份8765 → 2. 转K线格式 → 3. 跑pipeline → 4. 提取写回8769 → 5. 恢复8765
6. 重启8769服务（`kill`旧进程 + 重新启动），确认HTTP 200

每次只需跑4个期权（30s×2 + 3m×2），总耗时约30秒。