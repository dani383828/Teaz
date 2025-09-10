I’ll help implement the requested changes. Here’s the modified code with the new features, additional admin, and optimized spacing. I’ve addressed all points: adding the /auto_start command, a new limited admin, backup/restore improvements for agent status, new user management commands, and the /list_channels command. I’ve also ensured long messages are split into parts and removed unnecessary whitespace while preserving code functionality.
import os
import logging
import asyncio
import random
import string
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import psycopg2
from psycopg2 import pool
import tempfile
import subprocess

# Initial setup
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_IDS = {5542927340, 7608325054}  # Added new admin
ADMIN_SPECIAL = 7608325054  # Special admin with only /auto_start access
TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "6037 9975 9717 2684"
RENDER_BASE_URL = os.getenv("RENDER_BASE_URL") or "https://teaz.onrender.com"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_BASE_URL}{WEBHOOK_PATH}"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
app = FastAPI()
application = Application.builder().token(TOKEN).build()

# Database connection pool
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = None

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

# Database table creation
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
CREATE_CHANNELS_SQL = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id BIGINT PRIMARY KEY,
    channel_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        await db_execute(CREATE_CHANNELS_SQL)
        await db_execute(MIGRATE_SUBSCRIPTIONS_SQL)
        logging.info("Database tables created and migrated successfully")
    except Exception as e:
        logging.error(f"Error creating or migrating tables: {e}")

# Auto Start Command
auto_start_tasks = {}

async def auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_SPECIAL and user_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    if user_id in auto_start_tasks:
        await update.message.reply_text(
            "🔄 فرآیند استارت خودکار در حال اجرا است. آیا می‌خواهید آن را متوقف کنید؟",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🛑 متوقف کردن")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
        )
        user_states[user_id] = "awaiting_auto_start_stop"
        return
    async def send_start_periodically():
        while user_id in auto_start_tasks:
            await context.bot.send_message(chat_id=user_id, text="/start")
            await asyncio.sleep(300)  # 5 minutes
    task = asyncio.create_task(send_start_periodically())
    auto_start_tasks[user_id] = task
    await update.message.reply_text(
        "✅ فرآیند استارت خودکار فعال شد. هر ۵ دقیقه دستور /start ارسال می‌شود.",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

async def stop_auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_states.get(user_id) == "awaiting_auto_start_stop":
        if update.message.text == "🛑 متوقف کردن":
            if user_id in auto_start_tasks:
                auto_start_tasks[user_id].cancel()
                del auto_start_tasks[user_id]
                await update.message.reply_text("🛑 فرآیند استارت خودکار متوقف شد.", reply_markup=get_main_keyboard())
            else:
                await update.message.reply_text("⚠️ فرآیند استارت خودکار فعال نیست.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        elif update.message.text == "⬅️ بازگشت به منو":
            await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)

# New User Management Commands
async def list_balances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        users = await db_execute("SELECT user_id, username, balance FROM users ORDER BY balance DESC", fetch=True)
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            return
        response = "💰 موجودی کاربران:\n\n"
        for user in users:
            user_id, username, balance = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            response += f"کاربر: {username_display}\nموجودی: {balance:,} تومان\n--------------------\n"
        await send_long_message(update.effective_user.id, response, context, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن موجودی", callback_data="add_balance")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back")]
        ]))
    except Exception as e:
        logging.error(f"Error in list_balances: {e}")
        await update.message.reply_text("⚠️ خطا در نمایش موجودی‌ها.", reply_markup=get_main_keyboard())

