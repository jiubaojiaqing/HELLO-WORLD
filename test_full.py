"""
chan-trading 全面测试脚本
测试所有 API 端点、数据完整性、模拟流程、边界条件
"""
import json
import urllib.request
import urllib.error
import urllib.parse
import time
import sys

BASE = "http://127.0.0.1:8769"
results = {"pass": 0, "fail": 0, "errors": []}

def api_get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        r = urllib.request.urlopen(url, timeout=15)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()) if e.fp else {}
    except Exception as e:
        return 0, {"error": str(e)}

def api_post(path, data):
    url = BASE + path
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()) if e.fp else {}
    except Exception as e:
        return 0, {"error": str(e)}

def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        results["fail"] += 1
        results["errors"].append(f"{name}: {detail}")
        print(f"  [FAIL] {name} — {detail}")

def check_data_integrity(bars, label):
    """检查 bars 数据完整性"""
    ohlc_violations = 0
    missing_time = 0
    missing_fields = 0
    for b in bars:
        o, h, l, c = b.get("open", 0), b.get("high", 0), b.get("low", 0), b.get("close", 0)
        # OHLC 范围检查
        if o < l - 0.001 or o > h + 0.001 or c < l - 0.001 or c > h + 0.001:
            ohlc_violations += 1
        # high < low 检查
        if h < l:
            ohlc_violations += 1
        # time 字段
        if not b.get("time"):
            missing_time += 1
        # 必要字段
        for f in ("idx", "datetime", "open", "high", "low", "close"):
            if f not in b:
                missing_fields += 1
                break
    check(f"{label}: OHLC 范围", ohlc_violations == 0, f"{ohlc_violations} violations")
    check(f"{label}: time 字段", missing_time == 0, f"{missing_time} missing")
    check(f"{label}: 必要字段", missing_fields == 0, f"{missing_fields} missing")
    return ohlc_violations, missing_time, missing_fields

print("=" * 60)
print("1. 测试 /api/symbols")
print("=" * 60)
status, data = api_get("/api/symbols")
check("HTTP 200", status == 200, f"got {status}")
symbols = data.get("symbols", [])
check("symbols 非空", len(symbols) > 0, "empty")
print(f"  共 {len(symbols)} 个标的")
for s in symbols[:5]:
    print(f"    {s['code']}({s['period']}): {s['bars']} bars")

print()
print("=" * 60)
print("2. 测试 /api/data — 所有标的")
print("=" * 60)
all_codes = set()
for s in symbols:
    key = f"{s['code']}_{s['period']}"
    all_codes.add(key)

for s in symbols:
    code, period = s["code"], s["period"]
    status, data = api_get(f"/api/data/{code}", {"period": period})
    label = f"{code}({period})"
    check(f"{label}: HTTP 200", status == 200, f"got {status}")
    if status != 200:
        continue
    bars = data.get("bars", [])
    check(f"{label}: bars 非空", len(bars) > 0, "empty")
    check(f"{label}: total 匹配", data.get("total") == len(bars), f"total={data.get('total')} bars={len(bars)}")
    check_data_integrity(bars, label)
    # 检查 pens/segments/zhongshu
    check(f"{label}: pens 存在", "pens" in data, "missing")
    check(f"{label}: segments 存在", "segments" in data, "missing")
    check(f"{label}: zhongshu 存在", "zhongshu" in data, "missing")
    # 检查 summacd
    sm_count = sum(1 for b in bars if b.get("summacd"))
    print(f"    summacd 非空: {sm_count}/{len(bars)}")

print()
print("=" * 60)
print("3. 测试 /api/session/start — 模拟会话")
print("=" * 60)
# 测试不同标的
test_symbols = [
    ("002475", "30min"),
    ("002475", "day"),
    ("300502", "30min"),
    ("601899", "day"),
]
for code, period in test_symbols:
    status, data = api_post("/api/session/start", {
        "code": code, "period": period, "init_asset": 100000.0,
        "fees_on": True, "window_size": 150, "tplus1": True
    })
    label = f"{code}({period})"
    check(f"{label}: start HTTP 200", status == 200, f"got {status}")
    if status != 200:
        continue
    check(f"{label}: session_id 存在", "session_id" in data, "missing")
    check(f"{label}: status 存在", "status" in data, "missing")
    check(f"{label}: cur_pos 存在", "cur_pos" in data, "missing")
    check(f"{label}: total_bars 存在", "total_bars" in data, "missing")
    check(f"{label}: window 存在", "window" in data, "missing")
    check(f"{label}: all_bars 存在", "all_bars" in data, "missing")
    check(f"{label}: _symbolData 存在", "_symbolData" in data, "missing")
    # 检查初始资金
    check(f"{label}: init_asset=100000", data.get("init_asset") == 100000.0, f"got {data.get('init_asset')}")
    # 检查 window 数据完整性
    window = data.get("window", [])
    check(f"{label}: window 非空", len(window) > 0, "empty")
    check_data_integrity(window, f"{label}-window")

