"""
缠论模拟交易 App — FastAPI 主入口
启动: /root/.venv_tqsdk/bin/python3 app.py  (端口 8770)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, HTTPException, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.data_loader import load_symbols, load_symbol_full, _dt_to_ts
from engine.simulator import manager
from engine import db
from engine.csv_parser import parse_ohl_csv, parse_step25_csv, merge_bars, _extract_pens

# CSV 会话存储: session_id -> {'bars': [], 'symbolData': {...}}
_csv_sessions = {}


def _persist_uploads(code: str, period: str, ohl_text: str, step25_text: str,
                     raw_segments: list, raw_zhongshu: list):
    """2026-08-29 新增: 上传会话创建成功后自动落盘到 data/
    命名与 data_loader 扁平化约定一致: data/{code}_{period}_{kline|step25|segments|zhongshu}.csv
    - kline/step25: 保存原始上传文本 (load_kline 兼容 id/idx 列)
    - segments/zhongshu: 保存推导结果, 非空才写 (避免空推导覆盖历史有效文件)
    - 覆盖写同名文件; 任何失败仅告警, 不影响会话
    """
    import csv as _csv
    import logging
    logger = logging.getLogger("uvicorn.error")
    try:
        code = (code or "").strip()
        if not code:
            logger.warning("[persist] code 为空, 跳过落盘")
            return
        # 文件名清洗, 防路径注入
        bad = '\\/:*?"<>|'
        safe_code = "".join(ch for ch in code if ch not in bad).strip()
        safe_period = "".join(ch for ch in (period or "").strip() if ch not in bad).strip() or "unknown"
        if not safe_code:
            logger.warning("[persist] code 清洗后为空, 跳过落盘")
            return
        data_dir = BASE_DIR / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        if ohl_text:
            (data_dir / f"{safe_code}_{safe_period}_kline.csv").write_text(ohl_text, encoding="utf-8")
            logger.info(f"[persist] kline 已落盘: {safe_code}_{safe_period}_kline.csv")
        if step25_text:
            (data_dir / f"{safe_code}_{safe_period}_step25.csv").write_text(step25_text, encoding="utf-8")
            logger.info(f"[persist] step25 已落盘: {safe_code}_{safe_period}_step25.csv")

        if raw_segments:
            p = data_dir / f"{safe_code}_{safe_period}_segments.csv"
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(f, fieldnames=["from_idx", "from_price", "to_idx", "to_price", "label"])
                w.writeheader()
                w.writerows(raw_segments)
            logger.info(f"[persist] segments 已落盘: {p.name} ({len(raw_segments)} 条)")
        if raw_zhongshu:
            p = data_dir / f"{safe_code}_{safe_period}_zhongshu.csv"
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(f, fieldnames=["zs_id", "seg_type", "x_left", "x_right", "y_bottom", "y_top"])
                w.writeheader()
                w.writerows(raw_zhongshu)
            logger.info(f"[persist] zhongshu 已落盘: {p.name} ({len(raw_zhongshu)} 条)")
    except Exception as e:
        logger.warning(f"[persist] 落盘失败(不影响会话): {e}")

def _csv_state(eng):
    """统一会话状态出口: CSV 会话附带绘图数据
    - 模拟中(PLAYING): 返回引擎真实状态(window切片), 不泄露未来绘图数据
    - 复盘(FINISHED): 与普通逻辑一致, 只返回引擎 window(模拟窗口K线数);
      绘图(笔/线段/中枢)仍来自 _symbolData 全量, 但端点在窗口外由前端跳过渲染, 故只显示窗口内绘图
    """
    st = eng.state()
    sd = _csv_sessions.get(eng.session_id)
    if sd:
        st["_symbolData"] = sd
        st["is_csv"] = True
    return st

PORT = 8769
app = FastAPI(title="大富翁之生财有道", version="0.2.0")

# 初始化数据库
db.init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ---------- 请求模型 ----------
class StartReq(BaseModel):
    code: str = "002475"
    period: str = ""
    init_asset: float = 1000000.0
    fees_on: bool = True
    window_size: int = 150
    tplus1: bool = True

class TradeReq(BaseModel):
    session_id: str
    ratio: float = 1.0
    qty: int = 0

class AdvanceReq(BaseModel):
    session_id: str
    n: int = 1

class SaveResultReq(BaseModel):
    session_id: str
    nickname: str = "Guest"


# 静态文件 (需在路由前挂载)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ---------- 静态页面 ----------
@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ---------- PWA (2026-08-30) ----------
# sw.js 必须挂在根路径才能获得全站作用域(控制 / 与 /api 之外的页面导航); manifest 同理供 / 引用
@app.get("/sw.js")
def sw_js():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/manifest.json")
def manifest_json():
    return FileResponse(BASE_DIR / "static" / "manifest.json", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ---------- 数据 API ----------
@app.get("/api/symbols")
def api_symbols():
    return {"symbols": load_symbols()}


@app.get("/api/data/{code:path}")
def api_data(code: str, period: str = ""):
    data = load_symbol_full(code, period)
    if not data:
        raise HTTPException(404, f"Symbol {code} has no data")
    # 精简返回（前端只用部分字段）
    return {
        "code": data["code"],
        "period": data.get("period", ""),
        "total": data["total"],
        "bars": [{k: b[k] for k in ("idx", "datetime", "time", "open", "high", "low", "close", "signals", "in_zhongshu", "summacd")} for b in data["bars"]],
        "pens": data["pens"],
        "segments": data.get("segments", []),
        "zhongshu": data.get("zhongshu", []),
    }


# ---------- 常规会话 ----------
@app.post("/api/session/start")
def api_start(req: StartReq):
    # 校验: 标的K线数 ≥ 模拟K线数 + hist_n(50)
    data = load_symbol_full(req.code, req.period)
    if not data:
        raise HTTPException(400, f"Symbol {req.code} has no data")
    total_bars = data["total"]
    if total_bars < req.window_size + 50:
        raise HTTPException(400,
            f"Insufficient bars: {total_bars} available, need ≥ {req.window_size+50}")
    eng = manager.create(req.code, period=req.period, init_asset=req.init_asset,
                         fees_on=req.fees_on, window_size=req.window_size,
                         # 2026-08-29 修复: 尊重开关值, 不再按周期强制关闭(导入的 3m/30s 数据 T+1 开关此前无效)
                         tplus1=req.tplus1)
    if not eng:
        raise HTTPException(400, f"Symbol {req.code} — insufficient data")
    return eng.state()


# ---------- CSV 上传会话 ----------
@app.post("/api/csv-session/start")
async def api_csv_start(ohl: UploadFile, step25: UploadFile = None,
                        init_asset: float = Form(1000000.0), fees_on: str = Form("true"),
                        window_size: int = Form(150), period: str = Form("30min"),
                        tplus1: str = Form("true"), code: str = Form("")):
    """上传 OHLC CSV(+可选 step25 CSV)创建模拟会话
    若 code 非空，从本地 data/{code}_{period}_segments.csv + data/{code}_{period}_zhongshu.csv 补全线段/中枢
    会话创建成功后自动落盘到 data/ (覆盖写): {code}_{period}_{kline|step25|segments|zhongshu}.csv
    """
    try:
        ohl_text = (await ohl.read()).decode('utf-8-sig')
        ohl_rows = parse_ohl_csv(ohl_text)
    except Exception as e:
        raise HTTPException(400, f"OHLC CSV parse failed: {e}")

    step25_rows = []
    if step25:
        try:
            step25_text = (await step25.read()).decode('utf-8-sig')
            step25_rows = parse_step25_csv(step25_text)
        except Exception as e:
            raise HTTPException(400, f"Chan theory CSV parse failed: {e}")

    bars = merge_bars(ohl_rows, step25_rows if step25_rows else None)
    if len(bars) < window_size + 50:
        raise HTTPException(400, f"Insufficient bars: {len(bars)} available, need ≥ {window_size+50}")

    # 2026-08-30 改进: code 为空时不再从文件名提取, 改为自动命名 kline_时间戳,
    # 避免用户改文件名; 同名覆盖写按秒区分, 天然不冲突
    code = (code or "").strip()
    if not code:
        code = f"kline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 2026-08-29 修复: FastAPI 表单 bool 会把任意字符串当 True, 这里手动解析
    tplus1 = str(tplus1).strip().lower() in ("1", "true", "yes", "on", "y")
    fees_on = str(fees_on).strip().lower() in ("1", "true", "yes", "on", "y")

    eng = manager.create_from_bars(
        code="CSV", bars=bars, period=period, init_asset=init_asset,
        fees_on=fees_on, window_size=window_size,
        # 2026-08-29 修复: 尊重开关值, 不再按周期强制关闭(导入的 3m/30s 数据 T+1 开关此前无效)
        tplus1=tplus1,
    )
    if not eng:
        raise HTTPException(400, "Not enough data to create session")

    # 提取笔并补 time (方案A: 前端按 time 匹配)
    pens = _extract_pens(bars)
    idx2dt = {b["idx"]: b["datetime"] for b in bars}
    for p in pens:
        fd = idx2dt.get(p["start_idx"])
        td = idx2dt.get(p["end_idx"])
        p["start_time"] = _dt_to_ts(fd) if fd else 0
        p["end_time"] = _dt_to_ts(td) if td else 0

    # 从上传的 step25 自动推导线段/中枢 (用户仅需 kline + step25 两个文件)
    raw_segments: list = []
    raw_zhongshu: list = []
    try:
        from engine.csv_parser import derive_segments, derive_zhongshu
        raw_segments = derive_segments(bars)
        raw_zhongshu = derive_zhongshu(bars)
    except Exception:
        raw_segments = []
        raw_zhongshu = []

    # 推导为空且 code 非空时，回退本地 data/ 文件补全
    if not raw_segments and not raw_zhongshu and code:
        try:
            from engine.data_loader import load_segments as _load_seg, load_zhongshu as _load_zhu
            raw_segments = _load_seg(code, period)
            raw_zhongshu = _load_zhu(code, period)
        except Exception:
            raw_segments = []
            raw_zhongshu = []

    # 2026-08-29 新增: 上传数据自动落盘到 data/ (覆盖写, 失败不影响会话)
    _persist_uploads(code, period, ohl_text, step25_text if step25_rows else "", raw_segments, raw_zhongshu)

    # 统一 idx→time 映射 (本地回退数据同样补 time)
    segments = []
    for sg in raw_segments:
        fd = idx2dt.get(sg["from_idx"])
        td = idx2dt.get(sg["to_idx"])
        if fd is None or td is None:
            continue
        segments.append({**sg, "from_time": _dt_to_ts(fd), "to_time": _dt_to_ts(td)})
    zhongshu = []
    for zs in raw_zhongshu:
        ld = idx2dt.get(zs["x_left"])
        rd = idx2dt.get(zs["x_right"])
        if ld is None or rd is None:
            continue
        zhongshu.append({**zs, "x_left_time": _dt_to_ts(ld), "x_right_time": _dt_to_ts(rd)})

    symbol_data = {
        "code": code or "CSV",
        "period": period,
        "bars": [
            {k: b[k] for k in ("idx", "datetime", "time", "open", "high", "low", "close", "signals", "in_zhongshu", "summacd")}
            for b in bars
        ],
        "pens": pens,
        "segments": segments,
        "zhongshu": zhongshu,
    }
    _csv_sessions[eng.session_id] = symbol_data
    # 返回引擎真实状态(PLAYING): 模拟中不显示缠论绘图, 走完后由前端触发复盘
    return _csv_state(eng)


@app.get("/api/session/{session_id}")
def api_state(session_id: str):
    eng = manager.get(session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    # CSV 会话: 真实状态 + 绘图数据, 不再强制 FINISHED (否则恢复会话即泄露全量绘图)
    if session_id in _csv_sessions:
        return _csv_state(eng)
    res = eng.state()
    # 2026-08-14 全量 bars：chart 用全量 K 线渲染（否则笔/线段/中枢基于 symbolData.bars
    # 定位，K 线只画 window 切片 → 时间轴错位）
    if res.get("code") and res.get("period"):
        try:
            sd = load_symbol_full(res["code"], res["period"])
            if sd and sd.get("bars"):
                res["_symbolData"] = {
                    "code": sd["code"],
                    "period": sd.get("period", ""),
                    "total": sd["total"],
                    "bars": [{k: b[k] for k in ("idx","datetime","time","open","high","low","close","signals","in_zhongshu","summacd")} for b in sd["bars"]],
                    "pens": sd.get("pens", []),
                    "segments": sd.get("segments", []),
                    "zhongshu": sd.get("zhongshu", []),
                }
                # 覆盖 all_bars/window：让前端 renderState 的 data.bars 拿到全量
                res["all_bars"] = res["_symbolData"]["bars"]
                res["window"] = res["_symbolData"]["bars"]
                res["total_bars"] = res["_symbolData"]["total"]
                res["is_csv"] = False
        except Exception:
            pass
    return res


@app.post("/api/session/advance")
def api_advance(req: AdvanceReq):
    eng = manager.get(req.session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    eng.advance(req.n)
    return _csv_state(eng)


@app.post("/api/session/buy")
def api_buy(req: TradeReq):
    eng = manager.get(req.session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    r = eng.buy(ratio=req.ratio, qty=req.qty or None)
    return {**r, "state": _csv_state(eng)}


@app.post("/api/session/sell")
def api_sell(req: TradeReq):
    eng = manager.get(req.session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    r = eng.sell(ratio=req.ratio, qty=req.qty or None)
    return {**r, "state": _csv_state(eng)}


@app.get("/api/session/{session_id}/result")
def api_result(session_id: str):
    eng = manager.get(session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    return eng.result()


# ---------- 排行榜 API ----------
@app.post("/api/session/save_result")
def api_save_result(req: SaveResultReq):
    eng = manager.get(req.session_id)
    if not eng:
        raise HTTPException(404, "Session not found")
    res = eng.result()
    uid = db.get_or_create_user(req.nickname.strip() or "Guest")
    sid = db.save_result(
        uid, eng.code if hasattr(eng, "code") else "?",
        res["init_asset"], res["final_asset"], res["total_profit"],
        res["profit_rate"], res["trade_count"], res["win_count"],
        period=eng.period if hasattr(eng, "period") else "",
    )
    return {"ok": True, "saved": sid, "user_id": uid, "stats": db.user_stats(uid)}


@app.get("/api/leaderboard")
def api_leaderboard(limit: int = 20, offset: int = 0, period: str = "", code: str = "", rank_type: str = "rate"):
    board, total = db.leaderboard(limit=limit, offset=offset, period=period, code=code, rank_type=rank_type)
    return {"board": board, "total": total}


@app.get("/api/user/{nickname}")
def api_user(nickname: str):
    uid = db.get_or_create_user(nickname.strip() or "Guest")
    user = db.find_user_by_nickname(nickname.strip() or "Guest")
    return {"stats": db.user_stats(uid),
            "sessions": db.user_sessions(uid),
            "user": user}


@app.put("/api/user/update_nickname")
def api_update_nickname(req: dict):
    """修改当前用户昵称。前端用 localStorage 的 user_id 调用"""
    try:
        uid = req.get("user_id", "").strip()
        new_nick = (req.get("nickname") or "").strip()
        if not uid or not new_nick:
            raise HTTPException(400, "user_id and nickname required")
        old_nick = db.find_user_by_id(uid)
        if not old_nick:
            raise HTTPException(404, "user not found")
        new_uid = db.update_user_nickname(uid, new_nick)
        return {"ok": True, "user_id": new_uid, "old_nickname": old_nick["nickname"], "nickname": new_nick}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- 启动 ----------
if __name__ == "__main__":
    import uvicorn
    import threading
    # 2026-08-31 HTTPS 支持: config/cert.pem + config/key.pem 存在则启用 (iPad PWA 离线需要 SW, SW 仅 https 生效)
    # 同时在 8768 开 HTTP 明文通道: iOS 不允许从未受信的 https 站点下载描述文件, 证书首次下载走 HTTP
    ssl_args = {}
    cert_file = BASE_DIR / "config" / "cert.pem"
    key_file = BASE_DIR / "config" / "key.pem"
    http_port = PORT - 1  # 8768
    if cert_file.exists() and key_file.exists():
        ssl_args = {"ssl_certfile": str(cert_file), "ssl_keyfile": str(key_file)}
        http_cfg = uvicorn.Config(app, host="0.0.0.0", port=http_port, log_level="warning")
        http_srv = uvicorn.Server(http_cfg)
        threading.Thread(target=http_srv.run, daemon=True).start()
        print(f"🚀 缠论模拟交易 App  https://0.0.0.0:{PORT} (自签名) + http://0.0.0.0:{http_port} (证书下载/兜底)")
    else:
        print(f"🚀 缠论模拟交易 App  http://0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning", **ssl_args)
