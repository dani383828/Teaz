import os
import logging
import asyncio
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
)

# ---------- تنظیمات اولیه ----------
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jZNg"
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
    status TEXT DEFAULT 'active'
)
"""

async def create_tables():
    await db_execute(CREATE_USERS_SQL)
    await db_execute(CREATE_PAYMENTS_SQL)
    await db_execute(CREATE_SUBSCRIPTIONS_SQL)

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
    await db_execute(
        "INSERT INTO subscriptions (user_id, payment_id, plan, status) VALUES (%s, %s, %s, 'active')",
        (user_id, payment_id, plan)
    )

async def update_subscription_config(payment_id, config):
    await db_execute("UPDATE subscriptions SET config = %s WHERE payment_id = %s", (config, payment_id))

async def update_payment_status(payment_id, status):
    await db_execute("UPDATE payments SET status = %s WHERE id = %s", (status, payment_id))

async def get_user_subscriptions(user_id):
    rows = await db_execute("SELECT id, plan, config, status, payment_id FROM subscriptions WHERE user_id = %s", (user_id,), fetch=True)
    return rows

user_states = {}

async def set_bot_commands():
    commands = [BotCommand(command="/start", description="شروع ربات")]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""

    if not await is_user_member(user_id):
        kb = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text(
            "❌ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس مجدد /start را بزنید.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    invited_by = context.user_data.get("invited_by")
    await ensure_user(user_id, username, invited_by)

    phone = await get_user_phone(user_id)
    if phone:
        await update.message.reply_text(
            f"🌐 به فروشگاه VPN ما خوش آمدید!\nشماره تماس شما: {phone}\nیک گزینه را انتخاب کنید:",
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
        "🌐 به فروشگاه VPN ما خوش آمدید!\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""

    # اگر دکمه بازگشت به منو بود و در انتظار فیش، وضعیت رو پاک کن و منو رو نمایش بده
    if text == "بازگشت به منو":
        if user_states.get(user_id, "").startswith("awaiting_deposit_receipt_") or \
           user_states.get(user_id, "").startswith("awaiting_subscription_receipt_"):
            user_states.pop(user_id, None)
            await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
            return

        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return

    if update.message.photo or update.message.document or update.message.text:
        state = user_states.get(user_id)
        if state and (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_")):
            try:
                payment_id = int(state.split("_")[-1])
            except:
                payment_id = None

            if payment_id:
                payment = await db_execute("SELECT amount, type FROM payments WHERE id = %s", (payment_id,), fetchone=True)
                if payment:
                    amount, ptype = payment
                    caption = f"💳 فیش پرداختی از کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}):\n"
                    caption += f"مبلغ: {amount}\nنوع: {'افزایش موجودی' if ptype == 'increase_balance' else 'خرید اشتراک'}"

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

    if text == "💰 موجودی":
        await update.message.reply_text("💰 بخش موجودی:\nیک گزینه را انتخاب کنید:", reply_markup=get_balance_keyboard())
        return

    if text == "نمایش موجودی":
        bal = await get_balance(user_id)
        await update.message.reply_text(f"💰 موجودی شما: {bal} تومان", reply_markup=get_balance_keyboard())
        return

    if text == "افزایش موجودی":
        await update.message.reply_text("💳 لطفا مبلغ واریزی را وارد کنید:", reply_markup=get_back_keyboard())
        user_states[user_id] = "awaiting_deposit_amount"
        return

    if user_states.get(user_id) == "awaiting_deposit_amount":
        if text.isdigit():
            amount = int(text)
            payment_id = await add_payment(user_id, amount, "increase_balance")
            await update.message.reply_text(
                f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n💎 {TRON_ADDRESS}\nیا\n🏦 {BANK_CARD}",
                reply_markup=get_back_keyboard()
            )
            user_states[user_id] = f"awaiting_deposit_receipt_{payment_id}"
        else:
            await update.message.reply_text("⚠️ لطفا عدد وارد کنید.")
        return

    if text == "💳 خرید اشتراک":
        await update.message.reply_text("💳 پلن را انتخاب کنید:", reply_markup=get_subscription_keyboard())
        return

    if text in ["۱ ماهه: ۹۰ هزار تومان", "۳ ماهه: ۲۵۰ هزار تومان", "۶ ماهه: ۴۵۰ هزار تومان"]:
        mapping = {
            "۱ ماهه: ۹۰ هزار تومان": 90000,
            "۳ ماهه: ۲۵۰ هزار تومان": 250000,
            "۶ ماهه: ۴۵۰ هزار تومان": 450000
        }
        amount = mapping[text]
        payment_id = await add_payment(user_id, amount, "buy_subscription", description=text)
        await add_subscription(user_id, payment_id, text)
        await update.message.reply_text(
            f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n💎 {TRON_ADDRESS}\nیا\n🏦 {BANK_CARD}",
            reply_markup=get_back_keyboard()
        )
        user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
        return

    if text == "📞 پشتیبانی":
        await update.message.reply_text(
            f"برای ارتباط با پشتیبانی، به آی‌دی زیر پیام دهید:\n@SupportBot\nیا با ادمین تماس بگیرید."
        )
        return

    if text == "💵 اعتبار رایگان":
        # اضافه کردن اعتبار رایگان (مثلا ۲۰ هزار تومان)
        await add_balance(user_id, 20000)
        await update.message.reply_text("🎉 ۲۰,۰۰۰ تومان اعتبار رایگان به موجودی شما اضافه شد.", reply_markup=get_main_keyboard())
        return

    if text == "📂 اشتراک‌های من":
        subs = await get_user_subscriptions(user_id)
        if not subs:
            await update.message.reply_text("شما هیچ اشتراکی ندارید.", reply_markup=get_main_keyboard())
        else:
            msg = "اشتراک‌های شما:\n"
            for sid, plan, config, status, payment_id in subs:
                msg += f"- {plan} ({status})\n"
            await update.message.reply_text(msg, reply_markup=get_main_keyboard())
        return

    # Default fallback
    await update.message.reply_text("لطفا یکی از گزینه‌ها را انتخاب کنید.", reply_markup=get_main_keyboard())

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if not str(user_id) == str(ADMIN_ID):
        await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
        return

    if data.startswith("approve_"):
        payment_id = int(data.split("_")[1])
        payment = await db_execute("SELECT user_id, amount, type FROM payments WHERE id = %s", (payment_id,), fetchone=True)
        if payment:
            payer_id, amount, ptype = payment
            await update_payment_status(payment_id, "approved")
            if ptype == "increase_balance":
                await add_balance(payer_id, amount)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("پرداخت تایید شد.")

            await application.bot.send_message(
                chat_id=payer_id,
                text="✅ فیش پرداخت شما تایید شد و موجودی شما به‌روزرسانی گردید."
            )
        else:
            await query.answer("پرداخت پیدا نشد.", show_alert=True)

    elif data.startswith("reject_"):
        payment_id = int(data.split("_")[1])
        payment = await db_execute("SELECT user_id FROM payments WHERE id = %s", (payment_id,), fetchone=True)
        if payment:
            payer_id = payment[0]
            await update_payment_status(payment_id, "rejected")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("پرداخت رد شد.")

            await application.bot.send_message(
                chat_id=payer_id,
                text="❌ فیش پرداخت شما رد شد. لطفا دوباره ارسال کنید یا پشتیبانی تماس بگیرید."
            )
        else:
            await query.answer("پرداخت پیدا نشد.", show_alert=True)

@app.on_event("startup")
async def on_startup():
    init_db_pool()
    await create_tables()
    await set_bot_commands()
    await application.bot.set_webhook(WEBHOOK_URL)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, message_handler))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    await application.start()
    await application.updater.start_polling()  # فقط در صورت نیاز به polling، در وب‌هوک باید حذف شود

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    close_db_pool()

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}