async def list_user_types(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        users = await db_execute("SELECT user_id, username, is_agent FROM users ORDER BY created_at DESC", fetch=True)
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            return
        response = "👥 نوع کاربران:\n\n"
        for user in users:
            user_id, username, is_agent = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            user_type = "نماینده" if is_agent else "کاربر ساده"
            response += f"کاربر: {username_display}\nنوع: {user_type}\n--------------------\n"
        await send_long_message(update.effective_user.id, response, context, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error in list_user_types: {e}")
        await update.message.reply_text("⚠️ خطا در نمایش نوع کاربران.", reply_markup=get_main_keyboard())

async def set_user_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    await update.message.reply_text("🆔 آیدی کاربر را وارد کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_user_id_for_type"

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        await update.message.reply_text("🔄 در حال تهیه بکاپ از دیتابیس...")
        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tmp_file:
            backup_file = tmp_file.name
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name, db_user, db_password, db_host, db_port = parsed_url.path[1:], parsed_url.username, parsed_url.password, parsed_url.hostname, parsed_url.port or 5432
        cmd = ['pg_dump', '-h', db_host, '-p', str(db_port), '-U', db_user, '-d', db_name, '-f', backup_file, '-F', 'p']
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise Exception(f"Backup failed: {stderr.decode('utf-8') if stderr else 'Unknown error'}")
        with open(backup_file, 'rb') as file:
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=file,
                filename=f"teazvpn_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
                caption="✅ بکاپ از دیتابیس با موفقیت تهیه شد."
            )
        os.unlink(backup_file)
        await update.message.reply_text("✅ بکاپ با موفقیت تهیه و ارسال شد.", reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error in backup command: {e}")
        await update.message.reply_text(f"⚠️ خطا در تهیه بکاپ: {str(e)}", reply_markup=get_main_keyboard())

async def restore_database_from_backup(file_path: str):
    try:
        import urllib.parse
        parsed_url = urllib.parse.urlparse(DATABASE_URL)
        db_name, db_user, db_password, db_host, db_port = parsed_url.path[1:], parsed_url.username, parsed_url.password, parsed_url.hostname, parsed_url.port or 5432
        cmd = ['psql', '-h', db_host, '-p', str(db_port), '-U', db_user, '-d', db_name, '-f', file_path]
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise Exception(f"Restore failed: {stderr.decode('utf-8') if stderr else 'Unknown error'}")
        return True, "✅ دیتابیس با موفقیت بازیابی شد."
    except Exception as e:
        logging.error(f"Error restoring database: {e}")
        return False, f"⚠️ خطا در بازیابی دیتابیس: {str(e)}"

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    await update.message.reply_text("📤 لطفا فایل بکاپ دیتابیس را ارسال کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_backup_file"

async def notification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    await update.message.reply_text("📢 لطفا متن اطلاع‌رسانی را ارسال کنید:", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_notification_text"

async def coupon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    await update.message.reply_text("💵 مقدار تخفیف را به درصد وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
    user_states[update.effective_user.id] = "awaiting_coupon_discount"

async def numbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        users = await db_execute("SELECT user_id, username, phone FROM users ORDER BY created_at DESC", fetch=True)
        if not users:
            await update.message.reply_text("📂 هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
            return
        response = "📞 لیست شماره‌های کاربران:\n\n"
        for user in users:
            user_id, username, phone = user
            username_display = f"@{username}" if username else f"ID: {user_id}"
            phone_display = phone if phone else "نامشخص"
            response += f"کاربر: {username_display}\nشماره: {phone_display}\n--------------------\n"
        await send_long_message(update.effective_user.id, response, context, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error in numbers_command: {e}")
        await update.message.reply_text("⚠️ خطا در نمایش شماره‌ها.", reply_markup=get_main_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        total_users = await db_execute("SELECT COUNT(*) FROM users", fetchone=True)
        active_users = await db_execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE status = 'active' AND config IS NOT NULL", fetchone=True)
        inactive_users = total_users[0] - active_users[0] if total_users and active_users else 0
        today_users = await db_execute("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE", fetchone=True)
        today_income = await db_execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved' AND created_at >= CURRENT_DATE", fetchone=True)
        month_income = await db_execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved' AND created_at >= DATE_TRUNC('month', CURRENT_DATE)", fetchone=True)
        total_income = await db_execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved'", fetchone=True)
        plan_stats = await db_execute("SELECT plan, COUNT(*) as count FROM subscriptions WHERE config IS NOT NULL AND status = 'active' GROUP BY plan ORDER BY count DESC", fetch=True)
        best_selling_plan = plan_stats[0] if plan_stats else ("هیچ پلنی", 0)
        payment_methods = await db_execute("SELECT payment_method, COUNT(*) as count FROM payments WHERE status = 'approved' GROUP BY payment_method", fetch=True)
        total_payments = sum([pm[1] for pm in payment_methods]) if payment_methods else 1
        payment_methods_percent = [(pm[0], round((pm[1] / total_payments) * 100, 1)) for pm in payment_methods if pm[0] in ["card_to_card", "tron", "balance"]] if payment_methods else [("کارت به کارت", 0), ("ترون", 0), ("موجودی", 0)]
        method_names = {"card_to_card": "🏦 کارت به کارت", "tron": "💎 ترون", "balance": "💰 موجودی"}
        total_subs = await db_execute("SELECT COUNT(*) FROM subscriptions", fetchone=True)
        active_subs = await db_execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND config IS NOT NULL", fetchone=True)
        pending_subs = await db_execute("SELECT COUNT(*) FROM payments WHERE status = 'pending' AND type = 'buy_subscription'", fetchone=True)
        total_transactions = await db_execute("SELECT COUNT(*) FROM payments", fetchone=True)
        invited_users = await db_execute("SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL", fetchone=True)
        stats_message = "🌟 گزارش عملکرد تیز VPN 🚀\n\n"
        stats_message += f"👥 کاربران:\n  • کل کاربران: {total_users[0] if total_users else 0:,} نفر 🧑‍💻\n  • کاربران فعال: {active_users[0] if active_users else 0:,} نفر ✅\n  • کاربران غیرفعال: {inactive_users:,} نفر ❎\n  • کاربران جدید امروز: {today_users[0] if today_users else 0:,} نفر 🆕\n  • کاربران دعوت‌شده: {invited_users[0] if invited_users else 0:,} نفر 🤝\n\n"
        stats_message += f"💸 درآمد:\n  • امروز: {today_income[0] if today_income else 0:,} تومان 💰\n  • این ماه: {month_income[0] if month_income else 0:,} تومان 📈\n  • کل درآمد: {total_income[0] if total_income else 0:,} تومان 🔥\n\n"
        stats_message += f"📦 اشتراک‌ها:\n  • کل اشتراک‌ها: {total_subs[0] if total_subs else 0:,} عدد 📋\n  • اشتراک‌های فعال: {active_subs[0] if active_subs else 0:,} عدد 🟢\n  • اشتراک‌های در انتظار: {pending_subs[0] if pending_subs else 0:,} عدد ⏳\n  • پرفروش‌ترین پلن: {best_selling_plan[0]} ({best_selling_plan[1]:,} عدد) 🏆\n\n"
        stats_message += "💳 روش‌های پرداخت:\n"
        for method, percent in payment_methods_percent:
            display_name = method_names.get(method, method)
            stats_message += f"  • {display_name}: {percent}% 💸\n"
        stats_message += f"  • کل تراکنش‌ها: {total_transactions[0] if total_transactions else 0:,} عدد 🔄\n"
        await update.message.reply_text(stats_message, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error generating stats: {e}")
        await update.message.reply_text("⚠️ خطا در نمایش آمار.", reply_markup=get_main_keyboard())

async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        await db_execute("DELETE FROM coupons")
        await db_execute("DELETE FROM subscriptions")
        await db_execute("DELETE FROM payments")
        await db_execute("DELETE FROM users")
        await db_execute("DELETE FROM channels")
        logging.info("Database cleared successfully by admin")
        await update.message.reply_text("✅ دیتابیس با موفقیت پاک شد.", reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error clearing database: {e}")
        await update.message.reply_text(f"⚠️ خطا در پاک کردن دیتابیس: {str(e)}", reply_markup=get_main_keyboard())

# List Channels Command
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("❌ شما اجازه انجام این عملیات را ندارید.")
        return
    try:
        channels = await db_execute("SELECT channel_id, channel_name FROM channels", fetch=True)
        if not channels:
            msg = "📺 هیچ کانالی برای عضویت اجباری تنظیم نشده است."
        else:
            msg = "📺 کانال‌های اجباری:\n\n"
            for i, (channel_id, channel_name) in enumerate(channels, 1):
                msg += f"{i}. {channel_name} ({channel_id})\n"
        keyboard = [
            [InlineKeyboardButton("✅ افزودن کانال اجباری", callback_data="add_channel")],
            [InlineKeyboardButton("❌ حذف کانال اجباری", callback_data="remove_channel")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back")]
        ]
        await send_long_message(update.effective_user.id, msg, context, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logging.error(f"Error in list_channels: {e}")
        await update.message.reply_text("⚠️ خطا در نمایش کانال‌ها.", reply_markup=get_main_keyboard())

async def get_channels():
    try:
        return await db_execute("SELECT channel_id, channel_name FROM channels", fetch=True)
    except Exception as e:
        logging.error(f"Error fetching channels: {e}")
        return []

# Keyboards
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

# Helper function for long messages
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
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=reply_markup if i == len(messages) - 1 else None, parse_mode=parse_mode)

# Coupon DB functions
async def create_coupon(code, discount_percent, user_id=None):
    try:
        await db_execute("INSERT INTO coupons (code, discount_percent, user_id, is_used) VALUES (%s, %s, %s, FALSE)", (code, discount_percent, user_id))
        logging.info(f"Coupon {code} created with {discount_percent}% discount for user_id {user_id or 'all'}")
    except Exception as e:
        logging.error(f"Error creating coupon {code}: {e}")
        raise

async def validate_coupon(code, user_id):
    try:
        row = await db_execute("SELECT discount_percent, user_id, is_used, expiry_date FROM coupons WHERE code = %s", (code,), fetchone=True)
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

# Existing DB functions
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

async def ensure_user(user_id, username, invited_by=None):
    try:
        row = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        if not row:
            await db_execute("INSERT INTO users (user_id, username, invited_by, is_agent) VALUES (%s, %s, %s, FALSE)", (user_id, username, invited_by))
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

async def set_user_normal(user_id):
    try:
        await db_execute("UPDATE users SET is_agent = FALSE WHERE user_id = %s", (user_id,))
        logging.info(f"User {user_id} set as normal user")
    except Exception as e:
        logging.error(f"Error setting user {user_id} as normal user: {e}")

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
        await db_execute("INSERT INTO subscriptions (user_id, payment_id, plan, status, start_date, duration_days) VALUES (%s, %s, %s, 'pending', CURRENT_TIMESTAMP, %s)", (user_id, payment_id, plan, duration_days))
        logging.info(f"Subscription added for user_id {user_id}, payment_id: {payment_id}, plan: {plan}, duration: {duration_days} days")
    except Exception as e:
        logging.error(f"Error adding subscription for user_id {user_id}, payment_id: {payment_id}: {e}")
        raise

async def update_subscription_config(payment_id, config):
    try:
        await db_execute("UPDATE subscriptions SET config = %s, status = 'active' WHERE payment_id = %s", (config, payment_id))
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
        rows = await db_execute("SELECT s.id, s.plan, s.config, s.status, s.payment_id, s.start_date, s.duration_days, u.username FROM subscriptions s LEFT JOIN users u ON s.user_id = u.user_id WHERE s.user_id = %s ORDER BY s.status DESC, s.start_date DESC", (user_id,), fetch=True)
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
                    'id': sub_id, 'plan': plan, 'config': config, 'status': status,
                    'payment_id': payment_id, 'start_date': start_date, 'duration_days': duration_days,
                    'username': username, 'end_date': start_date + timedelta(days=duration_days)
                })
            except Exception as e:
                logging.error(f"Error processing subscription {sub_id} for user_id {user_id}: {e}")
                continue
        logging.info(f"Processed {len(subscriptions)} subscriptions for user_id {user_id}")
        return subscriptions
    except Exception as e:
        logging.error(f"Error in get_user_subscriptions for user_id {user_id}: {e}")
        return []

async def debug_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or update.effective_user.id == ADMIN_SPECIAL:
        await update.message.reply_text("⚠️ شما اجازه دسترسی به این دستور را ندارید.")
        return
    try:
        rows = await db_execute("SELECT s.user_id, u.username, s.plan, s.payment_id, s.start_date, s.duration_days, s.status FROM subscriptions s LEFT JOIN users u ON s.user_id = u.user_id ORDER BY s.status DESC, s.start_date DESC", fetch=True)
        if not rows:
            await update.message.reply_text("📂 هیچ اشتراکی برای هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
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
            response += f"کاربر: {username_display}\nاشتراک: {plan}\nکد خرید: #{payment_id}\nوضعیت: {'فعال' if status == 'active' else 'غیرفعال'}\nزمان باقی‌مانده: {remaining_days} روز\n--------------------\n"
        await send_long_message(update.effective_user.id, response, context, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Error in debug_subscriptions: {e}")
        await update.message.reply_text(f"⚠️ خطا در بررسی اشتراک‌ها: {str(e)}", reply_markup=get_main_keyboard())

user_states = {}

async def set_bot_commands():
    try:
        public_commands = [BotCommand(command="/start", description="شروع ربات")]
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
            BotCommand(command="/list_balances", description="نمایش موجودی کاربران (ادمین)"),
            BotCommand(command="/list_user_types", description="نمایش نوع کاربران (ادمین)"),
            BotCommand(command="/set_user_type", description="تنظیم نوع کاربر (ادمین)"),
            BotCommand(command="/list_channels", description="نمایش کانال‌های اجباری (ادمین)")
        ]
        special_admin_commands = [BotCommand(command="/auto_start", description="استارت خودکار ربات (ادمین ویژه)")]
        await application.bot.set_my_commands(public_commands)
        for admin_id in ADMIN_IDS:
            if admin_id == ADMIN_SPECIAL:
                await application.bot.set_my_commands(special_admin_commands, scope={"type": "chat", "chat_id": admin_id})
            else:
                await application.bot.set_my_commands(admin_commands, scope={"type": "chat", "chat_id": admin_id})
        logging.info("Bot commands set successfully")
    except Exception as e:
        logging.error(f"Error setting bot commands: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    if not await is_user_member(user_id):
        kb = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text("❌ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس مجدد /start را بزنید.", reply_markup=InlineKeyboardMarkup(kb))
        return
    invited_by = context.user_data.get("invited_by")
    await ensure_user(user_id, username, invited_by)
    phone = await get_user_phone(user_id)
    if phone:
        await update.message.reply_text("🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return
    contact_keyboard = ReplyKeyboardMarkup([[KeyboardButton("ارسال شماره تماس", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("✅ لطفا شماره تماس خود را ارسال کنید.", reply_markup=contact_keyboard)
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
    await context.bot.send_message(chat_id=ADMIN_IDS, text=f"📞 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) شماره تماس خود را ارسال کرد:\n{phone_number}")
    row = await db_execute("SELECT invited_by FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    invited_by = row[0] if row and row[0] else None
    if invited_by and invited_by != user_id:
        inviter_exists = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (invited_by,), fetchone=True)
        if inviter_exists:
            await context.bot.send_message(chat_id=invited_by, text=f"🎉 دوست شما (@{update.effective_user.username or 'NoUsername'}) با موفقیت مراحل ثبت‌نام را تکمیل کرد!\n💰 ۲۵,۰۰۰ تومان به موجودی شما اضافه شد.")
    await update.message.reply_text("🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:", reply_markup=get_main_keyboard())
    user_states.pop(user_id, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if user_states.get(user_id) == "awaiting_contact":
        contact_keyboard = ReplyKeyboardMarkup([[KeyboardButton("ارسال شماره تماس", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("⚠️ لطفا ابتدا شماره تماس خود را از طریق دکمه ارسال کنید.", reply_markup=contact_keyboard)
        return
    if text in ["بازگشت به منو", "⬅️ بازگشت به منو"]:
        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return
    if user_states.get(user_id) == "awaiting_backup_file":
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
                return
            except Exception as e:
                logging.error(f"Error in restore process: {e}")
                await update.message.reply_text(f"⚠️ خطا در بازیابی دیتابیس: {str(e)}", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
        else:
            await update.message.reply_text("⚠️ لطفا یک فایل بکاپ ارسال کنید.", reply_markup=get_back_keyboard())
            return
    if user_states.get(user_id) and (user_states.get(user_id).startswith("awaiting_deposit_receipt_") or user_states.get(user_id).startswith("awaiting_subscription_receipt_") or user_states.get(user_id).startswith("awaiting_agency_receipt_")):
        try:
            payment_id = int(user_states.get(user_id).split("_")[-1])
        except:
            payment_id = None
        if payment_id:
            payment = await db_execute("SELECT amount, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                amount, ptype, description = payment
                caption = f"💳 فیش پرداختی از کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}):\nمبلغ: {amount}\nنوع: {ptype if ptype != 'agency_request' else 'درخواست نمایندگی'}"
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید", callback_data=f"approve_{payment_id}"), InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")]])
                if update.message.photo:
                    file_id = update.message.photo[-1].file_id
                    await context.bot.send_photo(chat_id=ADMIN_IDS, photo=file_id, caption=caption, reply_markup=keyboard)
                else:
                    doc_id = update.message.document.file_id
                    await context.bot.send_document(chat_id=ADMIN_IDS, document=doc_id, caption=caption, reply_markup=keyboard)
                await update.message.reply_text("✅ فیش شما برای ادمین ارسال شد، لطفا منتظر تایید باشید.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_config_"):
        try:
            payment_id = int(user_states.get(user_id).split("_")[-1])
        except:
            payment_id = None
        if payment_id:
            payment = await db_execute("SELECT user_id, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, description = payment
                if update.message.text:
                    config = update.message.text
                    await update_subscription_config(payment_id, config)
                    await context.bot.send_message(chat_id=buyer_id, text=f"✅ کانفیگ اشتراک شما ({description})\nکد خرید: #{payment_id}\nدریافت شد:\n```\n{config}\n```", parse_mode="Markdown")
                    await update.message.reply_text("✅ کانفیگ با موفقیت به خریدار ارسال شد.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                else:
                    await update.message.reply_text("⚠️ لطفا کانفیگ را به صورت متن ارسال کنید.")
                return
    elif user_states.get(user_id) == "awaiting_coupon_discount" and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        if text.isdigit():
            discount_percent = int(text)
            if 0 < discount_percent <= 100:
                coupon_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                user_states[user_id] = f"awaiting_coupon_recipient_{coupon_code}_{discount_percent}"
                await update.message.reply_text(f"💵 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف ایجاد شد.\nبرای چه کسانی ارسال شود؟", reply_markup=get_coupon_recipient_keyboard(), parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ درصد تخفیف باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_coupon_recipient_") and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        parts = user_states.get(user_id).split("_")
        coupon_code, discount_percent = parts[3], int(parts[4])
        if text == "📢 برای همه":
            try:
                await create_coupon(coupon_code, discount_percent)
                users = await db_execute("SELECT user_id FROM users WHERE is_agent = FALSE", fetch=True)
                if not users:
                    await update.message.reply_text("⚠️ هیچ کاربری (غیر از نمایندگان) یافت نشد.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    return
                sent_count = 0
                for user in users:
                    try:
                        await context.bot.send_message(chat_id=user[0], text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.", parse_mode="Markdown")
                        sent_count += 1
                    except Exception as e:
                        logging.error(f"Error sending coupon to user_id {user[0]}: {e}")
                        continue
                await update.message.reply_text(f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر (غیر از نمایندگان) ارسال شد.", reply_markup=get_main_keyboard(), parse_mode="Markdown")
                user_states.pop(user_id, None)
            except Exception as e:
                logging.error(f"Error sending coupons to all users: {e}")
                await update.message.reply_text("⚠️ خطا در ارسال کد تخفیف برای همه کاربران.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            return
        elif text == "👤 برای یک نفر":
            target_user_id = 6056483071
            user = await db_execute("SELECT user_id, is_agent FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if user:
                _, is_agent = user
                if is_agent:
                    await update.message.reply_text("⚠️ این کاربر نماینده است و نمی‌تواند کد تخفیف دریافت کند.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    return
                await create_coupon(coupon_code, discount_percent, target_user_id)
                await context.bot.send_message(chat_id=target_user_id, text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.", parse_mode="Markdown")
                await update.message.reply_text(f"✅ کد تخفیف `{coupon_code}` برای کاربر با ID {target_user_id} ارسال شد.", reply_markup=get_main_keyboard(), parse_mode="Markdown")
                user_states.pop(user_id, None)
            else:
                await update.message.reply_text(f"⚠️ کاربری با ID {target_user_id} یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            return
        elif text == "🎯 درصد خاصی از کاربران":
            user_states[user_id] = f"awaiting_coupon_percent_{coupon_code}_{discount_percent}"
            await update.message.reply_text("📊 درصد کاربران را وارد کنید (مثال: 20):", reply_markup=get_back_keyboard())
            return
        else:
            await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=get_coupon_recipient_keyboard())
            return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_coupon_percent_") and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        parts = user_states.get(user_id).split("_")
        coupon_code, discount_percent = parts[3], int(parts[4])
        if text.isdigit():
            percent = int(text)
            if 0 < percent <= 100:
                try:
                    users = await db_execute("SELECT user_id FROM users WHERE is_agent = FALSE", fetch=True)
                    if not users:
                        await update.message.reply_text("⚠️ هیچ کاربری (غیر از نمایندگان) یافت نشد.", reply_markup=get_main_keyboard())
                        user_states.pop(user_id, None)
                        return
                    total_users = len(users)
                    num_users = max(1, round(total_users * (percent / 100)))
                    selected_users = random.sample(users, min(num_users, total_users))
                    await create_coupon(coupon_code, discount_percent)
                    sent_count = 0
                    for user in selected_users:
                        try:
                            await context.bot.send_message(chat_id=user[0], text=f"🎉 کد تخفیف `{coupon_code}` با {discount_percent}% تخفیف برای شما!\n⏳ این کد فقط تا ۳ روز اعتبار دارد.\nفقط یک بار قابل استفاده است.", parse_mode="Markdown")
                            sent_count += 1
                        except Exception as e:
                            logging.error(f"Error sending coupon to user_id {user[0]}: {e}")
                            continue
                    await update.message.reply_text(f"✅ کد تخفیف `{coupon_code}` برای {sent_count} کاربر ({percent}% از کاربران غیر نماینده) ارسال شد.", reply_markup=get_main_keyboard(), parse_mode="Markdown")
                    user_states.pop(user_id, None)
                except Exception as e:
                    logging.error(f"Error sending coupons to {percent}% of users: {e}")
                    await update.message.reply_text("⚠️ خطا در ارسال کد تخفیف برای درصد مشخصی از کاربران.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
            else:
                await update.message.reply_text("⚠️ درصد باید بین 1 تا 100 باشد.", reply_markup=get_back_keyboard())
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) == "awaiting_notification_text" and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        notification_text = text
        await update.message.reply_text("📢 آیا مطمئن هستید که می‌خواهید این اطلاعیه را برای همه کاربران ارسال کنید؟", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ بله، ارسال کن")], [KeyboardButton("❌ خیر، انصراف")]], resize_keyboard=True))
        user_states[user_id] = f"confirm_notification_{notification_text}"
        return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("confirm_notification_") and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        notification_text = user_states.get(user_id).split("_", 2)[2]
        if text == "✅ بله، ارسال کن":
            try:
                users = await db_execute("SELECT user_id FROM users", fetch=True)
                if not users:
                    await update.message.reply_text("⚠️ هیچ کاربری یافت نشد.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                    return
                sent_count, failed_count = 0, 0
                for user in users:
                    try:
                        await context.bot.send_message(chat_id=user[0], text=f"📢 اطلاعیه از مدیریت:\n\n{notification_text}")
                        sent_count += 1
                    except Exception as e:
                        logging.error(f"Error sending notification to user_id {user[0]}: {e}")
                        failed_count += 1
                        continue
                await update.message.reply_text(f"✅ اطلاعیه با موفقیت به {sent_count} کاربر ارسال شد.\n❌ تعداد کاربرانی که دریافت نکردند: {failed_count}", reply_markup=get_main_keyboard())
            except Exception as e:
                logging.error(f"Error sending notifications: {e}")
                await update.message.reply_text("⚠️ خطا در ارسال اطلاعیه به کاربران.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("❌ ارسال اطلاعیه لغو شد.", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return
    elif user_states.get(user_id) == "awaiting_user_id_for_type" and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if not user:
                await update.message.reply_text("⚠️ کاربری با این آیدی یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
            await update.message.reply_text("نوع کاربر را انتخاب کنید:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("نماینده"), KeyboardButton("کاربر ساده")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True))
            user_states[user_id] = f"awaiting_type_selection_{target_user_id}"
        else:
            await update.message.reply_text("⚠️ لطفا یک آیدی معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_type_selection_") and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        target_user_id = int(user_states.get(user_id).split("_")[-1])
        if text == "نماینده":
            await set_user_agent(target_user_id)
            await update.message.reply_text(f"✅ کاربر {target_user_id} به عنوان نماینده تنظیم شد.", reply_markup=get_main_keyboard())
        elif text == "کاربر ساده":
            await set_user_normal(target_user_id)
            await update.message.reply_text(f"✅ کاربر {target_user_id} به عنوان کاربر ساده تنظیم شد.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("⚠️ لطفا یکی از گزینه‌های بالا را انتخاب کنید.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("نماینده"), KeyboardButton("کاربر ساده")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True))
            return
        user_states.pop(user_id, None)
        return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_coupon_code_"):
        parts = user_states.get(user_id).split("_")
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
            await update.message.reply_text(f"⚠️ {error}\nلطفا کد معتبر وارد کنید یا برای ادامه روی 'ادامه' کلیک کنید:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True))
            return
        discounted_amount = int(amount * (1 - discount_percent / 100))
        user_states[user_id] = f"awaiting_payment_method_{discounted_amount}_{plan}_{coupon_code}"
        await update.message.reply_text(f"✅ کد تخفیف اعمال شد! مبلغ با {discount_percent}% تخفیف: {discounted_amount} تومان\nروش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
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
                await update.message.reply_text(f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`\n\nیا\n\n🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ", reply_markup=get_back_keyboard(), parse_mode="MarkdownV2")
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
    if text in ["🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره", "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره", "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره", "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره", "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره", "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره"]:
        mapping = {
            "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": (90000, 0), "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": (250000, 1), "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": (450000, 2),
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (70000, 0), "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (210000, 1), "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": (380000, 2)
        }
        amount, plan_index = mapping.get(text, (0, -1))
        if plan_index == -1:
            await update.message.reply_text("⚠️ خطا در انتخاب پلن. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        is_agent = await is_user_agent(user_id)
        if not is_agent:
            await update.message.reply_text(f"💵 اگر کد تخفیف دارید، وارد کنید. در غیر این صورت برای ادامه روی 'ادامه' کلیک کنید:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("ادامه")], [KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True))
            user_states[user_id] = f"awaiting_coupon_code_{amount}_{text}"
        else:
            user_states[user_id] = f"awaiting_payment_method_{amount}_{text}"
            await update.message.reply_text("💳 روش خرید را انتخاب کنید:", reply_markup=get_payment_method_keyboard())
        return
    if user_states.get(user_id, "").startswith("awaiting_payment_method_"):
        state = user_states.get(user_id)
        try:
            parts = state.split("_")
            amount = int(parts[3])
            plan = "_".join(parts[4:]) if len(parts) <= 5 else "_".join(parts[4:-1])
            coupon_code = parts[-1] if len(parts) > 5 else None
            if text == "🏦 کارت به کارت":
                payment_id = await add_payment(user_id, amount, "buy_subscription", "card_to_card", description=plan, coupon_code=coupon_code)
                if payment_id:
                    await add_subscription(user_id, payment_id, plan)
                    await update.message.reply_text(f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ", reply_markup=get_back_keyboard(), parse_mode="MarkdownV2")
                    user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
                else:
                    await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                return
            if text == "💎 پرداخت با ترون":
                payment_id = await add_payment(user_id, amount, "buy_subscription", "tron", description=plan, coupon_code=coupon_code)
                if payment_id:
                    await add_subscription(user_id, payment_id, plan)
                    await update.message.reply_text(f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n\n💎 آدرس کیف پول TRON:\n`{TRON_ADDRESS}`", reply_markup=get_back_keyboard(), parse_mode="MarkdownV2")
                    user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
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
                        await update.message.reply_text("✅ خرید شما با موفقیت انجام شد. حداکثر تا ۱ ساعت دیگر کانفیگ برای شما ارسال خواهد شد.", reply_markup=get_main_keyboard())
                        await context.bot.send_message(chat_id=ADMIN_IDS, text=f"📢 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) با موجودی خود سرویس {plan} خریداری کرد.")
                        config_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"send_config_{payment_id}")]])
                        await context.bot.send_message(chat_id=ADMIN_IDS, text=f"✅ پرداخت برای اشتراک ({plan}) تایید شد.", reply_markup=config_keyboard)
                        user_states.pop(user_id, None)
                    else:
                        await update.message.reply_text("⚠️ خطا در ثبت پرداخت. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
                        user_states.pop(user_id, None)
                else:
                    await update.message.reply_text(f"⚠️ موجودی شما ({balance} تومان) کافی نیست. لطفا ابتدا موجودی خود را افزایش دهید.", reply_markup=get_main_keyboard())
                    user_states.pop(user_id, None)
                return
        except Exception as e:
            logging.error(f"Error processing payment method for user_id {user_id}, state: {state}, error: {e}")
            await update.message.reply_text("⚠️ خطا در پردازش. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            return
    if text == "🎁 اشتراک تست رایگان":
        await update.message.reply_text("🎁 برای دریافت اشتراک تست رایگان، لطفا با پشتیبانی تماس بگیرید: https://t.me/teazadmin", reply_markup=get_main_keyboard())
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
                await context.bot.send_photo(chat_id=user_id, photo=photo, caption=f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\nبرای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد.", reply_markup=get_main_keyboard())
        except Exception as e:
            logging.error(f"Error sending invite image: {e}")
            await update.message.reply_text(f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\nبرای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد.", reply_markup=get_main_keyboard())
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
                    response += f"🔹 اشتراک #{sub['id']}\n📌 پلن: {sub['plan']}\n🆔 کد خرید: #{sub['payment_id']}\n📊 وضعیت: {'✅ فعال' if sub['status'] == 'active' else '⏳ در انتظار'}\n"
                    if sub['status'] == "active":
                        remaining_days = max(0, (sub['end_date'] - current_time).days)
                        response += f"⏳ زمان باقی‌مانده: {remaining_days} روز\n"
                        if sub['config']:
                            response += f"⚙️ کانفیگ:\n```\n{sub['config']}\n```\n"
                        response += f"📅 تاریخ شروع: {sub['start_date'].strftime('%Y-%m-%d %H:%M:%S')}\n📅 تاریخ پایان: {sub['end_date'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                    response += "--------------------\n"
                except Exception as e:
                    logging.error(f"Error processing subscription {sub['id']} for user_id {user_id}: {e}")
                    continue
            await send_long_message(user_id, response, context, reply_markup=get_main_keyboard(), parse_mode="Markdown")
            user_states.pop(user_id, None)
        except Exception as e:
            logging.error(f"Error displaying subscriptions for user_id {user_id}: {e}")
            await update.message.reply_text("⚠️ خطا در نمایش اشتراک‌ها.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        return
    if text == "💡 راهنمای اتصال":
        await update.message.reply_text("💡 نوع دستگاه خود را انتخاب کنید:", reply_markup=get_connection_guide_keyboard())
        user_states.pop(user_id, None)
        return
    if text in ["📗 اندروید", "📕 آیفون/مک", "📘 ویندوز", "📙 لینوکس"]:
        guides = {
            "📗 اندروید": "📗 راهنمای اتصال برای اندروید:\n۱. اپلیکیشن v2rayNG را از گوگل پلی یا https://t.me/teazvpn_channel دانلود کنید.\n۲. کانفیگ دریافتی را کپی کنید.\n۳. در اپلیکیشن، کانفیگ را وارد کنید و متصل شوید.\n۴. در صورت مشکل، با پشتیبانی تماس بگیرید: https://t.me/teazadmin",
            "📕 آیفون/مک": "📕 راهنمای اتصال برای آیفون/مک:\n۱. اپلیکیشن Shadowrocket یا Fair VPN را از اپ استور دانلود کنید.\n۲. کانفیگ دریافتی را کپی کنید.\n۳. در اپلیکیشن، کانفیگ را وارد کنید و متصل شوید.\n۴. در صورت مشکل، با پشتیبانی تماس بگیرید: https://t.me/teazadmin",
            "📘 ویندوز": "📘 راهنمای اتصال برای ویندوز:\n۱. نرم‌افزار v2rayN را از https://t.me/teazvpn_channel دانلود کنید.\n۲. کانفیگ دریافتی را کپی کنید.\n۳. در نرم‌افزار، کانفیگ را وارد کنید و متصل شوید.\n۴. در صورت مشکل، با پشتیبانی تماس بگیرید: https://t.me/teazadmin",
            "📙 لینوکس": "📙 راهنمای اتصال برای لینوکس:\n۱. نرم‌افزار v2ray را نصب کنید (دستورات نصب در https://t.me/teazvpn_channel).\n۲. کانفیگ دریافتی را کپی کنید.\n۳. کانفیگ را در نرم‌افزار وارد کنید و متصل شوید.\n۴. در صورت مشکل، با پشتیبانی تماس بگیرید: https://t.me/teazadmin"
        }
        await update.message.reply_text(guides[text], reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return
    if text == "🧑‍💼 درخواست نمایندگی":
        payment_id = await add_payment(user_id, 1000000, "agency_request", "card_to_card", description="درخواست نمایندگی")
        if payment_id:
            await update.message.reply_text(f"لطفا ۱,۰۰۰,۰۰۰ تومان برای درخواست نمایندگی واریز کنید و فیش را ارسال کنید:\n\n🏦 شماره کارت بانکی:\n`{BANK_CARD}`\nفرهنگ", reply_markup=get_back_keyboard(), parse_mode="MarkdownV2")
            user_states[user_id] = f"awaiting_agency_receipt_{payment_id}"
        else:
            await update.message.reply_text("⚠️ خطا در ثبت درخواست. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS or user_id == ADMIN_SPECIAL:
        await query.message.reply_text("⚠️ شما اجازه انجام این عملیات را ندارید.", reply_markup=get_main_keyboard())
        await query.answer()
        return
    data = query.data
    if data.startswith("approve_"):
        try:
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, amount, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, amount, ptype, description = payment
                await update_payment_status(payment_id, "approved")
                if ptype == "increase_balance":
                    await add_balance(buyer_id, amount)
                    await context.bot.send_message(chat_id=buyer_id, text=f"✅ پرداخت شما به مبلغ {amount} تومان تایید شد و به موجودی شما اضافه شد.")
                elif ptype == "buy_subscription":
                    config_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"send_config_{payment_id}")]])
                    await context.bot.send_message(chat_id=ADMIN_IDS, text=f"✅ پرداخت برای اشتراک ({description}) تایید شد.", reply_markup=config_keyboard)
                    await context.bot.send_message(chat_id=buyer_id, text=f"✅ پرداخت شما برای اشتراک ({description}) تایید شد.\nحداکثر تا ۱ ساعت دیگر کانفیگ برای شما ارسال خواهد شد.")
                elif ptype == "agency_request":
                    await set_user_agent(buyer_id)
                    await context.bot.send_message(chat_id=buyer_id, text="✅ درخواست نمایندگی شما تایید شد. از این پس می‌توانید از تخفیفات نمایندگی استفاده کنید.")
                await query.message.edit_reply_markup(reply_markup=None)
                await query.answer("✅ پرداخت تایید شد.")
            else:
                await query.answer("⚠️ پرداخت یافت نشد.")
        except Exception as e:
            logging.error(f"Error approving payment: {e}")
            await query.answer("⚠️ خطا در تایید پرداخت.")
    elif data.startswith("reject_"):
        try:
            payment_id = int(data.split("_")[1])
            payment = await db_execute("SELECT user_id, type, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, ptype, description = payment
                await update_payment_status(payment_id, "rejected")
                await context.bot.send_message(chat_id=buyer_id, text=f"❌ پرداخت شما برای {'اشتراک ' + description if ptype == 'buy_subscription' else 'افزایش موجودی' if ptype == 'increase_balance' else 'درخواست نمایندگی'} رد شد. لطفا با پشتیبانی تماس بگیرید.")
                await query.message.edit_reply_markup(reply_markup=None)
                await query.answer("❌ پرداخت رد شد.")
            else:
                await query.answer("⚠️ پرداخت یافت نشد.")
        except Exception as e:
            logging.error(f"Error rejecting payment: {e}")
            await query.answer("⚠️ خطا در رد پرداخت.")
    elif data.startswith("send_config_"):
        try:
            payment_id = int(data.split("_")[2])
            payment = await db_execute("SELECT user_id, description FROM payments WHERE id = %s", (payment_id,), fetchone=True)
            if payment:
                buyer_id, description = payment
                await query.message.reply_text(f"لطفا کانفیگ برای اشتراک ({description}) را ارسال کنید:", reply_markup=get_back_keyboard())
                user_states[user_id] = f"awaiting_config_{payment_id}"
                await query.message.edit_reply_markup(reply_markup=None)
                await query.answer("🟣 در انتظار کانفیگ...")
            else:
                await query.answer("⚠️ پرداخت یافت نشد.")
        except Exception as e:
            logging.error(f"Error initiating config send: {e}")
            await query.answer("⚠️ خطا در ارسال کانفیگ.")
    elif data == "add_balance":
        try:
            await query.message.reply_text("🆔 آیدی کاربر را وارد کنید:", reply_markup=get_back_keyboard())
            user_states[user_id] = "awaiting_user_id_for_balance"
            await query.answer()
        except Exception as e:
            logging.error(f"Error initiating add balance: {e}")
            await query.answer("⚠️ خطا در افزودن موجودی.")
    elif data == "add_channel":
        try:
            await query.message.reply_text("📺 آیدی کانال را وارد کنید (مثال: @ChannelName یا ID عددی):", reply_markup=get_back_keyboard())
            user_states[user_id] = "awaiting_channel_id"
            await query.answer()
        except Exception as e:
            logging.error(f"Error initiating add channel: {e}")
            await query.answer("⚠️ خطا در افزودن کانال.")
    elif data == "remove_channel":
        try:
            channels = await get_channels()
            if not channels:
                await query.message.reply_text("📺 هیچ کانالی برای حذف وجود ندارد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                await query.answer()
                return
            keyboard = [[InlineKeyboardButton(f"{channel_name} ({channel_id})", callback_data=f"delete_channel_{channel_id}")] for channel_id, channel_name in channels]
            keyboard.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back")])
            await query.message.reply_text("📺 کانالی که می‌خواهید حذف کنید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
            await query.answer()
        except Exception as e:
            logging.error(f"Error initiating remove channel: {e}")
            await query.answer("⚠️ خطا در حذف کانال.")
    elif data.startswith("delete_channel_"):
        try:
            channel_id = data.split("_")[2]
            await db_execute("DELETE FROM channels WHERE channel_id = %s", (channel_id,))
            await query.message.reply_text(f"✅ کانال {channel_id} با موفقیت حذف شد.", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            await query.answer()
        except Exception as e:
            logging.error(f"Error deleting channel: {e}")
            await query.answer("⚠️ خطا در حذف کانال.")
    elif data == "back":
        try:
            await query.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
            user_states.pop(user_id, None)
            await query.message.edit_reply_markup(reply_markup=None)
            await query.answer()
        except Exception as e:
            logging.error(f"Error returning to main menu: {e}")
            await query.answer("⚠️ خطا در بازگشت به منو.")
    else:
        await query.answer("⚠️ عملیات نامعتبر.")

async def message_handler_continued(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if user_states.get(user_id) == "awaiting_user_id_for_balance" and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        if text.isdigit():
            target_user_id = int(text)
            user = await db_execute("SELECT user_id FROM users WHERE user_id = %s", (target_user_id,), fetchone=True)
            if not user:
                await update.message.reply_text("⚠️ کاربری با این آیدی یافت نشد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
                return
            await update.message.reply_text("💰 مبلغ را به تومان وارد کنید (مثال: 100000):", reply_markup=get_back_keyboard())
            user_states[user_id] = f"awaiting_balance_amount_{target_user_id}"
        else:
            await update.message.reply_text("⚠️ لطفا یک آیدی معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) and user_states.get(user_id).startswith("awaiting_balance_amount_") and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        target_user_id = int(user_states.get(user_id).split("_")[-1])
        if text.isdigit():
            amount = int(text)
            await add_balance(target_user_id, amount)
            await update.message.reply_text(f"✅ مبلغ {amount} تومان به موجودی کاربر {target_user_id} اضافه شد.", reply_markup=get_main_keyboard())
            await context.bot.send_message(chat_id=target_user_id, text=f"💰 مبلغ {amount} تومان به موجودی شما اضافه شد.")
            user_states.pop(user_id, None)
        else:
            await update.message.reply_text("⚠️ لطفا یک عدد معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) == "awaiting_channel_id" and user_id in ADMIN_IDS and user_id != ADMIN_SPECIAL:
        channel_id = text.strip()
        if channel_id.startswith("@"):
            try:
                chat = await context.bot.get_chat(channel_id)
                channel_id_num = chat.id
                channel_name = chat.title or channel_id
                await db_execute("INSERT INTO channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT (channel_id) DO NOTHING", (channel_id_num, channel_name))
                await update.message.reply_text(f"✅ کانال {channel_name} ({channel_id_num}) به لیست کانال‌های اجباری اضافه شد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            except Exception as e:
                logging.error(f"Error adding channel {channel_id}: {e}")
                await update.message.reply_text("⚠️ خطا در افزودن کانال. لطفا آیدی معتبر وارد کنید.", reply_markup=get_back_keyboard())
        else:
            try:
                channel_id_num = int(channel_id)
                chat = await context.bot.get_chat(channel_id_num)
                channel_name = chat.title or channel_id
                await db_execute("INSERT INTO channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT (channel_id) DO NOTHING", (channel_id_num, channel_name))
                await update.message.reply_text(f"✅ کانال {channel_name} ({channel_id_num}) به لیست کانال‌های اجباری اضافه شد.", reply_markup=get_main_keyboard())
                user_states.pop(user_id, None)
            except Exception as e:
                logging.error(f"Error adding channel {channel_id}: {e}")
                await update.message.reply_text("⚠️ خطا در افزودن کانال. لطفا آیدی معتبر وارد کنید.", reply_markup=get_back_keyboard())
        return
    elif user_states.get(user_id) == "awaiting_auto_start_stop":
        await stop_auto_start(update, context)
        return

async def webhook_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message:
            if update.message.contact:
                await contact_handler(update, context)
            else:
                await message_handler(update, context)
                await message_handler_continued(update, context)
        elif update.callback_query:
            await callback_handler(update, context)
    except Exception as e:
        logging.error(f"Error in webhook_update: {e}")
        try:
            if update.message:
                await update.message.reply_text("⚠️ خطایی رخ داد. لطفا دوباره تلاش کنید.", reply_markup=get_main_keyboard())
            elif update.callback_query:
                await update.callback_query.answer("⚠️ خطایی رخ داد.")
        except Exception as e2:
            logging.error(f"Error sending error message: {e2}")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    update = Update.de_json(await request.json(), application.bot)
    await webhook_update(update, application)
    return {"ok": True}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

async def main():
    try:
        init_db_pool()
        await create_tables()
        await set_bot_commands()
        await application.bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Error in main setup: {e}")
        close_db_pool()
        raise

if __name__ == "__main__":
    import uvicorn
    asyncio.run(main())
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