print()
print("=" * 60)
print("4. 测试模拟流程 — advance/buy/sell/result")
print("=" * 60)
# 创建一个会话
status, data = api_post("/api/session/start", {
    "code": "002475", "period": "30min", "init_asset": 100000.0,
    "fees_on": True, "window_size": 150, "tplus1": True
})
if status == 200:
    sid = data["session_id"]
    print(f"  会话: {sid}")
    
    # 测试 advance
    status, data = api_post("/api/session/advance", {"session_id": sid, "n": 5})
    check("advance HTTP 200", status == 200, f"got {status}")
    if status == 200:
        check("advance: cur_pos 增加", data.get("cur_pos", 0) > 0, f"cur_pos={data.get('cur_pos')}")
        check("advance: status 存在", "status" in data, "missing")
    
    # 测试 buy
    status, data = api_post("/api/session/buy", {"session_id": sid, "ratio": 0.5})
    check("buy HTTP 200", status == 200, f"got {status}")
    if status == 200:
        check("buy: ok 字段", "ok" in data, "missing")
        check("buy: state 存在", "state" in data, "missing")
        if data.get("ok"):
            check("buy: 成功", True)
            # 检查持仓
            state = data.get("state", {})
            pos = state.get("position", 0)
            if isinstance(pos, dict):
                vol = pos.get("volume", 0)
            else:
                vol = pos
            check("buy: 有持仓", vol > 0, f"volume={vol}")
        else:
            print(f"    buy 失败: {data.get('msg', 'unknown')}")
    
    # 测试 sell
    status, data = api_post("/api/session/sell", {"session_id": sid, "ratio": 1.0})
    check("sell HTTP 200", status == 200, f"got {status}")
    if status == 200:
        check("sell: ok 字段", "ok" in data, "missing")
        check("sell: state 存在", "state" in data, "missing")
    
    # 测试 result
    status, data = api_get(f"/api/session/{sid}/result")
    check("result HTTP 200", status == 200, f"got {status}")
    if status == 200:
        check("result: init_asset 存在", "init_asset" in data, "missing")
        check("result: final_asset 存在", "final_asset" in data, "missing")
        check("result: total_profit 存在", "total_profit" in data, "missing")
        check("result: profit_rate 存在", "profit_rate" in data, "missing")
        check("result: trade_count 存在", "trade_count" in data, "missing")
        check("result: win_count 存在", "win_count" in data, "missing")
        print(f"    结果: 初始={data.get('init_asset')}, 最终={data.get('final_asset')}, "
              f"盈亏={data.get('total_profit')}, 收益率={data.get('profit_rate')}%, "
              f"交易={data.get('trade_count')}, 胜={data.get('win_count')}")

print()
print("=" * 60)
print("5. 测试边界条件")
print("=" * 60)
# 不存在的标的
status, data = api_get("/api/data/999999", {"period": "30min"})
check("不存在标的: 404", status == 404, f"got {status}")

# 不存在的会话
status, data = api_get("/api/session/nonexistent")
check("不存在会话: 404", status == 404, f"got {status}")

# 不存在的会话 advance
status, data = api_post("/api/session/advance", {"session_id": "nonexistent", "n": 1})
check("不存在会话 advance: 404", status == 404, f"got {status}")

# 不存在的会话 buy
status, data = api_post("/api/session/buy", {"session_id": "nonexistent", "ratio": 1.0})
check("不存在会话 buy: 404", status == 404, f"got {status}")

# 不存在的会话 sell
status, data = api_post("/api/session/sell", {"session_id": "nonexistent", "ratio": 1.0})
check("不存在会话 sell: 404", status == 404, f"got {status}")

# 不存在的会话 result
status, data = api_get("/api/session/nonexistent/result")
check("不存在会话 result: 404", status == 404, f"got {status}")

# 数据不足
status, data = api_post("/api/session/start", {
    "code": "002475", "period": "30min", "init_asset": 100000.0,
    "fees_on": True, "window_size": 99999, "tplus1": True
})
check("数据不足: 400", status == 400, f"got {status}")

print()
print("=" * 60)
print("6. 测试排行榜 API")
print("=" * 60)
status, data = api_get("/api/leaderboard")
check("leaderboard HTTP 200", status == 200, f"got {status}")
if status == 200:
    check("leaderboard: board 存在", "board" in data, "missing")
    check("leaderboard: total 存在", "total" in data, "missing")
    print(f"    排行榜: {data.get('total', 0)} 条记录")

