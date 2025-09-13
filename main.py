import os
import logging
import asyncio
import random
import string
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
)

# ---------- Initial Setup ----------
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAMES = ["@teazvpn", "@charkhoun"]
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "6037 9975 9717 2684"
SUPPORT_ID = "@teazadmin"

RENDER_BASE_URL = os.getenv("RENDER_BASE_URL") or "https://teaz.onrender.com"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_BASE_URL}{WEBHOOK_PATH}"

# Configure logging to capture all levels
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()
application = Application.builder().token(TOKEN).build()

# ---------- PostgreSQL Connection Pool ----------
import psycopg2
from psycopg2 import pool

DATABASE_URL = os.getenv("DATABASE_URL")

db_pool: pool.ThreadedConnectionPool = None

def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    try:
        db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
        logger.info("Database pool initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        raise

def close_db_pool():
    global db_pool
    if db_pool:
        db_pool.closeall()
        db_pool = None
        logger.info("Database pool closed")

def _db_execute_sync(query, params=(), fetch=False, fetchone=False, returning=False):
    conn = None
    cur = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute(query, params)
        result = None
        if returning:
            result = cur.fetchone()[0] if cur.rowcount > 0 else None
        elif fetchone:
            result = cur.fetchone()
        elif fetch:
            result = cur.fetchall()
        if not query.strip().lower().startswith("select"):
            conn.commit()
        return result
    except Exception as e:
        logger.error(f"Database error in query '{query}' with params {params}: {e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            db_pool.putconn(conn)

async def db_execute(query, params=(), fetch=False, fetchone=False, returning=False):
    try:
        return await asyncio.to_thread(_db_execute_sync, query, params, fetch, fetchone, returning)
    except Exception as e:
        logger.error(f"Async database error in query '{query}' with params {params}: {e}")
        raise

# ---------- Create and Migrate Tables ----------
CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    balance BIGINT DEFAULT 0,
    invited_by BIGINT,
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_agent BOOLEAN DEFAULT FALSE
)
"""
CREATE_PAYMENTS_SQL = """
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    amount BIGINT,
    status TEXT,
    type TEXT,
    payment_method TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""
CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    payment_id INTEGER,
    plan TEXT,
    config TEXT,
    status TEXT DEFAULT 'pending',
    start_date TIMESTAMP,
    duration_days INTEGER
)
"""
CREATE_COUPONS_SQL = """
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    discount_percent INTEGER,
    user_id BIGINT,
    is_used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiry_date TIMESTAMP GENERATED ALWAYS AS (created_at + INTERVAL '3 days') STORED
)
"""
MIGRATE_SUBSCRIPTIONS_SQL = """
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS start_date TIMESTAMP;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS duration_days INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_agent BOOLEAN DEFAULT FALSE;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_method TEXT;
UPDATE subscriptions SET start_date = COALESCE(start_date, CURRENT_TIMESTAMP),
                        duration_days = CASE
                            WHEN plan = '🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره' THEN 30
                            WHEN plan = '🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره' THEN 90
                            WHEN plan = '🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره' THEN 180
                            WHEN plan = '🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره' THEN 30
                            WHEN plan = '🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره' THEN 90
                            WHEN plan = '🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره' THEN 180
                            ELSE 30
                        END
