import logging
import sqlite3
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
)

# ===== اطلاعات ربات =====
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

# ===== دیتابیس =====
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

# ===== کیبوردها =====
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💰 موجودی"), KeyboardButton("💳 خرید اشتراک")],
        [KeyboardButton("🎁 اشتراک تست رایگان"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("💵 اعتبار رایگان"), KeyboardButton("📂 اشتراک‌های من")],
    ], resize_keyboard=True)

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

# ===== توابع دیتابیس =====
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

# ===== وضعیت کاربر =====
user_states = {}
pending_configs = {}  # {admin_id: (buyer_id, payment_id)}

# ===== دستورات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_user_member(user.id):
        kb = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text("❌ ابتدا در کانال عضو شوید.", reply_markup=InlineKeyboardMarkup(kb))
        return
    ensure_user(user.id, user.username or "", context.user_data.get("invited_by"))

    phone = get_user_phone(user.id)
    if phone:
        await update.message.reply_text(f"🌐 خوش آمدید!\nشماره شما: {phone}", reply_markup=get_main_keyboard())
        return

    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text("✅ لطفا شماره تماس خود را ارسال کنید.", reply_markup=contact_keyboard)
    user_states[user.id] = "awaiting_contact"

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if user_states.get(uid) != "awaiting_contact":
        return
    contact = update.message.contact
    if not contact or contact.user_id != uid:
        await update.message.reply_text("⚠️ لطفا از دکمه ارسال کنید.")
        return
    save_user_phone(uid, contact.phone_number)
    await context.bot.send_message(ADMIN_ID, f"📞 کاربر {uid} شماره خود را ارسال کرد: {contact.phone_number}")
    await update.message.reply_text("🌐 خوش آمدید!", reply_markup=get_main_keyboard())
    user_states.pop(uid, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text or ""

    # ===== ارسال کانفیگ از طرف ادمین =====
    if uid == ADMIN_ID and uid in pending_configs:
        buyer_id, payment_id = pending_configs.pop(uid)
        await context.bot.send_message(buyer_id, f"📦 کانفیگ شما آماده است:\n\n{text}")
        await update.message.reply_text("✅ کانفیگ برای خریدار ارسال شد.")
        return

    # ===== ارسال فیش =====
    if update.message.photo or update.message.document:
        state = user_states.get(uid)
        if state and (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_")):
            payment_id = int(state.split("_")[-1])
            payment = cursor.execute("SELECT amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
            if payment:
                amount, ptype = payment
                caption = f"💳 فیش از کاربر {uid}:\nمبلغ: {amount}\nنوع: {ptype}"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تایید", callback_data=f"approve_{payment_id}"),
                     InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")]
                ])
                file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
                if update.message.photo:
                    await context.bot.send_photo(ADMIN_ID, file_id, caption=caption, reply_markup=keyboard)
                else:
                    await context.bot.send_document(ADMIN_ID, file_id, caption=caption, reply_markup=keyboard)
                await update.message.reply_text("✅ فیش برای ادمین ارسال شد.", reply_markup=get_main_keyboard())
                user_states.pop(uid, None)
                return

    # ===== منوها =====
    if text == "بازگشت به منو":
        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard()); return
    if text == "💰 موجودی":
        await update.message.reply_text("💰 بخش موجودی:", reply_markup=get_balance_keyboard()); return
    if text == "نمایش موجودی":
        await update.message.reply_text(f"💰 موجودی شما: {get_balance(uid)} تومان", reply_markup=get_balance_keyboard()); return
    if text == "افزایش موجودی":
        await update.message.reply_text("💳 مبلغ را وارد کنید:", reply_markup=get_back_keyboard())
        user_states[uid] = "awaiting_deposit_amount"; return
    if user_states.get(uid) == "awaiting_deposit_amount":
        if text.isdigit():
            amount = int(text)
            pid = add_payment(uid, amount, "increase_balance")
            await update.message.reply_text(f"واریز کنید:\n💎 {TRON_ADDRESS}\n🏦 {BANK_CARD}", reply_markup=get_back_keyboard())
            user_states[uid] = f"awaiting_deposit_receipt_{pid}"
        else:
            await update.message.reply_text("⚠️ لطفا عدد وارد کنید."); return
    if text == "💳 خرید اشتراک":
        await update.message.reply_text("پلن را انتخاب کنید:", reply_markup=get_subscription_keyboard()); return
    if text in ["۱ ماهه: ۹۰ هزار تومان", "۳ ماهه: ۲۵۰ هزار تومان", "۶ ماهه: ۴۵۰ هزار تومان"]:
        prices = {"۱ ماهه: ۹۰ هزار تومان":90000,"۳ ماهه: ۲۵۰ هزار تومان":250000,"۶ ماهه: ۴۵۰ هزار تومان":450000}
        amount = prices[text]
        pid = add_payment(uid, amount, "buy_subscription", text)
        await update.message.reply_text(f"واریز کنید:\n💎 {TRON_ADDRESS}\n🏦 {BANK_CARD}", reply_markup=get_back_keyboard())
        user_states[uid] = f"awaiting_subscription_receipt_{pid}"; return

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith(("approve_", "reject_")):
        if update.effective_user.id != ADMIN_ID:
            await query.message.reply_text("⚠️ شما ادمین نیستید."); return

        pid = int(data.split("_")[1])
        payment = cursor.execute("SELECT user_id, amount, type FROM payments WHERE id=?", (pid,)).fetchone()
        if not payment: await query.message.reply_text("⚠️ پیدا نشد."); return
        user_id, amount, ptype = payment

        if data.startswith("approve_"):
            update_payment_status(pid, "approved")
            if ptype == "increase_balance":
                add_balance(user_id, amount)
                await context.bot.send_message(user_id, f"💰 موجودی {amount} اضافه شد.")
            elif ptype == "buy_subscription":
                await context.bot.send_message(user_id, "✅ پرداخت تایید شد. منتظر دریافت کانفیگ باشید.")
                # دکمه ارسال کانفیگ فقط برای ادمین
                send_cfg_btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🟣 ارسال کانفیگ", callback_data=f"sendcfg_{pid}")]
                ])
                await query.message.reply_text("✅ پرداخت تایید شد.", reply_markup=send_cfg_btn)

        else:
            update_payment_status(pid, "rejected")
            await context.bot.send_message(user_id, "❌ پرداخت شما رد شد.")

async def send_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("فقط ادمین", show_alert=True); return
    pid = int(query.data.split("_")[1])
    payment = cursor.execute("SELECT user_id FROM payments WHERE id=?", (pid,)).fetchone()
    if not payment: await query.message.reply_text("پرداخت پیدا نشد."); return
    buyer_id = payment[0]
    pending_configs[ADMIN_ID] = (buyer_id, pid)
    await query.message.reply_text("📄 لطفا کانفیگ را ارسال کنید.")

# ===== هندلرها =====
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), message_handler))
application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(approve_|reject_)"))
application.add_handler(CallbackQueryHandler(send_config_callback, pattern="^sendcfg_"))

# ===== وبهوک =====
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(WEBHOOK_URL)
    await application.initialize()
    await application.start()

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
