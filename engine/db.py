"""
缠论模拟交易 App — SQLite 数据库
用户 / 模拟记录 / 排行榜
"""
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "app.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        nickname TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        code TEXT NOT NULL,
        period TEXT DEFAULT '',
        init_asset REAL NOT NULL,
        final_asset REAL,
        profit REAL,
        profit_rate REAL,
        trade_count INTEGER DEFAULT 0,
        win_count INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """
    )
    # 兼容旧库: 补 period 列
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "period" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN period TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()

def create_user(nickname: str):
    conn = _conn()
    uid = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO users (id, nickname, created_at) VALUES (?,?,?)",
                 (uid, nickname, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return uid


def get_or_create_user(nickname: str):
    """按昵称查找，不存在则创建"""
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE nickname=?", (nickname,)).fetchone()
    if row:
        conn.close()
        return row["id"]
    conn.close()
    return create_user(nickname)


def save_result(user_id, code, init_asset, final_asset, profit, profit_rate,
                trade_count, win_count, period=""):
    """保存一次模拟结果，返回 session id"""
    conn = _conn()
    sid = uuid.uuid4().hex[:12]
    win_rate = round(win_count / trade_count * 100, 2) if trade_count else 0
    conn.execute("""
        INSERT INTO sessions (id, user_id, code, period, init_asset, final_asset,
            profit, profit_rate, trade_count, win_count, win_rate,
            created_at, finished_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (sid, user_id, code, period, init_asset, final_asset, profit, profit_rate,
          trade_count, win_count, win_rate, datetime.now().isoformat(),
          datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return sid


def leaderboard(limit=20, offset=0, period="", code="", rank_type="rate"):
    """排行榜: 全部按昵称聚合排名
    rate:  每个昵称历史最高单次收益率
    count: 每个昵称总交易次数 SUM(trade_count)
    wins:  每个昵称总盈利次数 SUM(win_count), 仅>0的昵称
    period/code: 筛选(可选)
    返回统一行: {id, nickname, code:'', period:'全部', profit_rate, profit, trade_count, win_count}"""
    conn = _conn()
    where = []
    args = []
    if period:
        where.append("s.period=?")
        args.append(period)
    if code:
        where.append("s.code=?")
        args.append(code)
    where_sql = " AND ".join(where) if where else "1=1"

    base = f"SELECT u.nickname, u.id as user_id FROM sessions s JOIN users u ON s.user_id = u.id WHERE {where_sql}"

    if rank_type == "count":
        # 按昵称总交易次数排名
        rows = conn.execute(f"""
            SELECT nickname, user_id,
                   SUM(s.trade_count) as total_trades,
                   SUM(s.win_count) as total_wins,
                   MAX(s.profit_rate) as best_rate,
                   ROUND(SUM(s.profit),2) as sum_profit
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE {where_sql}
            GROUP BY s.user_id
            ORDER BY total_trades DESC
            LIMIT ? OFFSET ?
        """, (*args, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) as c FROM (SELECT s.user_id FROM sessions s WHERE {where_sql} GROUP BY s.user_id)", args).fetchone()["c"]

    elif rank_type == "wins":
        # 按昵称总盈利次数排名(只>0的)
        rows = conn.execute(f"""
            SELECT nickname, user_id,
                   SUM(s.win_count) as total_wins,
                   SUM(s.trade_count) as total_trades,
                   MAX(s.profit_rate) as best_rate,
                   ROUND(SUM(s.profit),2) as sum_profit
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE {where_sql}
            GROUP BY s.user_id
            HAVING total_wins > 0
            ORDER BY total_wins DESC
            LIMIT ? OFFSET ?
        """, (*args, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) as c FROM (SELECT s.user_id FROM sessions s WHERE {where_sql} GROUP BY s.user_id HAVING SUM(s.win_count)>0)", args).fetchone()["c"]

    else:  # rate: 按昵称历史最高单次收益率
        rows = conn.execute(f"""
            SELECT nickname, user_id,
                   MAX(s.profit_rate) as best_rate,
                   SUM(s.trade_count) as total_trades,
                   SUM(s.win_count) as total_wins,
                   ROUND(SUM(s.profit),2) as sum_profit
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE {where_sql} AND s.profit_rate IS NOT NULL
            GROUP BY s.user_id
            ORDER BY best_rate DESC
            LIMIT ? OFFSET ?
        """, (*args, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) as c FROM (SELECT s.user_id FROM sessions s WHERE {where_sql} AND s.profit_rate IS NOT NULL GROUP BY s.user_id)", args).fetchone()["c"]

    out = []
    for r in rows:
        rd = dict(r)
        out.append({
            "id": rd.get("user_id") or "",
            "nickname": rd.get("nickname") or "",
            "code": "",
            "period": "全部",
            "profit_rate": rd.get("best_rate") or 0,
            "profit": rd.get("sum_profit") or 0,
            "trade_count": rd.get("total_trades") or 0,
            "win_count": rd.get("total_wins") or 0,
            "created_at": "",
            "finished_at": "",
        })
    conn.close()
    return out, total


def user_sessions(user_id, limit=50):
    """用户历史战绩"""
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM sessions WHERE user_id=?
        ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_nickname(user_id, new_nickname):
    """修改用户昵称。新昵称不能与其他用户冲突。返回新 user_id"""
    conn = _conn()
    row = conn.execute("SELECT id, nickname FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("user not found")
    if row["nickname"] == new_nickname:
        conn.close()
        return row["id"]
    # 检查冲突
    conflict = conn.execute("SELECT id FROM users WHERE nickname=? AND id!=?", (new_nickname, user_id)).fetchone()
    if conflict:
        conn.close()
        raise ValueError("nickname exists")
    conn.execute("UPDATE users SET nickname=? WHERE id=?", (new_nickname, user_id))
    conn.commit()
    conn.close()
    return user_id


def find_user_by_nickname(nickname: str):
    """按昵称查用户"""
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE nickname=? LIMIT 1", (nickname,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_user_by_id(uid: str):
    """按id查用户"""
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def user_sessions(user_id, limit=50):
    """用户历史战绩"""
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM sessions WHERE user_id=?
        ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_stats(user_id):
    """用户累计统计"""
    conn = _conn()
    row = conn.execute("""
        SELECT COUNT(*) as total, SUM(profit) as sum_profit,
               AVG(profit_rate) as avg_rate,
               SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as win_cnt
        FROM sessions WHERE user_id=?
    """, (user_id,)).fetchone()
    conn.close()
    d = dict(row)
    total = d["total"] or 0
    return {
        "total_sessions": total,
        "sum_profit": round(d["sum_profit"] or 0, 2),
        "avg_rate": round(d["avg_rate"] or 0, 2),
        "win_rate": round((d["win_cnt"] or 0) / total * 100, 2) if total else 0,
    }


if __name__ == "__main__":
    init_db()
    print("✅ DB 初始化完成:", DB_PATH)
