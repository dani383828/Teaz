import logging
import sqlite3
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
)

# اطلاعات ربات
TOKEN = "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "0000 - 0000 - 0000 - 0000"

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://teaz.onrender.com{WEBHOOK_PATH}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

app = FastAPI()
application = Application.builder().token(TOKEN).build()

# دیتابیس sqlite ساده
conn = sqlite3.connect("vpnbot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    invited_by INTEGER,
    phone TEXT DEFAULT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT,
    type TEXT,
    description TEXT
)""")
conn.commit()

# --- کیبوردها ---
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💰 موجودی"), KeyboardButton("💳 خرید اشتراک")],
        [KeyboardButton("🎁 اشتراک تست رایگان"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("💵 اعتبار رایگان"), KeyboardButton("📂 اشتراک‌های من")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_balance_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("نمایش موجودی"), KeyboardButton("افزایش موجودی")],
        [KeyboardButton("بازگشت به منو")]
    ], resize_keyboard=True)

def get_back_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("بازگشت به منو")]], resize_keyboard=True)

def get_subscription_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("۱ ماهه: ۹۰ هزار تومان")],
        [KeyboardButton("۳ ماهه: ۲۵۰ هزار تومان")],
        [KeyboardButton("۶ ماهه: ۴۵۰ هزار تومان")],
        [KeyboardButton("بازگشت به منو")]
    ], resize_keyboard=True)

# --- توابع دیتابیس ---
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def ensure_user(user_id, username, invited_by=None):
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users(user_id, username, invited_by) VALUES (?, ?, ?)",
                       (user_id, username, invited_by))
        conn.commit()

def save_user_phone(user_id, phone):
    cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    conn.commit()

def get_user_phone(user_id):
    cursor.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

def add_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

def add_payment(user_id, amount, ptype, description=""):
    cursor.execute(
        "INSERT INTO payments(user_id, amount, status, type, description) VALUES (?, ?, 'pending', ?, ?)",
        (user_id, amount, ptype, description)
    )
    conn.commit()
    return cursor.lastrowid

def update_payment_status(payment_id, status):
    cursor.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
    conn.commit()

user_states = {}

# --- هندلرها ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""

    if not await is_user_member(user_id):
        kb = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text(
            "❌ ابتدا در کانال ما عضو شوید و مجدد /start بزنید.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    invited_by = context.user_data.get("invited_by")
    ensure_user(user_id, username, invited_by)

    phone = get_user_phone(user_id)
    if phone:
        await update.message.reply_text(
            f"🌐 خوش آمدید!\nشماره شما: {phone}\nیک گزینه را انتخاب کنید:",
            reply_markup=get_main_keyboard()
        )
        user_states.pop(user_id, None)
        return

    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
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
        await update.message.reply_text("⚠️ لطفا شماره تماس خود را از دکمه ارسال کنید.")
        return

    phone_number = contact.phone_number
    save_user_phone(user_id, phone_number)

    await application.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📞 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) شماره داد:\n{phone_number}"
    )

    await update.message.reply_text(
        "🌐 خوش آمدید!\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_states.get(user_id)
    if not state or not (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_")):
        return

    payment_id = int(state.split("_")[-1])
    payment = cursor.execute("SELECT amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
    amount, ptype = payment if payment else (0, "")
    caption = f"💳 فیش پرداختی از کاربر {user_id}:\n"
    caption += f"مبلغ: {amount}\nنوع: {'افزایش موجودی' if ptype == 'increase_balance' else 'خرید اشتراک'}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"approve_{payment_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
        ]
    ])

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await application.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, reply_markup=keyboard)
    elif update.message.document:
        doc_id = update.message.document.file_id
        await application.bot.send_document(chat_id=ADMIN_ID, document=doc_id, caption=caption, reply_markup=keyboard)

    await update.message.reply_text("✅ فیش شما برای ادمین ارسال شد.", reply_markup=get_main_keyboard())
    user_states.pop(user_id, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    # بقیه کد message_handler مثل قبل شما اینجا قرار می‌گیرد...

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    # کد تایید/رد پرداخت مثل قبل شما اینجا قرار می‌گیرد...

async def start_with_param(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and len(args) > 0:
        try:
            context.user_data["invited_by"] = int(args[0])
        except:
            pass
    await start(update, context)

# --- ثبت هندلرها ---
application.add_handler(CommandHandler("start", start_with_param))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, receipt_handler))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
application.add_handler(CallbackQueryHandler(admin_callback_handler))

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Webhook set:", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