WHERE start_date IS NULL OR duration_days IS NULL;
"""

async def create_tables():
    try:
        await db_execute(CREATE_USERS_SQL)
        await db_execute(CREATE_PAYMENTS_SQL)
        await db_execute(CREATE_SUBSCRIPTIONS_SQL)
        await db_execute(CREATE_COUPONS_SQL)
        await db_execute(MIGRATE_SUBSCRIPTIONS_SQL)
        logger.info("Database tables created and migrated successfully")
    except Exception as e:
        logger.error(f"Error creating or migrating tables: {e}")

# ---------- Keyboards ----------
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💰 موجودی"), KeyboardButton("💳 خرید اشتراک")],
        [KeyboardButton("🎁 اشتراک تست رایگان"), KeyboardButton("☎️ پشتیبانی")],
        [KeyboardButton("💵 اعتبار رایگان"), KeyboardButton("📂 اشتراک‌های من")],
        [KeyboardButton("💡 راهنمای اتصال"), KeyboardButton("🧑‍💼 درخواست نمایندگی")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_balance_keyboard():
    keyboard = [
        [KeyboardButton("نمایش موجودی"), KeyboardButton("افزایش موجودی")],
        [KeyboardButton("⬅️ بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)

def get_subscription_keyboard(is_agent=False):
    if is_agent:
        keyboard = [
            [KeyboardButton("🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("⬅️ بازگشت به منو")]
        ]
    else:
        keyboard = [
            [KeyboardButton("🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره")],
            [KeyboardButton("⬅️ بازگشت به منو")]
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_payment_method_keyboard():
    keyboard = [
        [KeyboardButton("🏦 کارت به کارت")],
        [KeyboardButton("💎 پرداخت با ترون")],
        [KeyboardButton("💰 پرداخت با موجودی")],
        [KeyboardButton("⬅️ بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_connection_guide_keyboard():
    keyboard = [
        [KeyboardButton("📗 اندروید")],
        [KeyboardButton("📕 آیفون/مک")],
        [KeyboardButton("📘 ویندوز")],
        [KeyboardButton("📙 لینوکس")],
        [KeyboardButton("⬅️ بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_coupon_recipient_keyboard():
    keyboard = [
        [KeyboardButton("📢 برای همه")],
        [KeyboardButton("👤 برای یک نفر")],
        [KeyboardButton("🎯 درصد خاصی از کاربران")],
        [KeyboardButton("⬅️ بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------- Helper Functions ----------
async def send_long_message(chat_id, text, context, reply_markup=None, parse_mode=None):
    max_message_length = 4000
    if len(text) <= max_message_length:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    messages = []
    current_message = ""
    for line in text.split("\n"):
        if len(current_message) + len(line) + 1 > max_message_length:
            messages.append(current_message)
            current_message = line + "\n"
        else:
            current_message += line + "\n"
    if current_message:
        messages.append(current_message)
    for i, msg in enumerate(messages):
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=reply_markup if i == len(messages) - 1 else None,
            parse_mode=parse_mode
        )

def generate_coupon_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# ---------- Database Functions ----------
async def ensure_user(user_id, username, invited_by=None):
    try:
        row = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        if not row:
            await db_execute(
                "INSERT INTO users (user_id, username, invited_by, is_agent) VALUES (%s, %s, %s, FALSE)",
                (user_id, username, invited_by)
            )
            logger.debug(f"User {user_id} ensured in database")
    except Exception as e:
        logger.error(f"Error ensuring user {user_id}: {e}")

async def set_user_agent(user_id, is_agent=True):
    try:
        await db_execute("UPDATE users SET is_agent = %s WHERE user_id = %s", (is_agent, user_id))
        logger.debug(f"User {user_id} set as {'agent' if is_agent else 'regular user'}")
    except Exception as e:
        logger.error(f"Error setting user {user_id} as {'agent' if is_agent else 'regular user'}: {e}")

async def is_user_agent(user_id):
    try:
        row = await db_execute("SELECT is_agent FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return row[0] if row and row[0] is not None else False
    except Exception as e:
        logger.error(f"Error checking agent status for user_id {user_id}: {e}")
        return False

async def save_user_phone(user_id, phone):
    try:
        await db_execute("UPDATE users SET phone = %s WHERE user_id = %s", (phone, user_id))
        logger.debug(f"Phone saved for user_id {user_id}")
    except Exception as e:
        logger.error(f"Error saving user phone for user_id {user_id}: {e}")

async def get_user_phone(user_id):
    try:
        row = await db_execute("SELECT phone FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Error getting user phone for user_id {user_id}: {e}")
        return None

async def add_balance(user_id, amount):
    try:
        await db_execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE user_id = %s", (amount, user_id))
        logger.debug(f"Added {amount} to balance for user_id {user_id}")
    except Exception as e:
        logger.error(f"Error adding balance for user_id {user_id}: {e}")

async def deduct_balance(user_id, amount):
    try:
        await db_execute("UPDATE users SET balance = COALESCE(balance,0) - %s WHERE user_id = %s", (amount, user_id))
        logger.debug(f"Deducted {amount} from balance for user_id {user_id}")
    except Exception as e:
        logger.error(f"Error deducting balance for user_id {user_id}: {e}")

async def get_balance(user_id):
    try:
        row = await db_execute("SELECT balance FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        logger.error(f"Error getting balance for user_id {user_id}: {e}")
        return 0

async def add_payment(user_id, amount, ptype, payment_method, description="", coupon_code=None):
    try:
        query = "INSERT INTO payments (user_id, amount, status, type, payment_method, description) VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING id"
        new_id = await db_execute(query, (user_id, amount, ptype, payment_method, description), returning=True)
        if coupon_code:
            await mark_coupon_used(coupon_code)
        logger.debug(f"Payment added for user_id {user_id}, amount: {amount}, type: {ptype}, payment_method: {payment_method}, id: {new_id}")
        return int(new_id) if new_id is not None else None
    except Exception as e:
        logger.error(f"Error adding payment for user_id {user_id}: {e}")
        return None

async def add_subscription(user_id, payment_id, plan):
    try:
        duration_mapping = {
            "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": 30,
            "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": 90,
            "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": 180,
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 30,
            "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 90,
            "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 180
        }
        duration_days = duration_mapping.get(plan, 30)
        await db_execute(
            "INSERT INTO subscriptions (user_id, payment_id, plan, status, start_date, duration_days) VALUES (%s, %s, %s, 'pending', CURRENT_TIMESTAMP, %s)",
            (user_id, payment_id, plan, duration_days)
        )
        logger.debug(f"Subscription added for user_id {user_id}, payment_id: {payment_id}, plan: {plan}, duration: {duration_days} days")
    except Exception as e:
        logger.error(f"Error adding subscription for user_id {user_id}, payment_id: {payment_id}: {e}")
        raise

async def update_subscription_config(payment_id, config):
    try:
        await db_execute(
            "UPDATE subscriptions SET config = %s, status = 'active' WHERE payment_id = %s",
            (config, payment_id)
        )
        logger.debug(f"Subscription config updated for payment_id {payment_id}")
    except Exception as e:
        logger.error(f"Error updating subscription config for payment_id {payment_id}: {e}")

async def update_payment_status(payment_id, status):
    try:
        await db_execute("UPDATE payments SET status = %s WHERE id = %s", (status, payment_id))
        logger.debug(f"Payment status updated to {status} for payment_id {payment_id}")
    except Exception as e:
        logger.error(f"Error updating payment status for payment_id {payment_id}: {e}")

async def get_user_subscriptions(user_id):
    try:
        rows = await db_execute(
            """
            SELECT s.id, s.plan, s.config, s.status, s.payment_id, s.start_date, s.duration_days, u.username
            FROM subscriptions s
            LEFT JOIN users u ON s.user_id = u.user_id
            WHERE s.user_id = %s
            ORDER BY s.status DESC, s.start_date DESC
            """,
            (user_id,), fetch=True
        )
        current_time = datetime.now()
        subscriptions = []
        for row in rows:
            try:
                sub_id, plan, config, status, payment_id, start_date, duration_days, username = row
                start_date = start_date or current_time
                duration_days = duration_days or 30
                if status == "active":
                    end_date = start_date + timedelta(days=duration_days)
                    if current_time > end_date:
                        await db_execute("UPDATE subscriptions SET status = 'inactive' WHERE id = %s", (sub_id,))
                        status = "inactive"
                subscriptions.append({
                    'id': sub_id,
                    'plan': plan,
                    'config': config,
                    'status': status,
                    'payment_id': payment_id,
                    'start_date': start_date,
                    'duration_days': duration_days,
                    'username': username or str(user_id),
                    'end_date': start_date + timedelta(days=duration_days)
                })
            except Exception as e:
                logger.error(f"Error processing subscription {sub_id} for user_id {user_id}: {e}")
                continue
        logger.debug(f"Processed {len(subscriptions)} subscriptions for user_id {user_id}")
        return subscriptions
    except Exception as e:
        logger.error(f"Error in get_user_subscriptions for user_id {user_id}: {e}")
        return []

async def create_coupon(code, discount_percent, user_id=None):
    try:
        await db_execute(
            "INSERT INTO coupons (code, discount_percent, user_id, is_used) VALUES (%s, %s, %s, FALSE)",
            (code, discount_percent, user_id)
        )
        logger.debug(f"Coupon {code} created with {discount_percent}% discount for user_id {user_id or 'all'}")
    except Exception as e:
        logger.error(f"Error creating coupon {code}: {e}")
        raise

async def validate_coupon(code, user_id):
    try:
        row = await db_execute(
            "SELECT discount_percent, user_id, is_used, expiry_date FROM coupons WHERE code = %s",
            (code,), fetchone=True
        )
        if not row:
            return None, "کد تخفیف نامعتبر است."
        discount_percent, coupon_user_id, is_used, expiry_date = row
        if is_used:
            return None, "این کد تخفیف قبلاً استفاده شده است."
        if datetime.now() > expiry_date:
            return None, "این کد تخفیف منقضی شده است."
        if coupon_user_id is not None and coupon_user_id != user_id:
            return None, "این کد تخفیف برای شما نیست."
        if await is_user_agent(user_id):
            return None, "نمایندگان نمی‌توانند از کد تخفیف استفاده کنند."
        return discount_percent, None
    except Exception as e:
        logger.error(f"Error validating coupon {code} for user_id {user_id}: {e}")
        return None, "خطا در بررسی کد تخفیف."

async def mark_coupon_used(code):
    try:
        await db_execute("UPDATE coupons SET is_used = TRUE WHERE code = %s", (code,))
        logger.debug(f"Coupon {code} marked as used")
    except Exception as e:
        logger.error(f"Error marking coupon {code} as used: {e}")

async def is_user_member(user_id):
    try:
        for channel in CHANNEL_USERNAMES:
            member = await application.bot.get_chat_member(channel, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        return True
    except Exception as e:
        logger.error(f"Error checking membership for user_id {user_id}: {e}")
        return False

# ---------- User States ----------
user_states = {}

# ---------- Command Handlers ----------
async def set_bot_commands():
    try:
        public_commands = [
            BotCommand(command="/start", description="شروع ربات")
        ]
        admin_commands = [
            BotCommand(command="/start", description="شروع ربات"),
            BotCommand(command="/debug_subscriptions", description="تشخیص اشتراک‌ها (ادمین)"),
            BotCommand(command="/cleardb", description="پاک کردن دیتابیس (ادمین)"),
            BotCommand(command="/stats", description="آمار ربات (ادمین)"),
            BotCommand(command="/numbers", description="نمایش شماره‌های کاربران (ادمین)"),
            BotCommand(command="/coupon", description="ایجاد کد تخفیف (ادمین)"),
            BotCommand(command="/notification", description="ارسال اطلاعیه به همه کاربران (ادمین)"),
            BotCommand(command="/backup", description="تهیه بکاپ از دیتابیس (ادمین)"),
            BotCommand(command="/restore", description="بازیابی دیتابیس از بکاپ (ادمین)"),
            BotCommand(command="/change_user_type", description="تغییر نوع کاربر (ادمین)"),
            BotCommand(command="/balance_management", description="مدیریت موجودی کاربران (ادمین)")
        ]
        await application.bot.set_my_commands(public_commands)
        await application.bot.set_my_commands(admin_commands, scope={"type": "chat", "chat_id": ADMIN_ID})
        logger.info("Bot commands set successfully")
    except Exception as e:
        logger.error(f"Error setting bot commands: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    logger.debug(f"Received /start from user_id: {user_id}, username: {username}")

    if not await is_user_member(user_id):
        kb = [[InlineKeyboardButton(f"📢 عضویت در {channel}", url=f"https://t.me/{channel.replace('@','')}")] for channel in CHANNEL_USERNAMES]
        await update.message.reply_text(
            "❌ برای استفاده از ربات، ابتدا در کانال‌های ما عضو شوید و سپس مجدد /start را بزنید.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        logger.debug(f"User {user_id} not a member of required channels")
        return

    await ensure_user(user_id, username, context.user_data.get("invited_by"))
    phone = await get_user_phone(user_id)
    if phone:
        await update.message.reply_text(
            "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} already has phone, showing main menu")
        return

    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس", request_contact=True)]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )
    await update.message.reply_text(
        "✅ لطفا شماره تماس خود را ارسال کنید.",
        reply_markup=contact_keyboard
    )
    user_states[user_id] = "awaiting_contact"
    logger.debug(f"User {user_id} prompted for contact")

import tempfile
import subprocess

# ---------- Admin Commands ----------
async def change_user_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /change_user_type")
        return
    await update.message.reply_text("لطفا ایدی عددی کاربر را وارد کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_user_id_for_type_change"
    logger.debug(f"User {update.effective_user.id} prompted for user_id to change type")

async def balance_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /balance_management")
        return
    try:
        users = await db_execute(
            "SELECT user_id, username, balance, is_agent FROM users ORDER BY created_at DESC",
            fetch=True
        )
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            logger.debug("No users found for balance management")
            return
        response = "📋 لیست کاربران و موجودی آن‌ها:\n\n"
        for user in users:
            user_id, username, balance, is_agent = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            account_type = "نماینده" if is_agent else "کاربر ساده"
            response += f"کاربر: {username_display}\nایدی: {user_id}\nموجودی: {balance:,} تومان\nنوع اکانت: {account_type}\n--------------------\n"
        await send_long_message(
            update.effective_user.id,
            response,
            context,
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("➕ افزایش موجودی"), KeyboardButton("➖ کاهش موجودی")],
                [KeyboardButton("⬅️ بازگشت به منو")]
            ], resize_keyboard=True)
        )
        user_states[update.effective_user.id] = "balance_management_menu"
        logger.debug(f"User {update.effective_user.id} viewed balance management menu")
    except Exception as e:
        logger.error(f"Error in balance_management: {e}")
        await update.message.reply_text("⚠️ خطایی در نمایش اطلاعات رخ داد.", reply_markup=get_main_keyboard())

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /backup")
        return
    try:
        await update.message.reply_text("🔄 در حال تهیه بکاپ از دیتابیس...")
        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tmp_file:
            backup_file = tmp_file.name
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name = parsed_url.path[1:]
        db_user = parsed_url.username
        db_password = parsed_url.password
        db_host = parsed_url.hostname
        db_port = parsed_url.port or 5432
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-f', backup_file,
            '-F', 'p'
        ]
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
            raise Exception(f"Backup failed: {error_msg}")
        with open(backup_file, 'rb') as file:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=file,
                filename=f"teazvpn_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
                caption="✅ بکاپ از دیتابیس با موفقیت تهیه شد."
            )
        os.unlink(backup_file)
        await update.message.reply_text("✅ بکاپ با موفقیت تهیه و ارسال شد.", reply_markup=get_main_keyboard())
        logger.debug(f"User {update.effective_user.id} created and sent backup")
    except Exception as e:
        logger.error(f"Error in backup command: {e}")
        await update.message.reply_text(f"⚠️ خطا در تهیه بکاپ: {str(e)}", reply_markup=get_main_keyboard())

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /restore")
        return
    await update.message.reply_text("📤 لطفا فایل بکاپ دیتابیس را ارسال کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_backup_file"
    logger.debug(f"User {update.effective_user.id} prompted for backup file")

async def restore_database_from_backup(file_path: str):
    try:
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name = parsed_url.path[1:]
        db_user = parsed_url.username
        db_password = parsed_url.password
        db_host = parsed_url.hostname
        db_port = parsed_url.port or 5432
        cmd = [
            'psql',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-f', file_path
        ]
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
            raise Exception(f"Restore failed: {error_msg}")
        logger.debug("Database restored successfully")
        return True, "✅ دیتابیس با موفقیت بازیابی شد."
    except Exception as e:
        logger.error(f"Error restoring database: {e}")
        return False, f"⚠️ خطا در بازیابی دیتابیس: {str(e)}"

async def notification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /notification")
        return
    await update.message.reply_text("📢 لطفا متن اطلاع‌رسانی را ارسال کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_notification_text"
    logger.debug(f"User {update.effective_user.id} prompted for notification text")

async def coupon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /coupon")
        return
    await update.message.reply_text("💵 مقدار تخفیف را به درصد وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_coupon_discount"
    logger.debug(f"User {update.effective_user.id} prompted for coupon discount")

async def numbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /numbers")
        return
    try:
        users = await db_execute(
            "SELECT user_id, username, phone FROM users ORDER BY created_at DESC",
            fetch=True
        )
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            logger.debug("No users found for numbers command")
            return
        response = "📞 لیست شماره‌های کاربران:\n\n"
        for user in users:
            user_id, username, phone = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            phone_display = phone if phone else "نامشخص"
            response += f"کاربر: {username_display}\nشماره: {phone_display}\n--------------------\n"
        await send_long_message(
            update.effective_user.id,
            response,
            context,
            reply_markup=get_main_keyboard()
        )
        logger.debug(f"User {update.effective_user.id} viewed user phone numbers")
    except Exception as e:
        logger.error(f"Error in numbers_command: {e}")
        await update.message.reply_text("⚠️ خطایی در نمایش شماره‌ها رخ داد.", reply_markup=get_main_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /stats")
        return
    try:
        total_users = await db_execute("SELECT COUNT(*) FROM users", fetchone=True)
        active_users = await db_execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE status = 'active' AND config IS NOT NULL", fetchone=True)
        inactive_users = total_users[0] - active_users[0] if total_users and active_users else 0
        today_users = await db_execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE", 
            fetchone=True
        )
        today_income = await db_execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved' AND created_at >= CURRENT_DATE",
            fetchone=True
        )
        month_income = await db_execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved' AND created_at >= DATE_TRUNC('month', CURRENT_DATE)",
            fetchone=True
        )
        total_income = await db_execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved'",
            fetchone=True
        )
        plan_stats = await db_execute(
            "SELECT plan, COUNT(*) as count FROM subscriptions WHERE config IS NOT NULL AND status = 'active' GROUP BY plan ORDER BY count DESC",
            fetch=True
        )
        best_selling_plan = plan_stats[0] if plan_stats else ("هیچ پلنی", 0)
        payment_methods = await db_execute(
            "SELECT payment_method, COUNT(*) as count FROM payments WHERE status = 'approved' GROUP BY payment_method",
            fetch=True
        )
        total_payments = sum([pm[1] for pm in payment_methods]) if payment_methods else 1
        payment_methods_percent = [
            (pm[0], round((pm[1] / total_payments) * 100, 1)) 
            for pm in payment_methods
            if pm[0] in ["card_to_card", "tron", "balance"]
        ] if payment_methods else [("کارت به کارت", 0), ("ترون", 0), ("موجودی", 0)]
        method_names = {
            "card_to_card": "🏦 کارت به کارت",
            "tron": "💎 ترون",
            "balance": "💰 موجودی"
        }
        total_subs = await db_execute("SELECT COUNT(*) FROM subscriptions", fetchone=True)
        active_subs = await db_execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND config IS NOT NULL", fetchone=True)
        pending_subs = await db_execute("SELECT COUNT(*) FROM payments WHERE status = 'pending' AND type = 'buy_subscription'", fetchone=True)
        total_transactions = await db_execute("SELECT COUNT(*) FROM payments", fetchone=True)
        invited_users = await db_execute("SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL", fetchone=True)
        
        stats_message = "🌟 گزارش عملکرد تیز VPN 🚀\n\n"
        stats_message += f"👥 کاربران:\nکل کاربران: {total_users[0] if total_users else 0:,} نفر 🧑‍💻\n"
        stats_message += f"کاربران فعال: {active_users[0] if active_users else 0:,} نفر ✅\n"
        stats_message += f"کاربران غیرفعال: {inactive_users:,} نفر ❎\n"
        stats_message += f"کاربران جدید امروز: {today_users[0] if today_users else 0:,} نفر 🆕\n"
        stats_message += f"کاربران دعوت‌شده: {invited_users[0] if invited_users else 0:,} نفر 🤝\n\n"
        stats_message += f"💸 درآمد:\nامروز: {today_income[0] if today_income else 0:,} تومان 💰\n"
        stats_message += f"این ماه: {month_income[0] if month_income else 0:,} تومان 📈\n"
        stats_message += f"کل درآمد: {total_income[0] if total_income else 0:,} تومان 🔥\n\n"
        stats_message += f"📦 اشتراک‌ها:\nکل اشتراک‌ها: {total_subs[0] if total_subs else 0:,} عدد 📋\n"
        stats_message += f"اشتراک‌های فعال: {active_subs[0] if active_subs else 0:,} عدد 🟢\n"
        stats_message += f"اشتراک‌های در انتظار: {pending_subs[0] if pending_subs else 0:,} عدد ⏳\n"
        stats_message += f"پرفروش‌ترین پلن: {best_selling_plan[0]} ({best_selling_plan[1]:,} عدد) 🏆\n\n"
        stats_message += "💳 روش‌های پرداخت:\n"
        for method, percent in payment_methods_percent:
            display_name = method_names.get(method, method)
            stats_message += f"  • {display_name}: {percent}% 💸\n"
        stats_message += f"کل تراکنش‌ها: {total_transactions[0] if total_transactions else 0:,} عدد 🔄\n"
        await update.message.reply_text(stats_message, reply_markup=get_main_keyboard())
        logger.debug(f"User {update.effective_user.id} viewed bot stats")
    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        await update.message.reply_text("⚠️ خطایی در نمایش آمار رخ داد.", reply_markup=get_main_keyboard())

async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /cleardb")
        return
    try:
        await db_execute("DELETE FROM coupons")
        await db_execute("DELETE FROM subscriptions")
        await db_execute("DELETE FROM payments")
        await db_execute("DELETE FROM users")
        logger.info("Database cleared successfully by admin")
        await update.message.reply_text("✅ دیتابیس با موفقیت پاک شد.", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Error clearing database: {e}")
        await update.message.reply_text(f"⚠️ خطا در پاک کردن دیتابیس: {str(e)}", reply_markup=get_main_keyboard())

async def debug_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        logger.debug(f"User {update.effective_user.id} denied access to /debug_subscriptions")
        return
    try:
        rows = await db_execute(
            """
            SELECT s.user_id, u.username, s.plan, s.payment_id, s.start_date, s.duration_days, s.status
            FROM subscriptions s
            LEFT JOIN users u ON s.user_id = u.user_id
            ORDER BY s.status DESC, s.start_date DESC
            """,
            fetch=True
        )
        if not rows:
            await update.message.reply_text("📂 هیچ اشتراکی برای هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            logger.debug("No subscriptions found for debug")
            return
        response = "📂 لیست تمام اشتراک‌های کاربران:\n\n"
        current_time = datetime.now()
        for row in rows:
            user_id, username, plan, payment_id, start_date, duration_days, status = row
            username_display = f"@{username}" if username else f"@{user_id}"
            start_date = start_date if start_date else current_time
            duration_days = duration_days if duration_days else 30
            remaining_days = max(0, (start_date + timedelta(days=duration_days) - current_time).days) if status == "active" else 0
            response += f"کاربر: {username_display}\nاشتراک: {plan}\nکد خرید: #{payment_id}\nوضعیت: {'فعال' if status == 'active' else 'غیرفعال'}\nزمان باقی‌مانده: {remaining_days} روز\n--------------------\n"
        await send_long_message(update.effective_user.id, response, context, reply_markup=get_main_keyboard())
        logger.debug(f"User {update.effective_user.id} viewed all subscriptions")
    except Exception as e:
        logger.error(f"Error in debug_subscriptions: {e}")
        await update.message.reply_text(f"⚠️ خطا در بررسی اشتراک‌ها: {str(e)}", reply_markup=get_main_keyboard())

# ---------- Handlers ----------
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Contact handler triggered for user_id: {user_id}")
    if user_states.get(user_id) != "awaiting_contact":
        logger.debug(f"User {user_id} not in awaiting_contact state")
        return
    contact = update.message.contact
    if contact is None or contact.user_id != user_id:
        await update.message.reply_text("⚠️ لطفا شماره تماس خود را از طریق دکمه ارسال کنید.")
        logger.debug(f"User {user_id} sent invalid contact")
        return
    phone_number = contact.phone_number
    await save_user_phone(user_id, phone_number)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📞 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) شماره تماس خود را ارسال کرد:\n{phone_number}"
    )
    row = await db_execute("SELECT invited_by FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    invited_by = row[0] if row and row[0] else None
    if invited_by and invited_by != user_id:
        inviter_exists = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (invited_by,), fetchone=True)
        if inviter_exists:
            await context.bot.send_message(
                chat_id=invited_by,
                text=f"🎉 دوست شما (@{update.effective_user.username or 'NoUsername'}) با موفقیت مراحل ثبت‌نام را تکمیل کرد!\n💰 ۲۵,۰۰۰ تومان به موجودی شما اضافه شد."
            )
            await add_balance(invited_by, 25000)
    await update.message.reply_text(
        "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)
    logger.debug(f"User {user_id} registered phone and shown main menu")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    logger.debug(f"Message received from user_id: {user_id}, text: {text}")

    # Handle /start explicitly in case it's sent as text
    if text == "/start":
        logger.debug(f"Processing /start as text for user_id: {user_id}")
        await start(update, context)
        return

    # Check channel membership
    if not await is_user_member(user_id):
        kb = [[InlineKeyboardButton(f"📢 عضویت در {channel}", url=f"https://t.me/{channel.replace('@','')}")] for channel in CHANNEL_USERNAMES]
        await update.message.reply_text(
            "❌ برای استفاده از ربات، ابتدا در کانال‌های ما عضو شوید و سپس مجدد /start را بزنید.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        logger.debug(f"User {user_id} not a member of required channels")
        return

    # Handle return to main menu
    if text in ["بازگشت به منو", "⬅️ بازگشت به منو"]:
        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} returned to main menu")
        return

    # Handle main menu options
    if text == "💰 موجودی":
        await update.message.reply_text("💰 بخش موجودی:\nیک گزینه را انتخاب کنید:", reply_markup=get_balance_keyboard())
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} selected balance menu")
        return

    if text == "نمایش موجودی":
        bal = await get_balance(user_id)
        await update.message.reply_text(f"💰 موجودی شما: {bal} تومان", reply_markup=get_balance_keyboard())
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} checked balance: {bal}")
        return

    if text == "افزایش موجودی":
        await update.message.reply_text("💳 لطفا مبلغ واریزی را به تومان وارد کنید (مثال: 90000):", reply_markup=get_back_keyboard())
        user_states[user_id] = "awaiting_deposit_amount"
        logger.debug(f"User {user_id} prompted for deposit amount")
        return

    if text == "💳 خرید اشتراک":
        is_agent = await is_user_agent(user_id)
        await update.message.reply_text("💳 پلن را انتخاب کنید:", reply_markup=get_subscription_keyboard(is_agent))
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} selected purchase subscription")
        return

    if text in [
        "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره",
        "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره",
        "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره",
        "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره",
        "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره",
        "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره"
    ]:
        mapping = {
            "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": (90000, 0),
            "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": (250000, 1),
            "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": (450000, 2),
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (70000, 0),
            "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (210000, 1),
            "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (380000, 2)
        }
        amount, plan_index = mapping.get(text, (0, -1))
        if plan_index == -1:
            await update.message.reply_text("⚠️ خطا در انتخاب پلن. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} sent invalid plan: {text}")
            return
        is_agent = await is_user_agent(user_id)
        if not is_agent:
            await update.message.reply_text(
                f"💵 اگر کد تخفیف دارید، وارد کنید. در غیر این صورت برای ادامه روی 'ادامه' کلیک کنید:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
            )
            user_states[user_id] = f"awaiting_coupon_code_{amount}_{text}"
            logger.debug(f"User {user_id} prompted for coupon code for plan: {text}")
        else:
            user_states[user_id] = f"awaiting_payment_method_{amount}_{text}"
            await update.message.reply_text("💳 روش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
            logger.debug(f"User {user_id} (agent) prompted for payment method for plan: {text}")
        return

    if text == "🎁 اشتراک تست رایگان":
        await update.message.reply_text(
            f"🎁 برای دریافت اشتراک تست رایگان، لطفا با پشتیبانی تماس بگیرید: https://t.me/{SUPPORT_ID}",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} requested free trial")
        return

    if text == "☎️ پشتیبانی":
        await update.message.reply_text(
            f"📞 برای پشتیبانی، لطفا با ادمین تماس بگیرید: https://t.me/{SUPPORT_ID}",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} requested support")
        return

    if text == "💵 اعتبار رایگان":
        await update.message.reply_text(
            f"🎉 برای دریافت اعتبار رایگان، لینک دعوت خود را برای دوستانتان بفرستید!\n"
            f"لینک شما: https://t.me/teaz_vpn_bot?start={user_id}\n"
            "با هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه می‌شود.",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} requested referral link")
        return

    if text == "📂 اشتراک‌های من":
        subscriptions = await get_user_subscriptions(user_id)
        if not subscriptions:
            await update.message.reply_text("📂 شما هیچ اشتراکی ندارید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} has no subscriptions")
            return
        response = "📋 اشتراک‌های شما:\n\n"
        for sub in subscriptions:
            status = "فعال" if sub["status"] == "active" else "غیرفعال"
            end_date = sub["end_date"].strftime("%Y-%m-%d %H:%M:%S") if sub["end_date"] else "نامشخص"
            remaining_days = max(0, (sub["end_date"] - datetime.now()).days) if sub["status"] == "active" else 0
            response += f"اشتراک: {sub['plan']}\nکد خرید: #{sub['payment_id']}\nوضعیت: {status}\nتاریخ پایان: {end_date}\nزمان باقی‌مانده: {remaining_days} روز\n"
            if sub["config"]:
                response += f"کانفیگ:\n```\n{sub['config']}\n```\n"
            response += "--------------------\n"
        await send_long_message(user_id, response, context, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        user_states.pop(user_id, None)
        logger.debug(f"User {user_id} viewed subscriptions")
        return

    if text == "💡 راهنمای اتصال":
        await update.message.reply_text(
            "📚 لطفا سیستم‌عامل خود را انتخاب کنید:",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states[user_id] = "awaiting_connection_guide"
        logger.debug(f"User {user_id} requested connection guide")
        return

    if text == "🧑‍💼 درخواست نمایندگی":
        await update.message.reply_text(
            "🌟 با دریافت نمایندگی رسمی تیز VPN، از تخفیف‌های ویژه برخوردار شوید!\n"
            "شرایط دریافت نمایندگی:\n"
            "۱. خرید حداقل ۵ اکانت در ماه\n"
            "۲. پشتیبانی مشتریان معرفی‌شده\n"
            "مزایا:\n"
            "- تخفیف ۲۰٪ روی همه پلن‌ها\n"
            "- اولویت در دریافت کانفیگ‌های جدید\n"
            f"لطفا مبلغ ۵۰۰,۰۰۰ تومان را واریز کنید و فیش را ارسال کنید:\n\n"
            f"🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ\n\n"
            f"💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`",
            reply_markup=get_back_keyboard(),
            parse_mode="MarkdownV2"
        )
        payment_id = await add_payment(user_id, 500000, "agency_request", "card_to_card")
        if payment_id:
            user_states[user_id] = f"awaiting_agency_receipt_{payment_id}"
            logger.debug(f"User {user_id} requested agency, payment_id: {payment_id}")
        else:
            await update.message.reply_text("⚠️ خطا در ثبت درخواست. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            logger.error(f"Failed to add agency payment for user_id {user_id}")
        return

    # Handle state-based inputs
    state = user_states.get(user_id)
    if state == "awaiting_contact":
        contact_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("ارسال شماره تماس", request_contact=True)]], 
            resize_keyboard=True, 
            one_time_keyboard=True
        )
        await update.message.reply_text(
            "⚠️ لطفا ابتدا شماره تماس خود را از طریق دکمه ارسال کنید.",
            reply_markup=contact_keyboard
        )
        logger.debug(f"User {user_id} prompted for contact again")
        return

    if state == "awaiting_deposit_amount":
        if text.isdigit():
            amount = int(text)
            payment_id = await add_payment(user_id, amount, "increase_balance", "card_to_card")
            if payment_id:
                await update.message.reply_text(
                    f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n"
                    f"💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`\n\n"
                    f"یا\n\n🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ",
                    reply_markup=get_back_keyboard(),
                    parse_mode="MarkdownV2"
                )
                user_states[user_id] = f"awaiting_deposit_receipt_{payment_id}"
                logger.debug(f"User {user_id} submitted deposit amount: {amount}, payment_id: {payment_id}")
            else:
                await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.error(f"Failed to add payment for user_id {user_id}")
        else:
            await update.message.reply_text("⚠️ لطفا عدد وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid deposit amount")
        return

    if state and (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_") or state.startswith("awaiting_agency_receipt_")):
        try:
            payment_id = int(state.split("_")[-1])
            payment = await db_execute("SELECT amount, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                amount, ptype, description = payment
                caption = f"💳 فیش پرداختی از کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}):\n"
                caption += f"مبلغ: {amount}\nنوع: {ptype if ptype != 'agency_request' else 'درخواست نمایندگی'}"
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ تایید", callback_data=f"approve_{payment_id}"),
                        InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
                    ]
                ])
                if update.message.photo:
                    file_id = update.message.photo[-1].file_id
                    await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, reply_markup=keyboard)
                elif update.message.document:
                    doc_id = update.message.document.file_id
                    await context.bot.send_document(chat_id=ADMIN_ID, document=doc_id, caption=caption, reply_markup=keyboard)
                else:
                    await update.message.reply_text("⚠️ لطفا یک عکس یا سند معتبر ارسال کنید.", reply_markup=get_back_keyboard())
                    logger.debug(f"User {user_id} sent invalid receipt")
                    return
                await update.message.reply_text("✅ فیش شما برای ادمین ارسال شد، لطفا منتظر تایید باشید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent receipt for payment_id: {payment_id}")
            else:
                await update.message.reply_text("⚠️ پرداخت یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.error(f"Payment {payment_id} not found for user_id {user_id}")
        except Exception as e:
            logger.error(f"Error processing receipt for user_id {user_id}: {e}")
            await update.message.reply_text("⚠️ خطا در پردازش فیش.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        return

    if state and state.startswith("awaiting_config_"):
        try:
            payment_id = int(state.split("_")[-1])
            payment = await db_execute("SELECT user_id, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, description = payment
                if update.message.text:
                    config = update.message.text
                    await update_subscription_config(payment_id, config)
                    await context.bot.send_message(
                        chat_id=buyer_id,
                        text=f"✅ کانفیگ اشتراک شما ({description})\nکد خرید: #{payment_id}\nدریافت شد:\n```\n{config}\n```",
                        parse_mode="Markdown"
                    )
                    await update.message.reply_text("✅ کانفیگ با موفقیت به خریدار ارسال شد.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} sent config for payment_id: {payment_id}")
                else:
                    await update.message.reply_text("⚠️ لطفا کانفیگ را به صورت متن ارسال کنید.", reply_markup=get_back_keyboard())
                    logger.debug(f"User {user_id} sent invalid config format")
            else:
                await update.message.reply_text("⚠️ پرداخت یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.error(f"Payment {payment_id} not found for user_id {user_id}")
        except Exception as e:
            logger.error(f"Error processing config for user_id {user_id}: {e}")
            await update.message.reply_text("⚠️ خطا در پردازش کانفیگ.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        return

    if state == "awaiting_connection_guide":
        guides = {
            "📗 اندروید": "📱 راهنمای اتصال برای اندروید:\n۱. اپلیکیشن v2rayNG را از گوگل پلی یا کافه بازار نصب کنید.\n۲. کانفیگ خود را در اپلیکیشن وارد کنید.\n۳. روی دکمه اتصال کلیک کنید.\nلینک دانلود: https://play.google.com/store/apps/details?id=com.v2ray.ang",
            "📕 آیفون/مک": "🍎 راهنمای اتصال برای iOS/Mac:\n۱. اپلیکیشن Shadowrocket یا Fair VPN را نصب کنید.\n۲. کانفیگ را در اپلیکیشن وارد کنید.\n۳. اتصال را فعال کنید.\nلینک دانلود Shadowrocket: https://apps.apple.com/us/app/shadowrocket/id932747118",
            "📘 ویندوز": "💻 راهنمای اتصال برای ویندوز:\n۱. کلاینت v2rayN را دانلود کنید.\n۲. کانفیگ را در برنامه وارد کنید.\n۳. اتصال را برقرار کنید.\nلینک دانلود: https://github.com/2dust/v2rayN/releases",
            "📙 لینوکس": "🐧 راهنمای اتصال برای لینوکس:\n۱. پکیج v2ray را نصب کنید.\n۲. کانفیگ را در فایل تنظیمات وارد کنید.\n۳. سرویس v2ray را اجرا کنید.\nدستور نصب: `sudo apt install v2ray`"
        }
        if text in guides:
            await update.message.reply_text(guides[text], reply_markup=get_main_keyboard(), parse_mode="Markdown")
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} received connection guide for {text}")
        else:
            await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=get_connection_guide_keyboard())
            logger.debug(f"User {user_id} sent invalid connection guide option")
        return

    if state == "awaiting_backup_file" and user_id == ADMIN_ID:
        if update.message.document:
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tmp_file:
                    backup_file = tmp_file.name
                await file.download_to_drive(backup_file)
                await update.message.reply_text("🔄 در حال بازیابی دیتابیس...")
                success, message = await restore_database_from_backup(backup_file)
                os.unlink(backup_file)
                await update.message.reply_text(message, reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} restored database")
            except Exception as e:
                logger.error(f"Error in restore process for user_id {user_id}: {e}")
                await update.message.reply_text(f"⚠️ خطا در بازیابی دیتابیس: {str(e)}", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
        else:
            await update.message.reply_text("⚠️ لطفا یک فایل بکاپ ارسال کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid backup file")
        return

    if state == "awaiting_user_id_for_type_change" and user_id == ADMIN_ID:
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute("SELECT username, is_agent FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if not user:
                await update.message.reply_text("⚠️ کاربری با این ایدی یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent invalid target user_id: {target_user_id}")
                return
            username, is_agent = user
            new_type = "کاربر ساده" if is_agent else "نماینده"
            await set_user_agent(target_user_id, not is_agent)
            await update.message.reply_text(
                f"✅ نوع کاربری @{username or target_user_id} به {new_type} تغییر کرد.",
                reply_markup=get_main_keyboard()
            )
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} changed type for user_id {target_user_id} to {new_type}")
        else:
            await update.message.reply_text("⚠️ لطفا ایدی عددی معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid user_id for type change")
        return

    if state == "balance_management_menu" and user_id == ADMIN_ID:
        if text == "➕ افزایش موجودی":
            await update.message.reply_text("لطفا ایدی عددی کاربر را وارد کنید:", reply_markup=get_back_keyboard())
            user_states[user_id] = "awaiting_user_id_for_balance_increase"
            logger.debug(f"User {user_id} selected increase balance")
            return
        elif text == "➖ کاهش موجودی":
            await update.message.reply_text("لطفا ایدی عددی کاربر را وارد کنید:", reply_markup=get_back_keyboard())
            user_states[user_id] = "awaiting_user_id_for_balance_decrease"
            logger.debug(f"User {user_id} selected decrease balance")
            return
        else:
            await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} sent invalid balance management option")
            return

    if state == "awaiting_user_id_for_balance_increase" and user_id == ADMIN_ID:
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute("SELECT username FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if not user:
                await update.message.reply_text("⚠️ کاربری با این ایدی یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent invalid user_id for balance increase: {target_user_id}")
                return
            await update.message.reply_text(
                f"لطفا مقدار افزایش موجودی (به تومان) برای کاربر @{user[0] or target_user_id} را وارد کنید:",
                reply_markup=get_back_keyboard()
            )
            user_states[user_id] = f"awaiting_balance_amount_increase_{target_user_id}"
            logger.debug(f"User {user_id} prompted for balance increase amount for user_id {target_user_id}")
        else:
            await update.message.reply_text("⚠️ لطفا ایدی عددی معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid user_id for balance increase")
        return

    if state == "awaiting_user_id_for_balance_decrease" and user_id == ADMIN_ID:
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute("SELECT username FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if not user:
                await update.message.reply_text("⚠️ کاربری با این ایدی یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent invalid user_id for balance decrease: {target_user_id}")
                return
            await update.message.reply_text(
                f"لطفا مقدار کاهش موجودی (به تومان) برای کاربر @{user[0] or target_user_id} را وارد کنید:",
                reply_markup=get_back_keyboard()
            )
            user_states[user_id] = f"awaiting_balance_amount_decrease_{target_user_id}"
            logger.debug(f"User {user_id} prompted for balance decrease amount for user_id {target_user_id}")
        else:
            await update.message.reply_text("⚠️ لطفا ایدی عددی معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid user_id for balance decrease")
        return

    if state and state.startswith("awaiting_balance_amount_increase_") and user_id == ADMIN_ID:
        if text.isdigit():
            amount = int(text)
            if amount > 0:
                target_user_id = int(state.split("_")[-1])
                await add_balance(target_user_id, amount)
                user = await db_execute("SELECT username FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
                await update.message.reply_text(
                    f"✅ موجودی کاربر @{user[0] or target_user_id} به مقدار {amount:,} تومان افزایش یافت.",
                    reply_markup=get_main_keyboard()
                )
                await context.bot.send_message(
                    target_user_id,
                    f"💰 موجودی شما به مقدار {amount:,} تومان افزایش یافت."
                )
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} increased balance for user_id {target_user_id} by {amount}")
            else:
                await update.message.reply_text("⚠️ مقدار باید بیشتر از صفر باشد.", reply_markup=get_back_keyboard())
                logger.debug(f"User {user_id} sent invalid balance increase amount")
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid balance increase amount format")
        return

    if state and state.startswith("awaiting_balance_amount_decrease_") and user_id == ADMIN_ID:
        if text.isdigit():
            amount = int(text)
            if amount > 0:
                target_user_id = int(state.split("_")[-1])
                current_balance = await get_balance(target_user_id)
                if current_balance >= amount:
                    await deduct_balance(target_user_id, amount)
                    user = await db_execute("SELECT username FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
                    await update.message.reply_text(
                        f"✅ موجودی کاربر @{user[0] or target_user_id} به مقدار {amount:,} تومان کاهش یافت.",
                        reply_markup=get_main_keyboard()
                    )
                    await context.bot.send_message(
                        target_user_id,
                        f"💰 موجودی شما به مقدار {amount:,} تومان کاهش یافت."
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} decreased balance for user_id {target_user_id} by {amount}")
                else:
                    await update.message.reply_text(
                        f"⚠️ موجودی کاربر کافی نیست (موجودی فعلی: {current_balance:,} تومان).",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} attempted to decrease balance for user_id {target_user_id} with insufficient funds")
            else:
                await update.message.reply_text("⚠️ مقدار باید بیشتر از صفر باشد.", reply_markup=get_back_keyboard())
                logger.debug(f"User {user_id} sent invalid balance decrease amount")
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid balance decrease amount format")
        return

    if state == "awaiting_coupon_discount" and user_id == ADMIN_ID:
        if text.isdigit():
            discount_percent = int(text)
            if 0 < discount_percent <= 100:
                coupon_code = generate_coupon_code()
                user_states[user_id] = f"awaiting_coupon_recipient_{coupon_code}_{discount_percent}"
                await update.message.reply_text(
                    f"💵 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف ایجاد شد.\nبرای چه کسانی ارسال شود؟",
                    reply_markup=get_coupon_recipient_keyboard(),
                    parse_mode="Markdown"
                )
                logger.debug(f"User {user_id} created coupon {coupon_code} with {discount_percent}% discount")
            else:
                await update.message.reply_text("⚠️ درصد تخفیف باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
                logger.debug(f"User {user_id} sent invalid coupon discount percent")
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid coupon discount format")
        return

    if state and state.startswith("awaiting_coupon_recipient_") and user_id == ADMIN_ID:
        parts = state.split("_")
        coupon_code = parts[3]
        discount_percent = int(parts[4])
        if text == "📢 برای همه":
            try:
                await create_coupon(coupon_code, discount_percent)
                users = await db_execute("SELECT user_id FROM users WHERE is_agent = FALSE", fetch=True)
                if not users:
                    await update.message.reply_text(
                        "⚠️ هیچ کاربری (غیر از نمایندگان) یافت نشد.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} found no non-agent users for coupon")
                    return
                sent_count = 0
                for user in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user[0],
                            text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.",
                            parse_mode="Markdown"
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Error sending coupon to user_id {user[0]}: {e}")
                        continue
                await update.message.reply_text(
                    f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر (غیر از نمایندگان) ارسال شد.",
                    reply_markup=get_main_keyboard(),
                    parse_mode="Markdown"
                )
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent coupon {coupon_code} to {sent_count} users")
            except Exception as e:
                logger.error(f"Error sending coupons to all users: {e}")
                await update.message.reply_text(
                    "⚠️ خطا در ارسال کد تخفیف برای همه کاربران.",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
            return
        elif text == "👤 برای یک نفر":
            await update.message.reply_text(
                "لطفا آیدی عددی کاربر را وارد کنید:",
                reply_markup=get_back_keyboard()
            )
            user_states[user_id] = f"awaiting_coupon_user_id_{coupon_code}_{discount_percent}"
            logger.debug(f"User {user_id} prompted for coupon recipient user_id")
            return
        elif text == "🎯 درصد خاصی از کاربران":
            user_states[user_id] = f"awaiting_coupon_percent_{coupon_code}_{discount_percent}"
            await update.message.reply_text("📊 درصد کاربران را وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} prompted for coupon percentage")
            return
        else:
            await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=get_coupon_recipient_keyboard())
            logger.debug(f"User {user_id} sent invalid coupon recipient option")
            return

    if state and state.startswith("awaiting_coupon_user_id_") and user_id == ADMIN_ID:
        parts = state.split("_")
        coupon_code = parts[3]
        discount_percent = int(parts[4])
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute(
                "SELECT user_id, is_agent FROM users WHERE user_id = %s",
                (target_user_id,), fetchone=True
            )
            if user:
                _, is_agent = user
                if is_agent:
                    await update.message.reply_text(
                        "⚠️ این کاربر نماینده است و نمی‌تواند کد تخفیف دریافت کند.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} attempted to send coupon to agent user_id {target_user_id}")
                    return
                await create_coupon(coupon_code, discount_percent, target_user_id)
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.",
                    parse_mode="Markdown"
                )
                await update.message.reply_text(
                    f"✅ کد تخفیف `{coupon_code}` برای کاربر با ID {target_user_id} ارسال شد.",
                    reply_markup=get_main_keyboard(),
                    parse_mode="Markdown"
                )
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent coupon {coupon_code} to user_id {target_user_id}")
            else:
                await update.message.reply_text(
                    f"⚠️ کاربری با ID {target_user_id} یافت نشد.",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent invalid user_id for coupon: {target_user_id}")
            return
        else:
            await update.message.reply_text("⚠️ لطفا ایدی عددی معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid user_id for coupon")
        return

    if state and state.startswith("awaiting_coupon_percent_") and user_id == ADMIN_ID:
        parts = state.split("_")
        coupon_code = parts[3]
        discount_percent = int(parts[4])
        if text.isdigit():
            percent = int(text)
            if 0 < percent <= 100:
                try:
                    users = await db_execute("SELECT user_id FROM users WHERE is_agent = FALSE", fetch=True)
                    if not users:
                        await update.message.reply_text(
                            "⚠️ هیچ کاربری (غیر از نمایندگان) یافت نشد.",
                            reply_markup=get_main_keyboard()
                        )
                        user_states.pop(user_id, None)
                        logger.debug(f"User {user_id} found no non-agent users for coupon percentage")
                        return
                    total_users = len(users)
                    num_users = max(1, round(total_users * (percent / 100)))
                    selected_users = random.sample(users, min(num_users, total_users))
                    await create_coupon(coupon_code, discount_percent)
                    sent_count = 0
                    for user in selected_users:
                        try:
                            await context.bot.send_message(
                                chat_id=user[0],
                                text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.",
                                parse_mode="Markdown"
                            )
                            sent_count += 1
                        except Exception as e:
                            logger.error(f"Error sending coupon to user_id {user[0]}: {e}")
                            continue
                    await update.message.reply_text(
                        f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر ({percent}% از کاربران غیر نماینده) ارسال شد.",
                        reply_markup=get_main_keyboard(),
                        parse_mode="Markdown"
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} sent coupon {coupon_code} to {sent_count} users")
                except Exception as e:
                    logger.error(f"Error sending coupons to {percent}% of users: {e}")
                    await update.message.reply_text(
                        "⚠️ خطا در ارسال کد تخفیف برای درصد مشخصی از کاربران.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
            else:
                await update.message.reply_text("⚠️ درصد باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
                logger.debug(f"User {user_id} sent invalid coupon percentage")
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            logger.debug(f"User {user_id} sent invalid coupon percentage format")
        return

    if state and state.startswith("awaiting_coupon_code_"):
        parts = state.split("_")
        amount = int(parts[3])
        plan = "_".join(parts[4:])
        if text == "ادامه":
            user_states[user_id] = f"awaiting_payment_method_{amount}_{plan}"
            await update.message.reply_text("💳 روش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
            logger.debug(f"User {user_id} continued to payment method for plan: {plan}")
            return
        coupon_code = text.strip()
        discount_percent, error = await validate_coupon(coupon_code, user_id)
        if error:
            await update.message.reply_text(
                f"⚠️ {error}\nلطفا کد معتبر وارد کنید یا برای ادامه روی 'ادامه' کلیک کنید:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
            )
            logger.debug(f"User {user_id} sent invalid coupon code: {coupon_code}")
            return
        discounted_amount = int(amount * (1 - discount_percent / 100))
        user_states[user_id] = f"awaiting_payment_method_{discounted_amount}_{plan}_{coupon_code}"
        await update.message.reply_text(
            f"✅ کد تخفیف اعمال شد! مبلغ با {discount_percent}% تخفیف: {discounted_amount} تومان\nروش خرید را انتخاب کنید:",
            reply_markup=get_payment_method_keyboard()
        )
        logger.debug(f"User {user_id} applied coupon {coupon_code} with {discount_percent}% discount")
        return

    if state and state.startswith("awaiting_payment_method_"):
        try:
            parts = state.split("_")
            amount = int(parts[3])
            plan = "_".join(parts[4:]) if len(parts) <= 5 else "_".join(parts[4:-1])
            coupon_code = parts[-1] if len(parts) > 5 else None
            
            if text == "🏦 کارت به کارت":
                payment_id = await add_payment(user_id, amount, "buy_subscription", "card_to_card", description=plan, coupon_code=coupon_code)
                if payment_id:
                    await add_subscription(user_id, payment_id, plan)
                    await update.message.reply_text(
                        f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n"
                        f"🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ",
                        reply_markup=get_back_keyboard(),
                        parse_mode="MarkdownV2"
                    )
                    user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
                    logger.debug(f"User {user_id} selected card payment, payment_id: {payment_id}")
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    logger.error(f"Failed to add card payment for user_id {user_id}")
                return

            if text == "💎 پرداخت با ترون":
                payment_id = await add_payment(user_id, amount, "buy_subscription", "tron", description=plan, coupon_code=coupon_code)
                if payment_id:
                    await add_subscription(user_id, payment_id, plan)
                    await update.message.reply_text(
                        f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n"
                        f"💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`",
                        reply_markup=get_back_keyboard(),
                        parse_mode="MarkdownV2"
                    )
                    user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
                    logger.debug(f"User {user_id} selected TRON payment, payment_id: {payment_id}")
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    logger.error(f"Failed to add TRON payment for user_id {user_id}")
                return

            if text == "💰 پرداخت با موجودی":
                balance = await get_balance(user_id)
                if balance >= amount:
                    payment_id = await add_payment(user_id, amount, "buy_subscription", "balance", description=plan, coupon_code=coupon_code)
                    if payment_id:
                        await add_subscription(user_id, payment_id, plan)
                        await deduct_balance(user_id, amount)
                        await update_payment_status(payment_id, "approved")
                        await update.message.reply_text(
                            "✅ خرید شما با موفقیت انجام شد. حداکثر تا ۱ ساعت دیگر کانفیگ برای شما ارسال خواهد شد.",
                            reply_markup=get_main_keyboard()
                        )
                        await context.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"📢 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) با موجودی خود سرویس {plan} خریداری کرد."
                        )
                        config_keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"send_config_{payment_id}")]
                        ])
                        await context.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"✅ پرداخت برای اشتراک ({plan}) تایید شد.",
                            reply_markup=config_keyboard
                        )
                        user_states.pop(user_id, None)
                        logger.debug(f"User {user_id} paid with balance, payment_id: {payment_id}")
                    else:
                        await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                        user_states.pop(user_id, None)
                        logger.error(f"Failed to add balance payment for user_id {user_id}")
                else:
                    await update.message.reply_text(
                        f"⚠️ موجودی شما ({balance} تومان) کافی نیست. لطفا ابتدا موجودی خود را افزایش دهید.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} has insufficient balance: {balance}")
                return

        except Exception as e:
            logger.error(f"Error processing payment method for user_id {user_id}, state: {state}: {e}")
            await update.message.reply_text("⚠️ خطا در پردازش. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        return

    if state == "awaiting_notification_text" and user_id == ADMIN_ID:
        notification_text = text
        await update.message.reply_text(
            "📢 آیا مطمئن هستید که می‌خواهید این اطلاعیه را برای همه کاربران ارسال کنید؟",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✅ بله، ارسال کن")],
                [KeyboardButton("❌ خیر، انصراف")]
            ], resize_keyboard=True)
        )
        user_states[user_id] = f"confirm_notification_{notification_text}"
        logger.debug(f"User {user_id} submitted notification text")
        return

    if state and state.startswith("confirm_notification_") and user_id == ADMIN_ID:
        notification_text = state.split("_", 2)[2]
        if text == "✅ بله، ارسال کن":
            try:
                users = await db_execute("SELECT user_id FROM users", fetch=True)
                if not users:
                    await update.message.reply_text(
                        "⚠️ هیچ کاربری یافت نشد.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                    logger.debug(f"User {user_id} found no users for notification")
                    return
                
                sent_count = 0
                failed_count = 0
                for user in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user[0],
                            text=f"📢 اطلاعیه از مدیریت:\n\n{notification_text}"
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Error sending notification to user_id {user[0]}: {e}")
                        failed_count += 1
                        continue
                
                await update.message.reply_text(
                    f"✅ اطلاعیه با موفقیت به {sent_count} کاربر ارسال شد.\n"
                    f"❌ تعداد کاربرانی که دریافت نکردند: {failed_count}",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
                logger.debug(f"User {user_id} sent notification to {sent_count} users")
            except Exception as e:
                logger.error(f"Error sending notifications: {e}")
                await update.message.reply_text(
                    "⚠️ خطا در ارسال اطلاعیه به کاربران.",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
        else:
            await update.message.reply_text(
                "❌ ارسال اطلاعیه لغو شد.",
                reply_markup=get_main_keyboard()
            )
            user_states.pop(user_id, None)
            logger.debug(f"User {user_id} canceled notification")
        return

    # Default response for unrecognized input
    await update.message.reply_text("⚠️ لطفا از منو انتخاب کنید.", reply_markup=get_main_keyboard())
    logger.debug(f"User {user_id} sent unrecognized input: {text}")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    logger.debug(f"Callback query received from user_id: {user_id}, data: {data}")

    if user_id != ADMIN_ID:
        await query.message.reply_text("⚠️ شما اجازه دسترسی به این عملیات را ندارید.")
        logger.debug(f"User {user_id} denied access to callback query")
        return

    if data.startswith("approve_"):
        try:
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, amount, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, amount, ptype, description = payment
                await update_payment_status(payment_id, "approved")
                if ptype == "increase_balance":
                    await add_balance(buyer_id, amount)
                    await context.bot.send_message(
                        chat_id=buyer_id,
                        text=f"✅ پرداخت شما ({amount} تومان) تایید شد و به موجودی شما اضافه شد."
                    )
                    await query.message.reply_text("✅ پرداخت تایید شد و موجودی کاربر افزایش یافت.", reply_markup=get_main_keyboard())
                    logger.debug(f"User {user_id} approved payment {payment_id} for balance increase")
                elif ptype == "buy_subscription":
                    config_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"send_config_{payment_id}")]
                    ])
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"✅ پرداخت برای اشتراک ({description}) تایید شد.",
                        reply_markup=config_keyboard
                    )
                    await context.bot.send_message(
                        chat_id=buyer_id,
                        text=f"✅ پرداخت شما برای اشتراک ({description}) تایید شد. حداکثر تا ۱ ساعت دیگر کانفیگ برای شما ارسال خواهد شد."
                    )
                    await query.message.reply_text("✅ پرداخت تایید شد.", reply_markup=get_main_keyboard())
                    logger.debug(f"User {user_id} approved payment {payment_id} for subscription")
                elif ptype == "agency_request":
                    await set_user_agent(buyer_id, True)
                    await context.bot.send_message(
                        chat_id=buyer_id,
                        text="🎉 درخواست نمایندگی شما تایید شد! از این پس می‌توانید از تخفیف‌های ویژه نمایندگان استفاده کنید."
                    )
                    await query.message.reply_text("✅ درخواست نمایندگی تایید شد.", reply_markup=get_main_keyboard())
                    logger.debug(f"User {user_id} approved payment {payment_id} for agency request")
            else:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.", reply_markup=get_main_keyboard())
                logger.error(f"Payment {payment_id} not found for approval by user_id {user_id}")
        except Exception as e:
            logger.error(f"Error approving payment {payment_id}: {e}")
            await query.message.reply_text(f"⚠️ خطا در تایید پرداخت: {str(e)}", reply_markup=get_main_keyboard())
        return

    if data.startswith("reject_"):
        try:
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, ptype, description = payment
                await update_payment_status(payment_id, "rejected")
                await context.bot.send_message(
                    chat_id=buyer_id,
                    text=f"❌ پرداخت شما برای {'اشتراک ' + description if ptype == 'buy_subscription' else 'افزایش موجودی' if ptype == 'increase_balance' else 'درخواست نمایندگی'} رد شد. لطفا با پشتیبانی تماس بگیرید: https://t.me/{SUPPORT_ID}"
                )
                await query.message.reply_text("❌ پرداخت رد شد.", reply_markup=get_main_keyboard())
                logger.debug(f"User {user_id} rejected payment {payment_id}")
            else:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.", reply_markup=get_main_keyboard())
                logger.error(f"Payment {payment_id} not found for rejection by user_id {user_id}")
        except Exception as e:
            logger.error(f"Error rejecting payment {payment_id}: {e}")
            await query.message.reply_text(f"⚠️ خطا در رد پرداخت: {str(e)}", reply_markup=get_main_keyboard())
        return

    if data.startswith("send_config_"):
        try:
            payment_id = int(data.split("_")[2])
            payment = await db_execute("SELECT user_id, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, description = payment
                await query.message.reply_text(
                    f"لطفا کانفیگ برای اشتراک ({description}) را ارسال کنید:",
                    reply_markup=get_back_keyboard()
                )
                user_states[user_id] = f"awaiting_config_{payment_id}"
                logger.debug(f"User {user_id} prompted to send config for payment_id {payment_id}")
            else:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.", reply_markup=get_main_keyboard())
                logger.error(f"Payment {payment_id} not found for config send by user_id {user_id}")
        except Exception as e:
            logger.error(f"Error initiating config send for payment {payment_id}: {e}")
            await query.message.reply_text(f"⚠️ خطا در ارسال کانفیگ: {str(e)}", reply_markup=get_main_keyboard())
        return

# ---------- Error Handler ----------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ خطایی رخ داد. لطفا دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.", reply_markup=get_main_keyboard())

# ---------- FastAPI Webhook Endpoint ----------
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        update = Update.de_json(await request.json(), application.bot)
        if update:
            await application.process_update(update)
            logger.debug("Webhook processed update successfully")
        else:
            logger.warning("Received invalid update in webhook")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return {"status": "error"}

# ---------- Startup and Shutdown ----------
async def on_startup():
    try:
        init_db_pool()
        await create_tables()
        await application.initialize()
        await application.bot.set_webhook(url=WEBHOOK_URL)
        await set_bot_commands()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        application.add_handler(CallbackQueryHandler(callback_query_handler))
        application.add_handler(CommandHandler("change_user_type", change_user_type))
        application.add_handler(CommandHandler("balance_management", balance_management))
        application.add_handler(CommandHandler("backup", backup_command))
        application.add_handler(CommandHandler("restore", restore_command))
        application.add_handler(CommandHandler("notification", notification_command))
        application.add_handler(CommandHandler("coupon", coupon_command))
        application.add_handler(CommandHandler("numbers", numbers_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("cleardb", clear_db))
        application.add_handler(CommandHandler("debug_subscriptions", debug_subscriptions))
        application.add_error_handler(error_handler)
        logger.info("Bot started and webhook set")
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise

async def on_shutdown():
    try:
        await application.bot.delete_webhook()
        close_db_pool()
        logger.info("Bot stopped and webhook deleted")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

@app.on_event("startup")
async def startup_event():
    await on_startup()

@app.on_event("shutdown")
async def shutdown_event():
    await on_shutdown()

# ---------- Main ----------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
