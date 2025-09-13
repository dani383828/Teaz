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

# ---------- تنظیمات اولیه ----------
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = ["@teazvpn", "@charkhoun"]
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "6037 9975 9717 2684"

RENDER_BASE_URL = os.getenv("RENDER_BASE_URL") or "https://teaz.onrender.com"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_BASE_URL}{WEBHOOK_PATH}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

app = FastAPI()
application = Application.builder().token(TOKEN).build()

# ---------- PostgreSQL connection pool (psycopg2) ----------
import psycopg2
from psycopg2 import pool
import tempfile
import subprocess

DATABASE_URL = os.getenv("DATABASE_URL")

db_pool: pool.ThreadedConnectionPool = None

def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    try:
        db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
        logging.info("Database pool initialized successfully")
    except Exception as e:
        logging.error(f"Failed to initialize database pool: {e}")
        raise

def close_db_pool():
    global db_pool
    if db_pool:
        db_pool.closeall()
        db_pool = None
        logging.info("Database pool closed")

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
        logging.error(f"Database error in query '{query}' with params {params}: {e}")
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
        logging.error(f"Async database error in query '{query}' with params {params}: {e}")
        raise

# ---------- ساخت و مهاجرت جداول ----------
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
                            WHEN plan = '۱ ماهه: ۹۰ هزار تومان' THEN 30
                            WHEN plan = '۳ ماهه: ۲۵۰ هزار تومان' THEN 90
                            WHEN plan = '۶ ماهه: ۴۵۰ هزار تومان' THEN 180
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
        logging.info("Database tables created and migrated successfully")
    except Exception as e:
        logging.error(f"Error creating or migrating tables: {e}")

# ---------- دستور جدید برای بکاپ گیری از دیتابیس ----------
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    
    try:
        await update.message.reply_text("🔄 در حال تهیه بکاپ از دیتابیس...")
        
        # ایجاد فایل موقت برای بکاپ
        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tmp_file:
            backup_file = tmp_file.name
        
        # استخراج اطلاعات اتصال از DATABASE_URL
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name = parsed_url.path[1:]
        db_user = parsed_url.username
        db_password = parsed_url.password
        db_host = parsed_url.hostname
        db_port = parsed_url.port or 5432
        
        # اجرای دستور pg_dump برای بکاپ
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-f', backup_file,
            '-F', 'p'  # فرمت plain text
        ]
        
        # تنظیم محیط برای پسورد
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        # اجرای دستور
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
            raise Exception(f"Backup failed: {error_msg}")
        
        # ارسال فایل بکاپ
        with open(backup_file, 'rb') as file:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=file,
                filename=f"teazvpn_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
                caption="✅ بکاپ از دیتابیس با موفقیت تهیه شد."
            )
        
        # حذف فایل موقت
        os.unlink(backup_file)
        
        await update.message.reply_text("✅ بکاپ با موفقیت تهیه و ارسال شد.")
        
    except Exception as e:
        logging.error(f"Error in backup command: {e}")
        await update.message.reply_text(f"⚠️ خطا در تهیه بکاپ: {str(e)}")

# ---------- دستور جدید برای بازیابی دیتابیس ----------
async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    
    await update.message.reply_text("📤 لطفا فایل بکاپ دیتابیس را ارسال کنید:")
    user_states[update.effective_user.id] = "awaiting_backup_file"

# ---------- تابع برای بازیابی دیتابیس از فایل بکاپ ----------
async def restore_database_from_backup(file_path: str):
    """
    بازیابی دیتابیس از فایل بکاپ
    """
    try:
        # استخراج اطلاعات اتصال از DATABASE_URL
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name = parsed_url.path[1:]
        db_user = parsed_url.username
        db_password = parsed_url.password
        db_host = parsed_url.hostname
        db_port = parsed_url.port or 5432
        
        # اجرای دستور psql برای بازیابی
        cmd = [
            'psql',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-f', file_path
        ]
        
        # تنظیم محیط برای پسورد
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        # اجرای دستور
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8') if stderr else "Unknown error"
            raise Exception(f"Restore failed: {error_msg}")
        
        return True, "✅ دیتابیس با موفقیت بازیابی شد."
        
    except Exception as e:
        logging.error(f"Error restoring database: {e}")
        return False, f"⚠️ خطا در بازیابی دیتابیس: {str(e)}"

