# 缠论模拟交易学习 App

基于**离线缠论数据**的模拟交易学习工具。不运行 00-25 pipeline，直接读取预计算的 step25 信号数据，逐根 K 线模拟交易。

## 快速启动

```bash
cd /root/Downloads/chan-trading
/root/.venv_tqsdk/bin/python3 app.py
# 打开 http://localhost:8770
```

## 数据格式

每个标的 2 份文件，按 `datetime` 对齐（K线可能经过包含处理，idx 与 step25 原始 idx 不一致，统一按日期 join）：

| 文件 | 内容 | 位置 |
|---|---|---|
| `data/klines/{code}.csv` | idx, datetime, open, high, low, close | K 线 |
| `data/step25/{code}.csv` | idx, datetime, 全部缠论信号列 | step25 输出 |

step25 信号列：`is_top/is_bottom`（分型）、`is_XSG/is_XXD`（新上涨/下跌）、`is_SZBBC/is_XDBBC`（背驰）、`is_SZ5BBC/is_XD5BBC/is_SZ7BBC/is_XD7BBC`（多级别背驰）、`is_SZZSPZBC/is_XDZSPZBC`（中枢盘整背驰）、`is_SZQSBC/is_XDQSBC`（趋势背驰）、`in_zhongshu`（中枢内）、`summacd`、`top_mark/bottom_mark/top_price/bottom_price`（笔端点 G/D 标记）。

**新增标的**：放入对应 CSV 即可，前端下拉自动出现。

## 当前标的（多周期）

5 标的 × 6 周期版本（默认 + 1min/5min/30min/day/week），由 stockone (8767) 生成：

| 代码 | 名称 | 可用周期 |
|---|---|---|
| 002475 | 立讯精密 | 默认(242) 1min(423) 5min(446) 30min(419) day(461) week(467) |
| 300502 | 新易盛 | 默认(425) 1min(470) 5min(460) 30min(421) day(470) week(413) |
| 603986 | 兆易创新 | 默认(476) 1min(447) 5min(396) 30min(419) day(477) week(364) |
| 688353 | 华盛锂电 | 默认(517) 1min(377) 5min(418) 30min(392) day(437) week(150) |
| 688661 | 和林微纳 | 默认(543) 1min(361) 5min(411) 30min(404) day(459) week(218) |

前端「标的 + 周期」两级选择 + 周期快捷筛选。周期自动识别（时间戳粒度）。

### 多周期数据下载

用 stockone 的 WS 切换周期触发 build，从产物转换（**不改 stockone 代码**）：
```
python tools/download_periods.py 002475          # 全部周期
python tools/download_periods.py 002475 5min     # 单周期
python tools/download_periods.py 002475,603986 1min
```
产物：`data/{klines,step25,segments,zhongshu}/{period}/{code}.csv`

## 功能

- 多标的离线数据（5 标的：日线 ×2 + 30分钟 ×3），**周期自动识别 + 前端筛选**
- 随机截取时段（默认 100 根，排除最近 10 根未完成数据）
- 全仓/半仓/按数量买卖（100 股整数倍）
- 费用模拟：佣金万 2.5（最低 5 元）+ 印花税千 1（卖出）+ 过户费万 0.2
- 逐根推进 / 快进 5 根 / 自动播放（0.8s/根）
- **初始化 50 根历史 K 线**（观察期，不可交易）+ **退出模拟按钮**
- **T+1 规则**：当日买入的股份当日不能卖出，仅非当日持仓可卖；结束时强制清仓结算
- **30 分钟周期仅供查看**（不可交易，A股分钟线不具备 T+1 实盘条件）
- K线图：红涨绿跌 + **笔连线（蓝=上笔/橙=下笔）** + **线段（粗蓝线）** + **中枢矩形高亮（紫）** + 缠论信号标记（最近 10 根教学遮罩）+ 当前价虚线
- **MACD 副图**（DIF/DEA 线 + 柱状图）
- **K 线图表交互**：滚轮缩放 / 拖拽平移 / 每次买卖标记点（B 绿 / S 红）
- **排行榜**：昵称保存成绩（自动记忆），按盈亏率排名（🥇🥈🥉）；支持**周期/标的筛选 + 分页**；点击昵称查看**个人战绩**（累计统计 + 历史明细）
- **缠论教学**：鼠标悬停 K 线 → 悬浮显示 OHLC + 信号中文含义（顶/底分型、盘整/趋势/笔背驰教学解释）+ 图例信号说明

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/symbols` | 可用标的列表 |
| GET | `/api/data/{code}` | 标的全量数据 |
| POST | `/api/session/start` | 创建会话（随机时段） |
| GET | `/api/session/{id}` | 会话状态 |
| POST | `/api/session/advance` | 推进 N 根 |
| POST | `/api/session/buy` | 买入（ratio/qty） |
| POST | `/api/session/sell` | 卖出 |
| GET | `/api/session/{id}/result` | 模拟结果 |
| POST | `/api/session/save_result` | 保存成绩（昵称） |
| GET | `/api/leaderboard` | 排行榜（limit/offset/period/code 筛选） |
| GET | `/api/user/{nickname}` | 用户累计统计 + 历史战绩明细 |

## 项目结构

```
chan-trading/
├── app.py              # FastAPI 入口 (端口 8770)
├── engine/
│   ├── data_loader.py  # 离线数据加载 (K线 + step25 合并)
│   └── simulator.py    # 模拟交易引擎 (状态机/买卖/费用/统计)
├── data/
│   ├── klines/         # K线 CSV
│   └── step25/         # 缠论信号 CSV
├── static/index.html   # 前端页面
└── README.md
```

## 路线图

- [x] v0.1 单标的跑通（002475 立讯精密, 242 根日线）
- [ ] 多标的离线数据 + 随机选标的
- [ ] 缠论绘图增强（笔/线段/中枢矩形框）
- [ ] 排行榜 / 用户系统 / 付费
