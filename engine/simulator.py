"""
缠论模拟交易 App — 模拟交易引擎
状态机: IDLE → PLAYING → FINISHED → RESULT
- 随机选标的 + 随机截取时段 (默认 100 根, 排除最近 10 根)
- 逐根推进, 用户可买/卖
- 费用: 佣金万2.5(最低5元) + 印花税千1(卖出) + 过户费万0.2
"""
import uuid
import random
import json
from datetime import datetime

from engine.data_loader import load_symbol_full


class SimEngine:
    """单会话模拟交易引擎"""

    def __init__(self, code, bars, period="", init_asset=100000.0, fees_on=True,
                 commission_rate=0.00025, min_commission=5.0,
                 stamp_rate=0.001, transfer_rate=0.00002,
                 window_size=100, hist_n=50, start_idx=None,
                 tplus1=True):
        """tplus1: True=30分钟按T+1同日不可卖; False=T+0当日可卖"""
        self.bars = bars
        self.window_size = window_size
        self.hist_n = hist_n
        self.init_asset = float(init_asset)
        self.fees_on = fees_on
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_rate = stamp_rate
        self.transfer_rate = transfer_rate

        # 随机截取时段 (CSV 路径可传入 start_idx)
        if start_idx is None:
            total = len(bars)
            max_start = total - 10 - window_size
            if max_start < 1:
                max_start = 1
            start_idx = random.randint(0, max_start)
        self.start_idx = start_idx
        self.window = self.bars[start_idx:start_idx + window_size]

        # 状态
        self.session_id = uuid.uuid4().hex[:12]
        self.code = code
        self.period = period
        self.tradeable = period in ("", "day", "30min", "30s", "3m")
        self.tplus1 = tplus1          # True=30分钟按T+1同日不可卖; False=T+0当日可卖
        self.pos = 0
        self.avg_cost = 0.0
        self.close_profit = 0.0          # 累计已实现盈亏
        self.cash = self.init_asset
        self.cur_pos = self.hist_n - 1
        self.holdings = []
        self.status = "PLAYING"
        self.trades = []
        self.created_at = datetime.now().isoformat()

    # ---------- 查询 ----------
    def state(self):
        cur = self.window[self.cur_pos] if self.cur_pos < len(self.window) else self.window[-1]
        return {
            "session_id": self.session_id,
            "status": self.status,
            "code": self.code,
            "period": self.period,
            "tradeable": self.tradeable,
            "hist_n": self.hist_n,
            "init_asset": round(self.init_asset, 2),
            "cash": round(self.cash, 2),
            "position": self.pos,
            "avg_cost": round(self.avg_cost, 3) if self.pos else 0,
            "total_asset": round(self.total_asset(cur["close"]), 2),
            "float_profit": round(self.float_profit(cur["close"]), 2),
            "close_profit": round(self.close_profit, 2),
            "available": round(self.cash, 2),
            "cur_pos": self.cur_pos,
            "total_bars": len(self.window),
            "start_idx": self.start_idx,
            "start_date": self.window[0]["datetime"],
            "end_date": self.window[-1]["datetime"],
            "cur_bar": self._bar_view(cur),
            # 2026-08-29 复盘(FINISHED)返回全量交易, 否则只回最近20条(逐根轮询时避免传大量数据)
            "trades": self.trades if self.status == "FINISHED" else self.trades[-20:],
            "fees_on": self.fees_on,
            "tplus1": self.tplus1,
            "window": [self._bar_view(b) for b in self.window[:self.cur_pos + 1]],
            "all_bars": [self._bar_view(b) for b in self.window],
        }

    def _bar_view(self, bar):
        """单根bar视图(含信号)
        2026-08-13 对齐8765: bar 附带 time 字段, 供前端 getTimeFromIdx / findWsIdxByTime 使用"""
        return {
            "idx": bar["idx"],
            "datetime": bar["datetime"],
            "time": self._to_ts(bar["datetime"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "signals": bar.get("signals", []),
            "in_zhongshu": bar.get("in_zhongshu", False),
            "summacd": round(bar.get("summacd", 0), 4),
        }

    def _to_ts(self, dt_str):
        """datetime 字符串 → unix 秒 (支持带秒的 30s 周期和 .000000 微秒后缀)"""
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

    def sellable_qty(self):
        """T+1: 可卖数量 = 非当日买入批次持仓; T+0: 全部持仓可卖"""
        if not self.holdings:
            return 0
        if not self.tplus1:
            return self.pos
        cur_dt = self.window[self.cur_pos]["datetime"][:10]
        return sum(h["qty"] for h in self.holdings if h["dt"][:10] != cur_dt)

    def total_asset(self, price):
        return self.cash + self.pos * price

    def float_profit(self, price):
        if self.pos <= 0:
            return 0.0
        return (price - self.avg_cost) * self.pos

    # ---------- 交易 ----------
    def calc_fee(self, amount, is_buy):
        """计算费用: 佣金(双边) + 印花税(仅卖) + 过户费(双边)"""
        if not self.fees_on:
            return 0.0
        # 佣金
        commission = max(amount * self.commission_rate, self.min_commission)
        transfer = amount * self.transfer_rate
        total = commission + transfer
        if not is_buy:
            total += amount * self.stamp_rate
        return round(total, 2)

    def buy(self, ratio=1.0, qty=None):
        """买入: ratio=1全仓/0.5半仓, 或指定股数(100整数倍)"""
        if self.status != "PLAYING":
            return {"ok": False, "msg": "Cannot trade in current state"}
        if not self.tradeable:
            return {"ok": False, "msg": "Reference only, not tradeable"}
        if self.cur_pos < self.hist_n:
            return {"ok": False, "msg": f"Observation period (first {self.hist_n} bars) — advance to trade"}
        cur = self.window[self.cur_pos]
        price = cur["close"]
        # 计算可买数量
        if qty and qty > 0:
            qty = int(qty)
        elif ratio > 0:
            max_amount = self.cash
            if self.fees_on:
                max_amount = self.cash / (1 + self.commission_rate + self.transfer_rate)
            qty = int(max_amount * ratio / price / 100) * 100
        else:
            return {"ok": False, "msg": "Specify quantity or ratio"}
        if qty <= 0:
            return {"ok": False, "msg": "Insufficient funds"}
        amount = qty * price
        fee = self.calc_fee(amount, is_buy=True)
        if amount + fee > self.cash:
            # 尝试减1手
            qty -= 100
            if qty <= 0:
                return {"ok": False, "msg": "Insufficient funds (incl. fees)"}
            amount = qty * price
            fee = self.calc_fee(amount, is_buy=True)
        # 成交
        old_cost = self.avg_cost * self.pos
        self.cash -= (amount + fee)
        self.pos += qty
        total_cost = old_cost + amount + fee
        self.avg_cost = total_cost / self.pos if self.pos else 0
        # T+1: 批次记录 (同日合并)
        dt_key = cur["datetime"][:10]
        if self.holdings and self.holdings[-1]["dt"][:10] == dt_key:
            h = self.holdings[-1]
            h["qty"] += qty
            h["cost"] = (h["cost"] * (h["qty"] - qty) + amount) / h["qty"]   # cost = 纯价格(不含手续费)
            h["fee"] = round(h.get("fee", 0) + fee, 2)
        else:
            self.holdings.append({"qty": qty, "cost": amount / qty, "fee": fee, "dt": cur["datetime"]})
        self.trades.append({
            "id": uuid.uuid4().hex[:8],
            "type": "buy", "idx": cur["idx"], "datetime": cur["datetime"],
            "price": round(price, 3), "qty": qty, "amount": round(amount, 2),
            "fee": fee, "status": "holding",
        })
        return {"ok": True, "msg": f"Bought {qty} shares @ {price:.2f}", "qty": qty, "price": price,
            "fee": fee, "position": self.pos, "cash": round(self.cash, 2),
            "avg_cost": round(self.avg_cost, 3) if self.pos else 0,
            "float_profit": round(self.float_profit(price), 2),
            "total_asset": round(self.total_asset(price), 2)}

    def sell(self, ratio=1.0, qty=None):
        """卖出 (T+1: 当日买入批次不可卖出; T+0: 全部可卖)"""
        if self.status != "PLAYING":
            return {"ok": False, "msg": "Cannot trade in current state"}
        if not self.tradeable:
            return {"ok": False, "msg": "Reference only, not tradeable"}
        if self.cur_pos < self.hist_n:
            return {"ok": False, "msg": f"Observation period (first {self.hist_n} bars) — advance to trade"}
        if self.pos <= 0:
            return {"ok": False, "msg": "No position"}
        cur = self.window[self.cur_pos]
        price = cur["close"]
        # 可卖数量: T+1 = 非当日批次; T+0 = 全部持仓
        sellable = self.sellable_qty()
        if sellable <= 0:
            return {"ok": False, "msg": "T+1: Today's buy cannot be sold today"}
        if qty and qty > 0:
            qty = min(int(qty), sellable)
        elif ratio > 0:
            qty = int(sellable * ratio / 100) * 100
            if qty <= 0:
                qty = sellable
        else:
            return {"ok": False, "msg": "Specify quantity or ratio"}
        if qty <= 0:
            return {"ok": False, "msg": "Invalid sell quantity"}
        # 从最早批次开始扣 (FIFO); T+0 忽略当日批次限制
        remain = qty
        sold_cost = 0.0
        sold_fee = 0.0
        new_holdings = []
        for h in self.holdings:
            if remain <= 0:
                new_holdings.append(h)
                continue
            if h["dt"][:10] == cur["datetime"][:10] and self.tplus1:
                new_holdings.append(h)   # T+1: 当日批次不可卖
                continue
            take = min(h["qty"], remain)
            fee_portion = take * h.get("fee", 0) / h["qty"]
            sold_cost += take * h["cost"]
            sold_fee += fee_portion
            h["fee"] = round(h.get("fee", 0) - fee_portion, 2)
            remain -= take
            h["qty"] -= take
            if h["qty"] > 0:
                new_holdings.append(h)
        self.holdings = new_holdings
        amount = qty * price
        fee = self.calc_fee(amount, is_buy=False)
        profit = amount - sold_cost - fee - sold_fee
        self.close_profit = round(self.close_profit + profit, 2)
        self.cash += (amount - fee)
        self.pos -= qty
        if self.pos <= 0:
            self.pos = 0
            self.avg_cost = 0.0
        else:
            total_cost = sum(h["cost"] * h["qty"] + h.get("fee", 0) for h in self.holdings)
            self.avg_cost = total_cost / self.pos
        self.trades.append({
            "id": uuid.uuid4().hex[:8],
            "type": "sell", "idx": cur["idx"], "datetime": cur["datetime"],
            "price": round(price, 3), "qty": qty, "amount": round(amount, 2),
            "fee": fee, "profit": round(profit, 2), "status": "closed",
        })
        return {"ok": True, "msg": f"Sold {qty} shares @ {price:.2f}", "qty": qty, "price": price,
            "fee": fee, "profit": round(profit, 2), "position": self.pos,
            "cash": round(self.cash, 2), "avg_cost": round(self.avg_cost, 3) if self.pos else 0,
            "float_profit": round(self.float_profit(price), 2),
            "close_profit": round(self.close_profit, 2),
            "total_asset": round(self.total_asset(price), 2)}

    # ---------- 推进 ----------
    def advance(self, n=1):
        """推进 n 根 K 线"""
        if self.status != "PLAYING":
            return {"ok": False, "msg": f"State: {self.status}"}
        self.cur_pos += n
        if self.cur_pos >= len(self.window) - 1:
            self.cur_pos = len(self.window) - 1
            # 先自动清仓（此时仍是 PLAYING，sell 可用），再标记完成
            self._auto_close()
            self.status = "FINISHED"
        return {"ok": True, "cur_pos": self.cur_pos, "status": self.status}

    def _auto_close(self):
        """结束时自动清仓 (模拟结束强制结算, 不受 T+1 限制)"""
        if self.pos <= 0:
            return
        cur = self.window[self.cur_pos]
        price = cur["close"]
        qty = self.pos
        amount = qty * price
        fee = self.calc_fee(amount, is_buy=False)
        sold_cost = sum(h["cost"] * h["qty"] for h in self.holdings)
        sold_fee = sum(h.get("fee", 0) for h in self.holdings)
        profit = amount - sold_cost - fee - sold_fee
        self.close_profit = round(self.close_profit + profit, 2)
        self.cash += (amount - fee)
        self.pos = 0
        self.avg_cost = 0.0
        self.holdings = []
        self.trades.append({
            "id": uuid.uuid4().hex[:8],
            "type": "sell", "idx": cur["idx"], "datetime": cur["datetime"],
            "price": round(price, 3), "qty": qty, "amount": round(amount, 2),
            "fee": fee, "profit": round(profit, 2), "status": "closed",
            "note": "Auto-closed",
        })

    def result(self):
        """统计结果"""
        final_price = self.window[-1]["close"]
        total_asset = self.total_asset(final_price)
        profit = total_asset - self.init_asset
        profit_rate = profit / self.init_asset * 100 if self.init_asset else 0
        # 交易统计
        buys = [t for t in self.trades if t["type"] == "buy"]
        sells = [t for t in self.trades if t["type"] == "sell"]
        closed = [t for t in sells if "profit" in t]
        win = [t for t in closed if t["profit"] > 0]
        losses = [t for t in closed if t["profit"] <= 0]
        return {
            "session_id": self.session_id,
            "status": self.status,
            "init_asset": round(self.init_asset, 2),
            "final_asset": round(total_asset, 2),
            "total_profit": round(profit, 2),
            "profit_rate": round(profit_rate, 2),
            "trade_count": len(buys),
            "sell_count": len(closed),
            "win_count": len(win),
            "loss_count": len(losses),
            "max_win": round(max([t["profit"] for t in win], default=0), 2),
            "max_loss": round(min([t["profit"] for t in losses], default=0), 2),
            "remain_position": self.pos,
            "end_date": self.window[-1]["datetime"],
        }


# ---------- 会话管理器 ----------
class SessionManager:
    def __init__(self):
        self.sessions = {}      # session_id -> SimEngine
    def create(self, code, period="", init_asset=100000.0, fees_on=True,
               window_size=100, tplus1=True):
        """创建新会话（随机窗口）"""
        data = load_symbol_full(code, period)
        if not data or len(data["bars"]) < 30:
            return None
        eng = SimEngine(code, data["bars"], period=period, init_asset=init_asset,
                        fees_on=fees_on, window_size=window_size, tplus1=tplus1)
        self.sessions[eng.session_id] = eng
        return eng

    def get(self, session_id):
        return self.sessions.get(session_id)

    def create_from_bars(self, code, bars, period="", init_asset=100000.0, fees_on=True,
                         window_size=150, tplus1=True):
        """从外部 bars 创建会话（CSV 上传用）"""
        if len(bars) < window_size + 50:
            return None
        ws = min(window_size, len(bars))
        start = random.randint(0, len(bars) - ws - 50)
        eng = SimEngine(code, bars, period=period, init_asset=init_asset,
                        fees_on=fees_on, window_size=ws, start_idx=start, tplus1=tplus1)
        self.sessions[eng.session_id] = eng
        return eng


manager = SessionManager()
