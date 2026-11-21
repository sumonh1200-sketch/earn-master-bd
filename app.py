import os
import sqlite3
from datetime import datetime, date
from zoneinfo import ZoneInfo
from functools import wraps

from flask import Flask, request, jsonify

app = Flask(__name__)

DATABASE = os.environ.get("DATABASE_PATH", "vip_income.db")
API_KEY = os.environ.get("API_KEY", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
TIMEZONE = ZoneInfo("Asia/Dhaka")


def db_connect():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                referred_by TEXT,
                joined_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                method TEXT NOT NULL,
                account_number TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_claims (
                user_id TEXT NOT NULL,
                claim_date TEXT NOT NULL,
                PRIMARY KEY (user_id, claim_date)
            )
        """)


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        supplied = request.headers.get("X-API-KEY", "")
        if not API_KEY or supplied != API_KEY:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_admin_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        supplied = request.headers.get("X-ADMIN-KEY", "")
        if not ADMIN_KEY or supplied != ADMIN_KEY:
            return jsonify({"ok": False, "error": "Unauthorized admin"}), 401
        return fn(*args, **kwargs)
    return wrapper


def get_json():
    return request.get_json(silent=True) or {}


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "VIP FREE INCOME BOT API",
        "message": "Backend is running"
    })


@app.post("/api/start")
@require_api_key
def start_user():
    data = get_json()
    user_id = str(data.get("user_id", "")).strip()
    referrer_id = str(data.get("referrer_id", "")).strip()

    if not user_id:
        return jsonify({"ok": False, "error": "user_id is required"}), 400

    now = datetime.now(TIMEZONE).isoformat()
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT user_id, balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if existing:
            return jsonify({
                "ok": True,
                "message": "User already exists",
                "user_id": user_id,
                "balance": existing["balance"]
            })

        valid_referrer = None
        if referrer_id and referrer_id != user_id:
            ref = conn.execute(
                "SELECT user_id FROM users WHERE user_id = ?",
                (referrer_id,)
            ).fetchone()
            if ref:
                valid_referrer = referrer_id

        conn.execute(
            "INSERT INTO users (user_id, balance, referred_by, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, 3, valid_referrer, now)
        )

        if valid_referrer:
            conn.execute(
                "UPDATE users SET balance = balance + 2 WHERE user_id = ?",
                (valid_referrer,)
            )

    return jsonify({
        "ok": True,
        "message": "Welcome bonus added",
        "user_id": user_id,
        "welcome_bonus": 3,
        "referral_bonus": 2 if valid_referrer else 0,
        "balance": 3
    })


@app.get("/api/balance")
@require_api_key
def balance():
    user_id = str(request.args.get("user_id", "")).strip()
    if not user_id:
        return jsonify({"ok": False, "error": "user_id is required"}), 400

    with db_connect() as conn:
        user = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404

    return jsonify({"ok": True, "user_id": user_id, "balance": user["balance"]})


@app.post("/api/bonus")
@require_api_key
def daily_bonus():
    data = get_json()
    user_id = str(data.get("user_id", "")).strip()
    if not user_id:
        return jsonify({"ok": False, "error": "user_id is required"}), 400

    today = datetime.now(TIMEZONE).date().isoformat()
    with db_connect() as conn:
        user = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        claimed = conn.execute(
            "SELECT user_id FROM daily_claims WHERE user_id = ? AND claim_date = ?",
            (user_id, today)
        ).fetchone()
        if claimed:
            return jsonify({"ok": False, "error": "Daily bonus already claimed today"}), 409

        conn.execute(
            "INSERT INTO daily_claims (user_id, claim_date) VALUES (?, ?)",
            (user_id, today)
        )
        conn.execute(
            "UPDATE users SET balance = balance + 1 WHERE user_id = ?",
            (user_id,)
        )
        new_balance = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()["balance"]

    return jsonify({
        "ok": True,
        "daily_bonus": 1,
        "balance": new_balance
    })


@app.post("/api/withdraw")
@require_api_key
def create_withdrawal():
    data = get_json()
    user_id = str(data.get("user_id", "")).strip()
    method = str(data.get("method", "")).strip().lower()
    account_number = str(data.get("account_number", "")).strip()

    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0

    if not user_id or not account_number:
        return jsonify({"ok": False, "error": "user_id and account_number are required"}), 400
    if amount < 150:
        return jsonify({"ok": False, "error": "Minimum withdrawal is 150 taka"}), 400
    if method not in ("bkash", "nagad", "rocket"):
        return jsonify({"ok": False, "error": "method must be bkash, nagad, or rocket"}), 400

    now = datetime.now(TIMEZONE).isoformat()
    with db_connect() as conn:
        user = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404
        if user["balance"] < amount:
            return jsonify({"ok": False, "error": "Insufficient balance"}), 400

        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
        cur = conn.execute(
            """INSERT INTO withdrawals
               (user_id, amount, method, account_number, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (user_id, amount, method, account_number, now)
        )
        withdrawal_id = cur.lastrowid

    return jsonify({
        "ok": True,
        "message": "Withdrawal request submitted for admin review",
        "withdrawal_id": withdrawal_id,
        "status": "pending"
    })


@app.get("/api/admin/withdrawals")
@require_admin_key
def list_withdrawals():
    status = request.args.get("status", "pending")
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT id, user_id, amount, method, account_number, status, created_at
               FROM withdrawals WHERE status = ? ORDER BY id DESC""",
            (status,)
        ).fetchall()

    return jsonify({"ok": True, "withdrawals": [dict(row) for row in rows]})


@app.post("/api/admin/withdrawal")
@require_admin_key
def update_withdrawal():
    data = get_json()
    try:
        withdrawal_id = int(data.get("withdrawal_id", 0))
    except (TypeError, ValueError):
        withdrawal_id = 0

    action = str(data.get("action", "")).strip().lower()
    if action not in ("approve", "reject"):
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400

    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, user_id, amount, status FROM withdrawals WHERE id = ?",
            (withdrawal_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Withdrawal not found"}), 404
        if row["status"] != "pending":
            return jsonify({"ok": False, "error": "Withdrawal is not pending"}), 409

        new_status = "approved" if action == "approve" else "rejected"
        conn.execute(
            "UPDATE withdrawals SET status = ? WHERE id = ?",
            (new_status, withdrawal_id)
        )
        if action == "reject":
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (row["amount"], row["user_id"])
            )

    return jsonify({
        "ok": True,
        "withdrawal_id": withdrawal_id,
        "status": new_status,
        "refunded": row["amount"] if action == "reject" else 0
    })


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
