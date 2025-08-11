import os
import logging
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
)

# ---------- تنظیمات اولیه ----------
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "0000 - 0000 - 0000 - 0000"

RENDER_BASE_URL = os.getenv("RENDER_BASE_URL") or "https://teaz.onrender.com"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_BASE_URL}{WEBHOOK_PATH}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

app = FastAPI()
application = Application.builder().token(TOKEN).build()

# ---------- PostgreSQL connection ----------
import psycopg2
from psycopg2 import pool

DATABASE_URL = os.getenv("DATABASE_URL")

db_pool: pool.ThreadedConnectionPool = None

def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)

def close_db_pool():
    global db_pool
    if db_pool:
        db_pool.closeall()
        db_pool = None

def _db_execute_sync(query, params=(), fetch=False, fetchone=False, returning=False):
    conn = None
    cur = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute(query, params)
        result = None
        if returning:
            result = cur.fetchone()[0]
        elif fetchone:
            result = cur.fetchone()
        elif fetch:
            result = cur.fetchall()
        if not query.strip().lower().startswith("select"):
            conn.commit()
        return result
    finally:
        if cur:
            cur.close()
        if conn:
            db_pool.putconn(conn)

async def db_execute(query, params=(), fetch=False, fetchone=False, returning=False):
    return await asyncio.to_thread(_db_execute_sync, query, params, fetch, fetchone, returning)

# ---------- ساخت جداول ----------
CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    balance BIGINT DEFAULT 0,
    invited_by BIGINT,
    phone TEXT
)
"""
CREATE_PAYMENTS_SQL = """
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    amount BIGINT,
    status TEXT,
    type TEXT,
    description TEXT
)
"""
CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    payment_id INTEGER,
    plan TEXT,
    config TEXT,
    status TEXT DEFAULT 'active',
    start_date TIMESTAMP,
    end_date TIMESTAMP
)
"""

async def create_tables():
    await db_execute(CREATE_USERS_SQL)
    await db_execute(CREATE_PAYMENTS_SQL)
    await db_execute(CREATE_SUBSCRIPTIONS_SQL)

# ---------- کیبوردها ----------
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💰 موجودی"), KeyboardButton("💳 خرید اشتراک")],
        [KeyboardButton("🎁 اشتراک تست رایگان"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("💵 اعتبار رایگان"), KeyboardButton("📂 اشتراک‌های من")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_balance_keyboard():
    keyboard = [
        [KeyboardButton("نمایش موجودی"), KeyboardButton("افزایش موجودی")],
        [KeyboardButton("بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("بازگشت به منو")]], resize_keyboard=True)

def get_subscription_keyboard():
    keyboard = [
        [KeyboardButton("۱ ماهه: ۹۰ هزار تومان")],
        [KeyboardButton("۳ ماهه: ۲۵۰ هزار تومان")],
        [KeyboardButton("۶ ماهه: ۴۵۰ هزار تومان")],
        [KeyboardButton("بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------- توابع DB ----------
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

async def ensure_user(user_id, username, invited_by=None):
    row = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if not row:
        await db_execute(
            "INSERT INTO users (user_id, username, invited_by) VALUES (%s, %s, %s)",
            (user_id, username, invited_by)
        )
        if invited_by and invited_by != user_id:
            inviter = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (invited_by,), fetchone=True)
            if inviter:
                await add_balance(invited_by, 25000)

async def save_user_phone(user_id, phone):
    await db_execute("UPDATE users SET phone = %s WHERE user_id = %s", (phone, user_id))

async def get_user_phone(user_id):
    row = await db_execute("SELECT phone FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    return row[0] if row else None

async def add_balance(user_id, amount):
    await db_execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE user_id = %s", (amount, user_id))

async def get_balance(user_id):
    row = await db_execute("SELECT balance FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    return int(row[0]) if row and row[0] is not None else 0

async def add_payment(user_id, amount, ptype, description=""):
    query = "INSERT INTO payments (user_id, amount, status, type, description) VALUES (%s, %s, 'pending', %s, %s) RETURNING id"
    new_id = await db_execute(query, (user_id, amount, ptype, description), returning=True)
    return int(new_id)

async def add_subscription(user_id, payment_id, plan):
    # محاسبه تاریخ پایان بر اساس پلن
    days_map = {
        "۱ ماهه: ۹۰ هزار تومان": 30,
        "۳ ماهه: ۲۵۰ هزار تومان": 90,
        "۶ ماهه: ۴۵۰ هزار تومان": 180
    }
    days = days_map.get(plan, 30)
    start = datetime.utcnow()
    end = start + timedelta(days=days)
    await db_execute(
        "INSERT INTO subscriptions (user_id, payment_id, plan, status, start_date, end_date) VALUES (%s, %s, %s, 'active', %s, %s)",
        (user_id, payment_id, plan, start, end)
    )

async def update_subscription_config(payment_id, config):
    await db_execute("UPDATE subscriptions SET config = %s WHERE payment_id = %s", (config, payment_id))

async def update_payment_status(payment_id, status):
    await db_execute("UPDATE payments SET status = %s WHERE id = %s", (status, payment_id))

async def get_user_subscriptions(user_id):
    # چک انقضای اشتراک‌ها
    now = datetime.utcnow()
    await db_execute("UPDATE subscriptions SET status = 'inactive' WHERE end_date < %s AND status = 'active'", (now,))
    rows = await db_execute("SELECT id, plan, config, status, payment_id, start_date, end_date FROM subscriptions WHERE user_id = %s", (user_id,), fetch=True)
    return rows