# ---------- دستور جدید برای اطلاع رسانی به همه کاربران ----------
async def notification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    
    await update.message.reply_text("📢 لطفا متن اطلاع‌رسانی را ارسال کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_notification_text"

# ---------- دستور جدید برای مدیریت کد تخفیف ----------
async def coupon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    
    await update.message.reply_text("💵 مقدار تخفیف را به درصد وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_coupon_discount"

# ---------- دستور جدید برای نمایش شماره‌ها ----------
async def numbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    
    try:
        users = await db_execute(
            "SELECT user_id, username, phone FROM users ORDER BY created_at DESC",
            fetch=True
        )
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.")
            return

        response = "📞 لیست شماره‌های کاربران:\n\n"
        for user in users:
            user_id, username, phone = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            phone_display = phone if phone else "نامشخص"
            response += f"کاربر: {username_display}\n"
            response += f"شماره: {phone_display}\n"
            response += "--------------------\n"

        await send_long_message(
            update.effective_user.id,
            response,
            context,
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logging.error(f"Error in numbers_command: {e}")
        await update.message.reply_text("⚠️ خطایی در نمایش شماره‌ها رخ داد. لطفاً دوباره تلاش کنید.")

# ---------- دستور آمار ربات ----------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
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
        
        total_subs = await db_execute(
            "SELECT COUNT(*) FROM subscriptions",
            fetchone=True
        )
        active_subs = await db_execute(
            "SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND config IS NOT NULL",
            fetchone=True
        )
        pending_subs = await db_execute(
            "SELECT COUNT(*) FROM payments WHERE status = 'pending' AND type = 'buy_subscription'",
            fetchone=True
        )
        total_transactions = await db_execute(
            "SELECT COUNT(*) FROM payments",
            fetchone=True
        )
        
        invited_users = await db_execute(
            "SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL",
            fetchone=True
        )
        
        stats_message = "🌟 گزارش عملکرد تیز VPN 🚀\n\n"
        stats_message += "👥 کاربران:\n"
        stats_message += f"  • کل کاربران: {total_users[0] if total_users else 0:,} نفر 🧑‍💻\n"
        stats_message += f"  • کاربران فعال: {active_users[0] if active_users else 0:,} نفر ✅\n"
        stats_message += f"  • کاربران غیرفعال: {inactive_users:,} نفر ❎\n"
        stats_message += f"  • کاربران جدید امروز: {today_users[0] if today_users else 0:,} نفر 🆕\n"
        stats_message += f"  • کاربران دعوت‌شده: {invited_users[0] if invited_users else 0:,} نفر 🤝\n\n"
        
        stats_message += "💸 درآمد:\n"
        stats_message += f"  • امروز: {today_income[0] if today_income else 0:,} تومان 💰\n"
        stats_message += f"  • این ماه: {month_income[0] if month_income else 0:,} تومان 📈\n"
        stats_message += f"  • کل درآمد: {total_income[0] if total_income else 0:,} تومان 🔥\n\n"
        
        stats_message += "📦 اشتراک‌ها:\n"
        stats_message += f"  • کل اشتراک‌ها: {total_subs[0] if total_subs else 0:,} عدد 📋\n"
        stats_message += f"  • اشتراک‌های فعال: {active_subs[0] if active_subs else 0:,} عدد 🟢\n"
        stats_message += f"  • اشتراک‌های در انتظار: {pending_subs[0] if pending_subs else 0:,} عدد ⏳\n"
        stats_message += f"  • پرفروش‌ترین پلن: {best_selling_plan[0]} ({best_selling_plan[1]:,} عدد) 🏆\n\n"
        
        stats_message += "💳 روش‌های پرداخت:\n"
        for method, percent in payment_methods_percent:
            display_name = method_names.get(method, method)
            stats_message += f"  • {display_name}: {percent}% 💸\n"
        stats_message += f"  • کل تراکنش‌ها: {total_transactions[0] if total_transactions else 0:,} عدد 🔄\n"
        
        await update.message.reply_text(stats_message)
        
    except Exception as e:
        logging.error(f"Error generating stats: {e}")
        await update.message.reply_text("⚠️ خطایی در نمایش آمار رخ داد. لطفاً دوباره تلاش کنید.")

# ---------- پاک کردن دیتابیس ----------
async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        await db_execute("DELETE FROM coupons")
        await db_execute("DELETE FROM subscriptions")
        await db_execute("DELETE FROM payments")
        await db_execute("DELETE FROM users")
        logging.info("Database cleared successfully by admin")
        await update.message.reply_text("✅ دیتابیس با موفقیت پاک شد.")
    except Exception as e:
        logging.error(f"Error clearing database: {e}")
        await update.message.reply_text(f"⚠️ خطا در پاک کردن دیتابیس: {str(e)}")

# ---------- کیبوردها ----------
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
        [KeyboardButton("بازگشت به منو")]
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

# ---------- تابع کمکی برای ارسال پیام‌های طولانی ----------
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

# ---------- توابع DB برای کوپن‌ها ----------
async def create_coupon(code, discount_percent, user_id=None):
    try:
        await db_execute(
            "INSERT INTO coupons (code, discount_percent, user_id, is_used) VALUES (%s, %s, %s, FALSE)",
            (code, discount_percent, user_id)
        )
        logging.info(f"Coupon {code} created with {discount_percent}% discount for user_id {user_id or 'all'}")
    except Exception as e:
        logging.error(f"Error creating coupon {code}: {e}")
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
        logging.error(f"Error validating coupon {code} for user_id {user_id}: {e}")
        return None, "خطا در بررسی کد تخفیف."

async def mark_coupon_used(code):
    try:
        await db_execute("UPDATE coupons SET is_used = TRUE WHERE code = %s", (code,))
        logging.info(f"Coupon {code} marked as used")
    except Exception as e:
        logging.error(f"Error marking coupon {code} as used: {e}")

# ---------- توابع DB موجود ----------
async def is_user_member(user_id):
    try:
        for channel in CHANNEL_USERNAMES:
            member = await application.bot.get_chat_member(channel, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        return True
    except Exception:
        return False

async def ensure_user(user_id, username, invited_by=None):
    try:
        row = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        if not row:
            await db_execute(
                "INSERT INTO users (user_id, username, invited_by, is_agent) VALUES (%s, %s, %s, FALSE)",
                (user_id, username, invited_by)
            )
            if invited_by and invited_by != user_id:
                inviter = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (invited_by,), fetchone=True)
                if inviter:
                    await add_balance(invited_by, 25000)
        logging.info(f"User {user_id} ensured in database")
    except Exception as e:
        logging.error(f"Error ensuring user {user_id}: {e}")

async def set_user_agent(user_id):
    try:
        await db_execute("UPDATE users SET is_agent = TRUE WHERE user_id = %s", (user_id,))
        logging.info(f"User {user_id} set as agent")
    except Exception as e:
        logging.error(f"Error setting user {user_id} as agent: {e}")

async def is_user_agent(user_id):
    try:
        row = await db_execute("SELECT is_agent FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return row[0] if row and row[0] is not None else False
    except Exception as e:
        logging.error(f"Error checking agent status for user_id {user_id}: {e}")
        return False

async def save_user_phone(user_id, phone):
    try:
        await db_execute("UPDATE users SET phone = %s WHERE user_id = %s", (phone, user_id))
        logging.info(f"Phone saved for user_id {user_id}")
    except Exception as e:
        logging.error(f"Error saving user phone for user_id {user_id}: {e}")
        return None

async def get_user_phone(user_id):
    try:
        row = await db_execute("SELECT phone FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return row[0] if row else None
    except Exception as e:
        logging.error(f"Error getting user phone for user_id {user_id}: {e}")
        return None

async def add_balance(user_id, amount):
    try:
        await db_execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE user_id = %s", (amount, user_id))
        logging.info(f"Added {amount} to balance for user_id {user_id}")
    except Exception as e:
        logging.error(f"Error adding balance for user_id {user_id}: {e}")

async def deduct_balance(user_id, amount):
    try:
        await db_execute("UPDATE users SET balance = COALESCE(balance,0) - %s WHERE user_id = %s", (amount, user_id))
        logging.info(f"Deducted {amount} from balance for user_id {user_id}")
    except Exception as e:
        logging.error(f"Error deducting balance for user_id {user_id}: {e}")

async def get_balance(user_id):
    try:
        row = await db_execute("SELECT balance FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        logging.error(f"Error getting balance for user_id {user_id}: {e}")
        return 0

async def add_payment(user_id, amount, ptype, payment_method, description="", coupon_code=None):
    try:
        query = "INSERT INTO payments (user_id, amount, status, type, payment_method, description) VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING id"
        new_id = await db_execute(query, (user_id, amount, ptype, payment_method, description), returning=True)
        if coupon_code:
            await mark_coupon_used(coupon_code)
        logging.info(f"Payment added for user_id {user_id}, amount: {amount}, type: {ptype}, payment_method: {payment_method}, id: {new_id}")
        return int(new_id) if new_id is not None else None
    except Exception as e:
        logging.error(f"Error adding payment for user_id {user_id}: {e}")
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
        logging.info(f"Subscription added for user_id {user_id}, payment_id: {payment_id}, plan: {plan}, duration: {duration_days} days, status: pending")
    except Exception as e:
        logging.error(f"Error adding subscription for user_id {user_id}, payment_id: {payment_id}: {e}")
        raise

async def update_subscription_config(payment_id, config):
    try:
        await db_execute(
            "UPDATE subscriptions SET config = %s, status = 'active' WHERE payment_id = %s",
            (config, payment_id)
        )
        logging.info(f"Subscription config updated and set to active for payment_id {payment_id}")
    except Exception as e:
        logging.error(f"Error updating subscription config for payment_id {payment_id}: {e}")

async def update_payment_status(payment_id, status):
    try:
        await db_execute("UPDATE payments SET status = %s WHERE id = %s", (status, payment_id))
        logging.info(f"Payment status updated to {status} for payment_id {payment_id}")
    except Exception as e:
        logging.error(f"Error updating payment status for payment_id {payment_id}: {e}")

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
        logging.info(f"Fetched {len(rows)} subscriptions for user_id {user_id}")
        current_time = datetime.now()
        subscriptions = []
        for row in rows:
            try:
                sub_id, plan, config, status, payment_id, start_date, duration_days, username = row
                start_date = start_date or current_time
                duration_days = duration_days or 30
                username = username or str(user_id)
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
                    'username': username,
                    'end_date': start_date + timedelta(days=duration_days)
                })
            except Exception as e:
                logging.error(f"Error processing subscription {sub_id} for user_id {user_id}: {e}")
                continue
        logging.info(f"Processed {len(subscriptions)} subscriptions for user_id {user_id}")
        return subscriptions
    except Exception as e:
        logging.error(f"Error in get_user_subscriptions for user_id {user_id}: {e}")
        return []

# ---------- دستور تشخیصی برای ادمین ----------
async def debug_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
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
            await update.message.reply_text("📂 هیچ اشتراکی برای هیچ کاربری یافت نشد.")
            return

        response = "📂 لیست تمام اشتراک‌های کاربران:\n\n"
        current_time = datetime.now()
        for row in rows:
            user_id, username, plan, payment_id, start_date, duration_days, status = row
            username_display = f"@{username}" if username else f"@{user_id}"
            start_date = start_date if start_date else current_time
            duration_days = duration_days if duration_days else 30
            remaining_days = 0
            if status == "active":
                end_date = start_date + timedelta(days=duration_days)
                remaining_days = max(0, (end_date - current_time).days)
            response += f"کاربر: {username_display}\n"
            response += f"اشتراک: {plan}\n"
            response += f"کد خرید: #{payment_id}\n"
            response += f"وضعیت: {'فعال' if status == 'active' else 'غیرفعال'}\n"
            response += f"زمان باقی‌مانده: {remaining_days} روز\n"
            response += "--------------------\n"
        
        await send_long_message(update.effective_user.id, response, context)
    except Exception as e:
        logging.error(f"Error in debug_subscriptions: {e}")
        await update.message.reply_text(f"⚠️ خطا در بررسی اشتراک‌ها: {str(e)}")

# ---------- وضعیت کاربر در مموری ----------
user_states = {}

# ---------- دستورات و هندلرها ----------
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
            BotCommand(command="/restore", description="بازیابی دیتابیس از بکاپ (ادمین)")
        ]
        await application.bot.set_my_commands(public_commands)
        await application.bot.set_my_commands(admin_commands, scope={"type": "chat", "chat_id": ADMIN_ID})
        logging.info("Bot commands set successfully")
    except Exception as e:
        logging.error(f"Error setting bot commands: {e}")

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

    invited_by = context.user_data.get("invited_by")
    await ensure_user(user_id, username, invited_by)

    phone = await get_user_phone(user_id)
    if phone:
        await update.message.reply_text(
            "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        return

    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "✅ لطفا شماره تماس خود را ارسال کنید.",
        reply_markup=contact_keyboard
    )
    user_states[user_id] = "awaiting_contact"

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_states.get(user_id) != "awaiting_contact":
        return
    contact = update.message.contact
    if contact is None or contact.user_id != user_id:
        await update.message.reply_text("⚠️ لطفا شماره تماس خود را از طریق دکمه ارسال کنید.")
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

    await update.message.reply_text(
        "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""

    if user_states.get(user_id) == "awaiting_contact":
        contact_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("ارسال شماره تماس", request_contact=True)]], 
            resize_keyboard=True, 
            one_time_keyboard=True
        )
        await update.message.reply_text(
            "⚠️ لطفا ابتدا شماره تماس خود را از طریق دکمه ارسال کنید.",
            reply_markup=contact_keyboard
        )
        return

    if text in ["بازگشت به منو", "⬅️ بازگشت به منو"]:
        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return

    # هندلر جدید برای دریافت فایل بکاپ
    if user_states.get(user_id) == "awaiting_backup_file":
        if update.message.document:
            try:
                # دریافت فایل
                file = await context.bot.get_file(update.message.document.file_id)
                
                # ایجاد فایل موقت برای ذخیره بکاپ
                with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tmp_file:
                    backup_file = tmp_file.name
                
                # دانلود فایل
                await file.download_to_drive(backup_file)
                
                await update.message.reply_text("🔄 در حال بازیابی دیتابیس...")
                
                # بازیابی دیتابیس
                success, message = await restore_database_from_backup(backup_file)
                
                # حذف فایل موقت
                os.unlink(backup_file)
                
                if success:
                    await update.message.reply_text(message, reply_markup=get_main_keyboard())
                else:
                    await update.message.reply_text(message, reply_markup=get_main_keyboard())
                
                user_states.pop(user_id, None)
                return
                
            except Exception as e:
                logging.error(f"Error in restore process: {e}")
                await update.message.reply_text(f"⚠️ خطا در بازیابی دیتابیس: {str(e)}", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
        else:
            await update.message.reply_text("⚠️ لطفا یک فایل بکاپ ارسال کنید.", reply_markup=get_back_keyboard())
            return

    if update.message.photo or update.message.document or update.message.text:
        state = user_states.get(user_id)
        if state and (
            state.startswith("awaiting_deposit_receipt_") or 
            state.startswith("awaiting_subscription_receipt_") or 
            state.startswith("awaiting_agency_receipt_")
        ):
            try:
                payment_id = int(state.split("_")[-1])
            except:
                payment_id = None

            if payment_id:
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
                    else:
                        doc_id = update.message.document.file_id
                        await context.bot.send_document(chat_id=ADMIN_ID, document=doc_id, caption=caption, reply_markup=keyboard)

                    await update.message.reply_text("✅ فیش شما برای ادمین ارسال شد، لطفا منتظر تایید باشید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    return
        elif state and state.startswith("awaiting_config_"):
            try:
                payment_id = int(state.split("_")[-1])
            except:
                payment_id = None

            if payment_id:
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
                        await update.message.reply_text("✅ کانفیگ با موفقیت به خریدار ارسال شد.", reply_markup=None)
                        user_states.pop(user_id, None)
                    else:
                        await update.message.reply_text("⚠️ لطفا کانفیگ را به صورت متن ارسال کنید.")
                    return
        elif state == "awaiting_coupon_discount" and user_id == ADMIN_ID:
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
                else:
                    await update.message.reply_text("⚠️ درصد تخفیف باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
            else:
                await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            return
        elif state and state.startswith("awaiting_coupon_recipient_") and user_id == ADMIN_ID:
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
                            logging.error(f"Error sending coupon to user_id {user[0]}: {e}")
                            continue
                    await update.message.reply_text(
                        f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر (غیر از نمایندگان) ارسال شد.",
                        reply_markup=get_main_keyboard(),
                        parse_mode="Markdown"
                    )
                    user_states.pop(user_id, None)
                except Exception as e:
                    logging.error(f"Error sending coupons to all users: {e}")
                    await update.message.reply_text(
                        "⚠️ خطا در ارسال کد تخفیف برای همه کاربران.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                return
            elif text == "👤 برای یک نفر":
                target_user_id = 6056483071  # کاربر مشخص‌شده
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
                else:
                    await update.message.reply_text(
                        f"⚠️ کاربری با ID {target_user_id} یافت نشد.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                return
            elif text == "🎯 درصد خاصی از کاربران":
                user_states[user_id] = f"awaiting_coupon_percent_{coupon_code}_{discount_percent}"
                await update.message.reply_text("📊 درصد کاربران را وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
                return
            else:
                await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=get_coupon_recipient_keyboard())
                return
        elif state and state.startswith("awaiting_couton_percent_") and user_id == ADMIN_ID:
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
                                logging.error(f"Error sending coupon to user_id {user[0]}: {e}")
                                continue
                        await update.message.reply_text(
                            f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر ({percent}% از کاربران غیر نماینده) ارسال شد.",
                            reply_markup=get_main_keyboard(),
                            parse_mode="Markdown"
                        )
                        user_states.pop(user_id, None)
                    except Exception as e:
                        logging.error(f"Error sending coupons to {percent}% of users: {e}")
                        await update.message.reply_text(
                            "⚠️ خطا در ارسال کد تخفیف برای درصد مشخصی از کاربران.",
                            reply_markup=get_main_keyboard()
                        )
                        user_states.pop(user_id, None)
                else:
                    await update.message.reply_text("⚠️ درصد باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
            else:
                await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
            return
        elif state and state.startswith("awaiting_coupon_code_"):
            parts = state.split("_")
            amount = int(parts[3])
            plan = "_".join(parts[4:]) if len(parts) <= 5 else "_".join(parts[4:-1])
            coupon_code = parts[-1] if len(parts) > 5 else None
            
            if text == "ادامه":
                user_states[user_id] = f"awaiting_payment_method_{amount}_{plan}"
                await update.message.reply_text("💳 روش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
                return
            coupon_code = text.strip()
            discount_percent, error = await validate_coupon(coupon_code, user_id)
            if error:
                await update.message.reply_text(
                    f"⚠️ {error}\nلطفا کد معتبر وارد کنید یا برای ادامه روی 'ادامه' کلیک کنید:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
                )
                return
            discounted_amount = int(amount * (1 - discount_percent / 100))
            user_states[user_id] = f"awaiting_payment_method_{discounted_amount}_{plan}_{coupon_code}"
            await update.message.reply_text(
                f"✅ کد تخفیف اعمال شد! مبلغ با {discount_percent}% تخفیف: {discounted_amount} تومان\nروش خرید را انتخاب کنید:",
                reply_markup=get_payment_method_keyboard()
            )
            return
        elif state == "awaiting_notification_text" and user_id == ADMIN_ID:
            notification_text = text
            await update.message.reply_text(
                "📢 آیا مطمئن هستید که می‌خواهید این اطلاعیه را برای همه کاربران ارسال کنید؟",
                reply_markup=ReplyKeyboardMarkup([
                    [KeyboardButton("✅ بله، ارسال کن")],
                    [KeyboardButton("❌ خیر، انصراف")]
                ], resize_keyboard=True)
            )
            user_states[user_id] = f"confirm_notification_{notification_text}"
            return
        elif state and state.startswith("confirm_notification_") and user_id == ADMIN_ID:
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
                            logging.error(f"Error sending notification to user_id {user[0]}: {e}")
                            failed_count += 1
                            continue
                    
                    await update.message.reply_text(
                        f"✅ اطلاعیه با موفقیت به {sent_count} کاربر ارسال شد.\n"
                        f"❌ تعداد کاربرانی که دریافت نکردند: {failed_count}",
                        reply_markup=get_main_keyboard()
                    )
                except Exception as e:
                    logging.error(f"Error sending notifications: {e}")
                    await update.message.reply_text(
                        "⚠️ خطا در ارسال اطلاعیه به کاربران.",
                        reply_markup=get_main_keyboard()
                    )
            else:
                await update.message.reply_text(
                    "❌ ارسال اطلاعیه لغو شد.",
                    reply_markup=get_main_keyboard()
                )
            user_states.pop(user_id, None)
            return

    if text == "💰 موجودی":
        await update.message.reply_text("💰 بخش موجودی:\nیک گزینه را انتخاب کنید:", reply_markup=get_balance_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "نمایش موجودی":
        bal = await get_balance(user_id)
        await update.message.reply_text(f"💰 موجودی شما: {bal} تومان", reply_markup=get_balance_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "افزایش موجودی":
        await update.message.reply_text("💳 لطفا مبلغ واریزی را به تومان وارد کنید (مثال: 90000):", reply_markup=get_back_keyboard())
        user_states[user_id] = "awaiting_deposit_amount"
        return

    if user_states.get(user_id) == "awaiting_deposit_amount":
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
            else:
                await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
        else:
            await update.message.reply_text("⚠️ لطفا عدد وارد کنید.", reply_markup=get_back_keyboard())
        return

    if text == "💳 خرید اشتراک":
        is_agent = await is_user_agent(user_id)
        await update.message.reply_text("💳 پلن را انتخاب کنید:", reply_markup=get_subscription_keyboard(is_agent))
        user_states.pop(user_id, None)
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
        is_agent = await is_user_agent(user_id)
        if not is_agent:
            await update.message.reply_text(
                f"💵 اگر کد تخفیف دارید، وارد کنید. در غیر این صورت برای ادامه روی 'ادامه' کلیک کنید:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
            )
            user_states[user_id] = f"awaiting_coupon_code_{amount}_{text}"
        else:
            user_states[user_id] = f"awaiting_payment_method_{amount}_{text}"
            await update.message.reply_text("💳 روش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
        return

    if user_states.get(user_id, "").startswith("awaiting_payment_method_"):
        state = user_states.get(user_id)
        logging.info(f"Processing payment method for user_id {user_id}, state: {state}")
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
                    logging.info(f"Set state to awaiting_subscription_receipt_{payment_id} for user_id {user_id}")
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
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
                    logging.info(f"Set state to awaiting_subscription_receipt_{payment_id} for user_id {user_id}")
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
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
                        logging.info(f"Payment with balance successful for user_id {user_id}, payment_id: {payment_id}")
                    else:
                        await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                        user_states.pop(user_id, None)
                else:
                    await update.message.reply_text(
                        f"⚠️ موجودی شما ({balance} تومان) کافی نیست. لطفا ابتدا موجودی خود را افزایش دهید.",
                        reply_markup=get_main_keyboard()
                    )
                    user_states.pop(user_id, None)
                return

        except Exception as e:
            logging.error(f"Error processing payment method for user_id {user_id}, state: {state}, error: {e}")
            await update.message.reply_text("⚠️ خطا در پردازش. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            return

    if text == "🎁 اشتراک تست رایگان":
        await update.message.reply_text(
            "🎁 برای دریافت اشتراک تست رایگان، لطفا با پشتیبانی تماس بگیرید: https://t.me/teazadmin",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "☎️ پشتیبانی":
        await update.message.reply_text("📞 پشتیبانی: https://t.me/teazadmin", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "💵 اعتبار رایگان":
        invite_link = f"https://t.me/teazvpn_bot?start={user_id}"
        try:
            with open("invite_image.jpg", "rb") as photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\n"
                           "برای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد.",
                    reply_markup=get_main_keyboard()
                )
        except Exception as e:
            logging.error(f"Error sending invite image: {e}")
            await update.message.reply_text(
                f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\n"
                "برای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد.",
                reply_markup=get_main_keyboard()
            )
        user_states.pop(user_id, None)
        return

    if text == "📂 اشتراک‌های من":
        try:
            subscriptions = await get_user_subscriptions(user_id)
            if not subscriptions:
                await update.message.reply_text("📂 شما هنوز اشتراکی ندارید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
            
            current_time = datetime.now()
            response = "📂 لیست کامل اشتراک‌های شما:\n\n"
            
            for sub in subscriptions:
                try:
                    response += f"🔹 اشتراک #{sub['id']}\n"
                    response += f"📌 پلن: {sub['plan']}\n"
                    response += f"🆔 کد خرید: #{sub['payment_id']}\n"
                    response += f"📊 وضعیت: {'✅ فعال' if sub['status'] == 'active' else '⏳ در انتظار'}\n"
                    
                    if sub['status'] == "active":
                        remaining_days = max(0, (sub['end_date'] - current_time).days)
                        response += f"⏳ زمان باقی‌مانده: {remaining_days} روز\n"
                        response += f"📅 تاریخ شروع: {sub['start_date'].strftime('%Y-%m-%d %H:%M')}\n"
                        response += f"📅 تاریخ انقضا: {sub['end_date'].strftime('%Y-%m-%d %H:%M')}\n"
                    
                    if sub['config']:
                        response += f"🔐 کانفیگ:\n```\n{sub['config']}\n```\n"
                    
                    response += "------------------------\n\n"
                    
                except Exception as e:
                    logging.error(f"Error processing subscription {sub['id']} for user_id {user_id}: {e}")
                    continue
            
            await send_long_message(user_id, response, context, reply_markup=get_main_keyboard(), parse_mode="Markdown")
            
        except Exception as e:
            logging.error(f"Error displaying subscriptions for user_id {user_id}: {e}")
            await update.message.reply_text("⚠️ خطا در نمایش اشتراک‌ها. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
        
        user_states.pop(user_id, None)
        return

    if text == "💡 راهنمای اتصال":
        await update.message.reply_text(
            "راهنمای راه‌اندازی\nدستگاه خود را انتخاب کنید:",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "📗 اندروید":
        await update.message.reply_text(
            "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های V2RayNG یا Hiddify(پیشنهادی) است ✅\n"
            "با این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "📕 آیفون/مک":
        await update.message.reply_text(
            "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های Singbox(پیشنهادی) یا Streisand یا V2box(پیشنهادی) هست ✅\n"
            "با این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "📘 ویندوز":
        await update.message.reply_text(
            "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\n"
            "با این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "📙 لینوکس":
        await update.message.reply_text(
            "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\n"
            "با این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
            reply_markup=get_connection_guide_keyboard()
        )
        user_states.pop(user_id, None)
        return

    if text == "🧑‍💼 درخواست نمایندگی":
        is_agent = await is_user_agent(user_id)
        if is_agent:
            await update.message.reply_text("💳 پلن را انتخاب کنید:", reply_markup=get_subscription_keyboard(is_agent=True))
            user_states.pop(user_id, None)
            return

        agency_text = (
            "🚀 اعطای نمایندگی رسمی تیز وی پی ان 🚀\n\n"
            "اگر به دنبال یک فرصت درآمدزایی پایدار و بدون محدودیت هستید، حالا بهترین زمان برای پیوستن به تیم ماست!\n"
            "ما به تعداد محدودی نماینده رسمی می‌پذیریم که بتوانند با فروش سرویس‌های پرسرعت و پایدار تیز وی پی ان، کسب‌وکار خودشان را راه‌اندازی کنند.\n\n"
            "💰 شرایط دریافت نمایندگی:\n"
            "برای شروع همکاری و فعال‌سازی پنل اختصاصی، کافیست ۱ میلیون تومان واریز کنید.\n"
            "پس از واریز، شما به یک پنل کامل و شخصی دسترسی خواهید داشت که امکان ساخت و مدیریت اکانت‌ها را برایتان فراهم می‌کند.\n\n"
            "📦 قیمت پلن‌ها برای نمایندگان:\n"
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۲۲ کاهش)\n"
            "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۱۶ کاهش)\n"
            "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۱۶ کاهش)\n\n"
            "🔹 اکانت‌ها کاملاً نامحدود هستند (بدون محدودیت حجم یا سرعت)\n"
            "🔹 شما تعیین‌کننده قیمت فروش به مشتری هستید\n"
            "🔹 پشتیبانی کامل و ۲۴ ساعته\n\n"
            "🔻 در صورت تایید موارد بالا روش پرداخت خود را انتخاب کنید"
        )
        await update.message.reply_text(agency_text, reply_markup=get_payment_method_keyboard())
        user_states[user_id] = "awaiting_agency_payment_method"
        return

    if user_states.get(user_id) == "awaiting_agency_payment_method":
        amount = 1000000
        description = "درخواست نمایندگی"
        if text == "🏦 کارت به کارت":
            payment_id = await add_payment(user_id, amount, "agency_request", "card_to_card", description=description)
            if payment_id:
                await update.message.reply_text(
                    f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n"
                    f"🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ",
                    reply_markup=get_back_keyboard(),
                    parse_mode="MarkdownV2"
                )
                user_states[user_id] = f"awaiting_agency_receipt_{payment_id}"
            else:
                await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            return

        if text == "💎 پرداخت با ترون":
            payment_id = await add_payment(user_id, amount, "agency_request", "tron", description=description)
            if payment_id:
                await update.message.reply_text(
                    f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n"
                    f"💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`",
                    reply_markup=get_back_keyboard(),
                    parse_mode="MarkdownV2"
                )
                user_states[user_id] = f"awaiting_agency_receipt_{payment_id}"
            else:
                await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            return

        if text == "💰 پرداخت با موجودی":
            balance = await get_balance(user_id)
            if balance >= amount:
                payment_id = await add_payment(user_id, amount, "agency_request", "balance", description=description)
                if payment_id:
                    await deduct_balance(user_id, amount)
                    await update_payment_status(payment_id, "approved")
                    await set_user_agent(user_id)
                    await add_balance(user_id, amount)  # Add the 1M to balance
                    await update.message.reply_text(
                        "✅ فیش شما تایید و نمایندگی به شما اعطا شد! ۱,۰۰۰,۰۰۰ تومان به موجودی شما اضافه شد.",
                        reply_markup=get_main_keyboard()
                    )
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"📢 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) با موجودی خود نمایندگی خریداری کرد."
                    )
                    user_states.pop(user_id, None)
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
            else:
                await update.message.reply_text(
                    f"⚠️ موجودی شما ({balance} تومان) کافی نیست. لطفا ابتدا موجودی خود را افزایش دهید.",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
            return

    await update.message.reply_text("⚠️ دستور نامعتبر است. لطفا از دکمه‌ها استفاده کنید.", reply_markup=get_main_keyboard())
    user_states.pop(user_id, None)

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("approve_") or data.startswith("reject_") or data.startswith("send_config_"):
        if update.effective_user.id != ADMIN_ID:
            await query.message.reply_text("⚠️ شما اجازه این کار را ندارید.")
            return

        if data.startswith("approve_"):
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, amount, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            user_id, amount, ptype, description = payment

            await update_payment_status(payment_id, "approved")
            if ptype == "increase_balance":
                await add_balance(user_id, amount)
                await context.bot.send_message(user_id, f"💰 پرداخت تایید شد. موجودی {amount} تومان اضافه شد.")
                await query.message.edit_reply_markup(None)
                await query.message.reply_text("✅ پرداخت تایید شد.")
            elif ptype == "buy_subscription":
                await context.bot.send_message(user_id, f"✅ پرداخت تایید شد. اشتراک شما (کد خرید: #{payment_id}) ارسال خواهد شد.")
                config_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"send_config_{payment_id}")]
                ])
                await query.message.edit_reply_markup(None)
                await query.message.reply_text(f"✅ پرداخت برای اشتراک ({description}) تایید شد.", reply_markup=config_keyboard)
            elif ptype == "agency_request":
                await set_user_agent(user_id)
                await add_balance(user_id, amount)  # Add the 1M to balance
                await context.bot.send_message(user_id, "✅ فیش شما تایید و نمایندگی به شما اعطا شد! ۱,۰۰۰,۰۰۰ تومان به موجودی شما اضافه شد.")
                await query.message.edit_reply_markup(None)
                await query.message.reply_text("✅ درخواست نمایندگی تایید شد.")

        elif data.startswith("reject_"):
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, amount, type FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            user_id, amount, ptype = payment

            await update_payment_status(payment_id, "rejected")
            await context.bot.send_message(user_id, "❌ پرداخت شما رد شد. با پشتیبانی تماس بگیرید.")
            await query.message.edit_reply_markup(None)
            await query.message.reply_text("❌ پرداخت رد شد.")

        elif data.startswith("send_config_"):
            payment_id = int(data.split("_")[-1])
            payment = await db_execute("SELECT user_id, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            await query.message.reply_text("لطفا کانفیگ را ارسال کنید.")
            user_states[ADMIN_ID] = f"awaiting_config_{payment_id}"

async def start_with_param(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and len(args) > 0:
        try:
            invited_by = int(args[0])
            if invited_by != update.effective_user.id:
                context.user_data["invited_by"] = invited_by
        except:
            context.user_data["invited_by"] = None
    await start(update, context)

# ---------- ثبت هندلرها ----------
application.add_handler(CommandHandler("start", start_with_param))
application.add_handler(CommandHandler("debug_subscriptions", debug_subscriptions))
application.add_handler(CommandHandler("cleardb", clear_db))
application.add_handler(CommandHandler("stats", stats_command))
application.add_handler(CommandHandler("numbers", numbers_command))
application.add_handler(CommandHandler("coupon", coupon_command))
application.add_handler(CommandHandler("notification", notification_command))
application.add_handler(CommandHandler("backup", backup_command))
application.add_handler(CommandHandler("restore", restore_command))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), message_handler))
application.add_handler(CallbackQueryHandler(admin_callback_handler))

# ---------- webhook endpoint ----------
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

# ---------- lifecycle events ----------
@app.on_event("startup")
async def on_startup():
    init_db_pool()
    await create_tables()
    try:
        await application.bot.set_webhook(url=WEBHOOK_URL)
        logging.info("Webhook set successfully")
    except Exception as e:
        logging.error(f"Error setting webhook: {e}")
    await set_bot_commands()
    await application.initialize()
    await application.start()
    print("✅ Webhook set:", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.stop()
        await application.shutdown()
    finally:
        close_db_pool()

# ---------- اجرای محلی (برای debug) ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
