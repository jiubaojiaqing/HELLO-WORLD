#!/usr/bin/env python3
"""
8769 项目全面测试脚本
- 19 个标的 × 5 次 = 95 次模拟交易
- 检查: 数据完整性、缠论信号、笔/线段/中枢、买卖逻辑、T+1、费用、定位
- 不改项目代码，只找 bug
"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8769"
RESULTS = []
BUGS = []

def api(method, path, data=None):
    url = BASE + path
    if data is not None:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method=method,
            headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__msg__": e.read().decode()[:200]}
    except Exception as e:
        return {"__error__": "EXC", "__msg__": str(e)}

def check(cond, msg, symbol=None, run=None):
    if not cond:
        BUGS.append({"symbol": symbol, "run": run, "msg": msg})

def test_symbol(code, period, run):
    """对一个标的跑一次完整模拟交易测试"""
    bugs_local = []
    tag = f"{code}({period})"

    # 1. 获取数据
    data = api("GET", f"/api/data/{code}", {"period": period})
    if "__error__" in data:
        bugs_local.append(f"数据获取失败: {data['__msg__']}")
        return bugs_local

    bars = data.get("bars", [])
    pens = data.get("pens", [])
    segments = data.get("segments", [])
    zhongshu = data.get("zhongshu", [])
    total = data.get("total", 0)

    # 2. 数据完整性检查
    if total != len(bars):
        bugs_local.append(f"total({total}) != len(bars)({len(bars)})")
    if len(bars) < 150:
        bugs_local.append(f"K线数不足: {len(bars)} < 150")

    # 检查每根 bar 字段
    for i, b in enumerate(bars):
        for k in ("idx", "datetime", "open", "high", "low", "close"):
            if k not in b:
                bugs_local.append(f"bar[{i}] 缺少字段 {k}")
                break
        # OHLC 合理性
        if b.get("high", 0) < b.get("low", 0):
            bugs_local.append(f"bar[{i}] high({b['high']}) < low({b['low']})")
        if b.get("open", 0) > b.get("high", 0) or b.get("open", 0) < b.get("low", 0):
            bugs_local.append(f"bar[{i}] open({b['open']}) 超出 high-low 范围")
        if b.get("close", 0) > b.get("high", 0) or b.get("close", 0) < b.get("low", 0):
            bugs_local.append(f"bar[{i}] close({b['close']}) 超出 high-low 范围")
        # time 字段
        if "time" in b and b["time"] == 0:
            bugs_local.append(f"bar[{i}] time=0 (datetime={b.get('datetime')})")

    # 3. 笔检查
    for i, p in enumerate(pens):
        for k in ("start_idx", "end_idx", "start_price", "end_price", "direction"):
            if k not in p:
                bugs_local.append(f"pen[{i}] 缺少字段 {k}")
                break
        if p.get("start_idx", -1) >= p.get("end_idx", -1):
            bugs_local.append(f"pen[{i}] start_idx({p['start_idx']}) >= end_idx({p['end_idx']})")
        if p.get("direction") not in ("up", "down"):
            bugs_local.append(f"pen[{i}] direction={p.get('direction')} 无效")
        # 检查笔端点是否在 K 线范围内
        si = p.get("start_idx", -1)
        ei = p.get("end_idx", -1)
        if si >= 0 and si >= len(bars):
            bugs_local.append(f"pen[{i}] start_idx({si}) 超出 K 线范围({len(bars)})")
        if ei >= 0 and ei >= len(bars):
            bugs_local.append(f"pen[{i}] end_idx({ei}) 超出 K 线范围({len(bars)})")

    # 4. 线段检查
    for i, sg in enumerate(segments):
        for k in ("from_idx", "to_idx", "from_price", "to_price"):
            if k not in sg:
                bugs_local.append(f"segment[{i}] 缺少字段 {k}")
                break
        if sg.get("from_idx", -1) >= sg.get("to_idx", -1):
            bugs_local.append(f"segment[{i}] from_idx >= to_idx")

    # 5. 中枢检查
    for i, zs in enumerate(zhongshu):
        for k in ("x_left", "x_right", "y_bottom", "y_top"):
            if k not in zs:
                bugs_local.append(f"zhongshu[{i}] 缺少字段 {k}")
                break
        if zs.get("y_bottom", 0) >= zs.get("y_top", 0):
            bugs_local.append(f"zhongshu[{i}] y_bottom({zs['y_bottom']}) >= y_top({zs['y_top']})")
        if zs.get("x_left", -1) >= zs.get("x_right", -1):
            bugs_local.append(f"zhongshu[{i}] x_left >= x_right")

    # 6. 信号检查
    sig_count = sum(1 for b in bars if b.get("signals"))
    if sig_count == 0:
        bugs_local.append("无任何信号标记")

    # 7. 启动模拟会话
    start = api("POST", "/api/session/start", {
        "code": code, "period": period, "init_asset": 100000.0,
        "fees_on": True, "window_size": 100, "tplus1": True
    })
    if "__error__" in start:
        bugs_local.append(f"启动会话失败: {start['__msg__']}")
        return bugs_local

    sid = start.get("session_id")
    if not sid:
        bugs_local.append("session_id 为空")
        return bugs_local

    # 8. 检查初始状态
    st = start
    if st.get("status") != "PLAYING":
        bugs_local.append(f"初始状态不是 PLAYING: {st.get('status')}")
    if st.get("cash") != 100000.0:
        bugs_local.append(f"初始现金不是 100000: {st.get('cash')}")
    if st.get("position") != 0:
        bugs_local.append(f"初始持仓不为 0: {st.get('position')}")
    if st.get("total_bars") != 100:
        bugs_local.append(f"total_bars 不是 100: {st.get('total_bars')}")
    if st.get("hist_n") != 50:
        bugs_local.append(f"hist_n 不是 50: {st.get('hist_n')}")
    if st.get("cur_pos") != 49:
        bugs_local.append(f"cur_pos 不是 49(观察期结束): {st.get('cur_pos')}")

    # 9. 观察期内不能交易
    buy_obs = api("POST", "/api/session/buy", {"session_id": sid, "ratio": 0.5})
    if buy_obs.get("ok"):
        bugs_local.append("观察期内允许买入(应拒绝)")

    # 10. 推进到可交易
    adv = api("POST", "/api/session/advance", {"session_id": sid, "n": 1})
    if "__error__" in adv:
        bugs_local.append(f"推进失败: {adv['__msg__']}")
        return bugs_local

    # 11. 买入测试
    buy = api("POST", "/api/session/buy", {"session_id": sid, "ratio": 0.5})
    if not buy.get("ok"):
        bugs_local.append(f"买入失败: {buy.get('msg')}")
    else:
        if buy.get("qty", 0) % 100 != 0:
            bugs_local.append(f"买入数量不是100整数倍: {buy.get('qty')}")
        if buy.get("qty", 0) <= 0:
            bugs_local.append(f"买入数量<=0: {buy.get('qty')}")

    # 12. T+1 测试: 当日买入不能卖
    sell_same = api("POST", "/api/session/sell", {"session_id": sid, "ratio": 1.0})
    if sell_same.get("ok"):
        bugs_local.append("T+1: 当日买入允许卖出(应拒绝)")

    # 13. 推进一天再卖
    api("POST", "/api/session/advance", {"session_id": sid, "n": 5})
    sell = api("POST", "/api/session/sell", {"session_id": sid, "ratio": 1.0})
    if not sell.get("ok"):
        bugs_local.append(f"卖出失败: {sell.get('msg')}")
    else:
        if sell.get("position", -1) != 0:
            bugs_local.append(f"全仓卖出后持仓不为0: {sell.get('position')}")

    # 14. 再买入再推进到结束
    api("POST", "/api/session/buy", {"session_id": sid, "ratio": 0.3})
    api("POST", "/api/session/advance", {"session_id": sid, "n": 50})

    # 15. 检查结果
    result = api("GET", f"/api/session/{sid}/result")
    if "__error__" in result:
        bugs_local.append(f"获取结果失败: {result['__msg__']}")
    else:
        if result.get("init_asset") != 100000.0:
            bugs_local.append(f"init_asset 异常: {result.get('init_asset')}")
        if result.get("final_asset", 0) <= 0:
            bugs_local.append(f"final_asset <= 0: {result.get('final_asset')}")
        # 检查交易记录
        st2 = api("GET", f"/api/session/{sid}")
        if "__error__" not in st2:
            trades = st2.get("trades", [])
            if len(trades) < 2:
                bugs_local.append(f"交易记录过少: {len(trades)}")

    # 16. 检查 window 数据完整性
    st3 = api("GET", f"/api/session/{sid}")
    if "__error__" not in st3:
        window = st3.get("window", [])
        all_bars = st3.get("all_bars", [])
        if len(window) != st3.get("total_bars"):
            bugs_local.append(f"window 长度({len(window)}) != total_bars({st3.get('total_bars')})")
        if len(all_bars) != st3.get("total_bars"):
            bugs_local.append(f"all_bars 长度({len(all_bars)}) != total_bars({st3.get('total_bars')})")
        # 检查 window 中每根 bar 的 time 字段
        for i, b in enumerate(window):
            if "time" not in b:
                bugs_local.append(f"window[{i}] 缺少 time 字段")
                break
            if b["time"] == 0:
                bugs_local.append(f"window[{i}] time=0 (datetime={b.get('datetime')})")
                break

    return bugs_local


def main():
    print("=" * 60)
    print("8769 项目全面测试 — 19 标的 × 5 次 = 95 次")
    print("=" * 60)

    # 获取标的列表
    syms = api("GET", "/api/symbols")
    if "__error__" in syms:
        print(f"❌ 无法获取标的列表: {syms['__msg__']}")
        sys.exit(1)

    symbols = syms.get("symbols", [])
    print(f"共 {len(symbols)} 个标的\n")

    total_bugs = 0
    for sym in symbols:
        code = sym["code"]
        period = sym["period"]
        bars = sym["bars"]
        print(f"\n{'─' * 50}")
        print(f"📊 {code}({period}) — {bars} 根K线")

        for run in range(1, 6):
            bugs = test_symbol(code, period, run)
            for b in bugs:
                BUGS.append({"symbol": code, "run": run, "msg": b})
            if bugs:
                total_bugs += len(bugs)
                for b in bugs:
                    print(f"  ❌ Run#{run}: {b}")
            else:
                print(f"  ✅ Run#{run}: 通过")

    print(f"\n{'=' * 60}")
    print(f"📋 测试完成: {len(symbols)} 标的 × 5 次 = {len(symbols)*5} 次")
    print(f"🐛 发现 BUG: {total_bugs} 个")
    print(f"{'=' * 60}")

    if total_bugs > 0:
        print("\n🔍 BUG 汇总:")
        for i, b in enumerate(BUGS, 1):
            print(f"  {i}. [{b['symbol']}] Run#{b['run']}: {b['msg']}")

    # 保存详细报告
    report = {
        "total_symbols": len(symbols),
        "total_runs": len(symbols) * 5,
        "total_bugs": total_bugs,
        "bugs": BUGS,
        "symbols": [{"code": s["code"], "period": s["period"], "bars": s["bars"]} for s in symbols]
    }
    with open("/root/Downloads/chan-trading/test_report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 详细报告已保存: /root/Downloads/chan-trading/test_report.json")

    return total_bugs

if __name__ == "__main__":
    sys.exit(main())