# 带参数
status, data = api_get("/api/leaderboard", {"limit": 5, "offset": 0, "rank_type": "rate"})
check("leaderboard 带参数: 200", status == 200, f"got {status}")

print()
print("=" * 60)
print("7. 测试用户 API")
print("=" * 60)
status, data = api_get("/api/user/TestUser")
check("user HTTP 200", status == 200, f"got {status}")
if status == 200:
    check("user: stats 存在", "stats" in data, "missing")
    check("user: sessions 存在", "sessions" in data, "missing")
    check("user: user 存在", "user" in data, "missing")

print()
print("=" * 60)
print("8. 测试 save_result")
print("=" * 60)
# 先创建一个会话并完成
status, data = api_post("/api/session/start", {
    "code": "002475", "period": "30min", "init_asset": 100000.0,
    "fees_on": True, "window_size": 150, "tplus1": True
})
if status == 200:
    sid = data["session_id"]
    # advance 到结束
    for _ in range(50):
        status, data = api_post("/api/session/advance", {"session_id": sid, "n": 5})
        if status != 200:
            break
        if data.get("is_finished"):
            break
    # save result
    status, data = api_post("/api/session/save_result", {"session_id": sid, "nickname": "TestUser"})
    check("save_result HTTP 200", status == 200, f"got {status}")
    if status == 200:
        check("save_result: ok=True", data.get("ok") == True, f"got {data.get('ok')}")
        check("save_result: saved 存在", "saved" in data, "missing")
        check("save_result: user_id 存在", "user_id" in data, "missing")
        check("save_result: stats 存在", "stats" in data, "missing")

print()
print("=" * 60)
print("9. 测试 CSV 上传会话")
print("=" * 60)
# 读取一个 CSV 文件测试上传
import os
csv_path = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\data\002475_30min_kline.csv"
if os.path.exists(csv_path):
    with open(csv_path, "rb") as f:
        csv_content = f.read()
    # 构造 multipart 请求
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="ohl"; filename="kline.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + csv_content + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="init_asset"\r\n\r\n'
        f"100000\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="fees_on"\r\n\r\n'
        f"true\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="window_size"\r\n\r\n'
        f"150\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="period"\r\n\r\n'
        f"30min\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="tplus1"\r\n\r\n'
        f"true\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="code"\r\n\r\n'
        f"002475\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        BASE + "/api/csv-session/start",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        r = urllib.request.urlopen(req, timeout=15)
        status = r.status
        data = json.loads(r.read())
        check("CSV 上传 HTTP 200", status == 200, f"got {status}")
        if status == 200:
            check("CSV: session_id 存在", "session_id" in data, "missing")
            check("CSV: is_csv=True", data.get("is_csv") == True, f"got {data.get('is_csv')}")
            check("CSV: is_finished=True", data.get("is_finished") == True, f"got {data.get('is_finished')}")
            check("CSV: _symbolData 存在", "_symbolData" in data, "missing")
            check("CSV: all_bars 存在", "all_bars" in data, "missing")
            bars = data.get("all_bars", [])
            check(f"CSV: bars 非空", len(bars) > 0, "empty")
            check_data_integrity(bars, "CSV")
    except urllib.error.HTTPError as e:
        status = e.code
        data = json.loads(e.read()) if e.fp else {}
        check("CSV 上传 HTTP 200", status == 200, f"got {status}: {data}")
    except Exception as e:
        check("CSV 上传", False, str(e))
else:
    print("  [SKIP] CSV 文件不存在")

print()
print("=" * 60)
print("10. 测试 update_nickname")
print("=" * 60)
status, data = api_get("/api/user/TestUser")
if status == 200:
    uid = data.get("user", {}).get("id", "")
    if uid:
        req = urllib.request.Request(
            BASE + "/api/user/update_nickname",
            data=json.dumps({"user_id": str(uid), "nickname": "NewName"}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        try:
            r = urllib.request.urlopen(req, timeout=10)
            status = r.status
            data = json.loads(r.read())
            check("update_nickname HTTP 200", status == 200, f"got {status}")
        except urllib.error.HTTPError as e:
            status = e.code
            data = json.loads(e.read()) if e.fp else {}
            check("update_nickname HTTP 200", status == 200, f"got {status}: {data}")
        except Exception as e:
            check("update_nickname", False, str(e))

print()
print("=" * 60)
print(f"测试完成: {results['pass']} 通过, {results['fail']} 失败")
print("=" * 60)
if results["errors"]:
    print("\n失败详情:")
    for e in results["errors"]:
        print(f"  - {e}")
