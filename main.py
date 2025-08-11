import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------------- دیتابیس ----------------------
import asyncpg

async def db_connect():
    return await asyncpg.connect(
        user='postgres',
        password='1234',
        database='vpn_bot',
        host='localhost'
    )

async def db_execute(query, params=(), fetch=False):
    conn = await db_connect()
    try:
        if fetch:
            result = await conn.fetch(query, *params)
        else:
            result = await conn.execute(query, *params)
    finally:
        await conn.close()
    return result

# ---------------------- ایجاد جداول ----------------------
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

async def setup_db():
    await db_execute(CREATE_USERS_SQL)
    await db_execute(CREATE_PAYMENTS_SQL)
    await db_execute(CREATE_SUBSCRIPTIONS_SQL)

# ---------------------- مدیریت اشتراک ----------------------
async def add_subscription(user_id, payment_id, plan):
    days_map = {
        "۱ ماهه: ۹۰ هزار تومان": 30,
        "۳ ماهه: ۲۵۰ هزار تومان": 90,
        "۶ ماهه: ۴۵۰ هزار تومان": 180
    }
    days = days_map.get(plan, 30)
    start_date = datetime.datetime.utcnow()
    end_date = start_date + datetime.timedelta(days=days)
    await db_execute(
        "INSERT INTO subscriptions (user_id, payment_id, plan, status, start_date, end_date) VALUES ($1, $2, $3, 'active', $4, $5)",
        (user_id, payment_id, plan, start_date, end_date)
    )

async def get_user_subscriptions(user_id):
    rows = await db_execute("SELECT id, plan, config, status, payment_id, start_date, end_date FROM subscriptions WHERE user_id = $1", (user_id,), fetch=True)
    now = datetime.datetime.utcnow()
    updated_rows = []
    for row in rows:
        sub_id, plan, config, status, payment_id, start_date, end_date = row
        if end_date and now > end_date and status != "inactive":
            await db_execute("UPDATE subscriptions SET status = 'inactive' WHERE id = $1", (sub_id,))
            status = "inactive"
        updated_rows.append((sub_id, plan, config, status, payment_id, start_date, end_date))
    return updated_rows

# ---------------------- کیبورد اصلی ----------------------
from telegram import ReplyKeyboardMarkup

def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📂 اشتراک‌های من", "💳 افزایش موجودی"],
        ["📦 خرید اشتراک", "📞 پشتیبانی"]
    ], resize_keyboard=True)

# ---------------------- هندلر پیام‌ها ----------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.message.from_user.id

    if text == "📂 اشتراک‌های من":
        subscriptions = await get_user_subscriptions(user_id)
        if not subscriptions:
            await update.message.reply_text("📂 شما هنوز اشتراکی ندارید.", reply_markup=get_main_keyboard())
            return
        response = "📂 اشتراک‌های شما:\n\n"
        now = datetime.datetime.utcnow()
        for sub in subscriptions:
            sub_id, plan, config, status, payment_id, start_date, end_date = sub
            response += f"🔹 اشتراک: {plan}\nکد خرید: #{payment_id}\nوضعیت: {'فعال' if status == 'active' else 'غیرفعال'}\n"
            if status == "active" and end_date:
                remaining_days = (end_date - now).days
                response += f"⏳ زمان باقی‌مانده: {remaining_days} روز\n"
            if config:
                response += f"کانفیگ:\n```\n{config}\n```\n"
            response += "--------------------\n"
        await update.message.reply_text(response, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return

# ---------------------- استارت ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    await db_execute("INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING", (user_id, username))
    await update.message.reply_text("👋 سلام! به ربات خوش اومدی.", reply_markup=get_main_keyboard())

# ---------------------- اجرای ربات ----------------------
async def main():
    await setup_db()
    app = Application.builder().token("YOUR_TELEGRAM_BOT_TOKEN").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
