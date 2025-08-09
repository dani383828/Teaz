import logging
import sqlite3
import traceback
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler
)

# ======== تنظیمات ربات ========
TOKEN = "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340  # حتما عدد صحیح و ادمین ربات را استارت زده باشد
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "0000 - 0000 - 0000 - 0000"

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://teaz.onrender.com{WEBHOOK_PATH}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ======== ایجاد برنامه ========
app = FastAPI()
application = Application.builder().token(TOKEN).build()

# ======== اتصال به دیتابیس SQLite ========
conn = sqlite3.connect("vpnbot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    invited_by INTEGER,
    phone TEXT DEFAULT NULL
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT,  -- pending, approved, rejected
    type TEXT,    -- increase_balance, buy_subscription
    description TEXT
)
""")
conn.commit()

# ======== کیبوردها ========
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

# ======== توابع دیتابیس ========
def ensure_user(user_id, username, invited_by=None):
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users(user_id, username, invited_by) VALUES (?, ?, ?)", (user_id, username, invited_by))
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

def get_pending_payments():
    cursor.execute("SELECT id, user_id, amount, type, description FROM payments WHERE status='pending'")
    return cursor.fetchall()

# ======== بررسی عضویت کاربر در کانال ========
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Error checking membership: {e}")
        return False

# ======== وضعیت کاربر ========
user_states = {}

# ======== هندلرها ========
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
    ensure_user(user_id, username, invited_by)

    phone = get_user_phone(user_id)
    if phone:
        await update.message.reply_text(
            f"🌐 به فروشگاه VPN ما خوش آمدید!\nشماره تماس شما: {phone}\nیک گزینه را انتخاب کنید:",
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
        await update.message.reply_text("⚠️ لطفا شماره تماس خود را از طریق دکمه ارسال کنید.")
        return

    phone_number = contact.phone_number
    save_user_phone(user_id, phone_number)

    # ارسال اطلاع به ادمین
    await application.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📞 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) شماره تماس خود را ارسال کرد:\n{phone_number}"
    )

    await update.message.reply_text(
        "🌐 به فروشگاه VPN ما خوش آمدید!\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    logging.info(f"Received message from {user_id}: {text}")
    logging.info(f"user_states[{user_id}] = {user_states.get(user_id)}")

    if text == "بازگشت به منو":
        await update.message.reply_text("🌐 منوی اصلی:", reply_markup=get_main_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "💰 موجودی":
        await update.message.reply_text("💰 بخش موجودی:\nیک گزینه را انتخاب کنید:", reply_markup=get_balance_keyboard())
        return

    if text == "نمایش موجودی":
        bal = get_balance(user_id)
        await update.message.reply_text(f"💰 موجودی شما: {bal} تومان", reply_markup=get_balance_keyboard())
        return

    if text == "افزایش موجودی":
        await update.message.reply_text(
            "💳 لطفا مبلغ واریزی خود را به تومان به صورت عدد وارد کنید (مثال: 50000):",
            reply_markup=get_back_keyboard()
        )
        user_states[user_id] = "awaiting_deposit_amount"
        return

    if user_states.get(user_id) == "awaiting_deposit_amount":
        if text.isdigit():
            amount = int(text)
            payment_id = add_payment(user_id, amount, "increase_balance")
            await update.message.reply_text(
                f"لطفا دقیقاً مبلغ {amount} تومان را به یکی از آدرس‌های زیر واریز کنید و سپس عکس فیش را ارسال کنید:\n\n"
                f"💎 آدرس ترون: `{TRON_ADDRESS}`\n"
                f"🏦 شماره کارت: `{BANK_CARD}`\n\n"
                "بعد از ارسال فیش، ادمین آن را بررسی و تایید یا رد خواهد کرد.",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard()
            )
            user_states[user_id] = f"awaiting_deposit_receipt_{payment_id}"
        else:
            await update.message.reply_text("⚠️ لطفا فقط عدد وارد کنید یا بازگشت به منو بزنید.")
        return

    if text == "💳 خرید اشتراک":
        await update.message.reply_text("💳 لطفا پلن اشتراک خود را انتخاب کنید:", reply_markup=get_subscription_keyboard())
        return

    if text in ["۱ ماهه: ۹۰ هزار تومان", "۳ ماهه: ۲۵۰ هزار تومان", "۶ ماهه: ۴۵۰ هزار تومان"]:
        mapping = {
            "۱ ماهه: ۹۰ هزار تومان": 90000,
            "۳ ماهه: ۲۵۰ هزار تومان": 250000,
            "۶ ماهه: ۴۵۰ هزار تومان": 450000
        }
        amount = mapping[text]
        payment_id = add_payment(user_id, amount, "buy_subscription", description=text)
        await update.message.reply_text(
            f"لطفا مبلغ {amount} تومان را به یکی از آدرس‌های زیر واریز کنید و عکس فیش را ارسال کنید:\n\n"
            f"💎 آدرس ترون: `{TRON_ADDRESS}`\n"
            f"🏦 شماره کارت: `{BANK_CARD}`\n\n"
            "پس از ارسال فیش، ادمین آن را بررسی و تایید یا رد خواهد کرد.\n"
            "در صورت تایید، حداکثر تا ۱ ساعت آینده کانفیگ اشتراک شما ارسال می‌شود.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        user_states[user_id] = f"awaiting_subscription_receipt_{payment_id}"
        return

    if text == "🎁 اشتراک تست رایگان":
        await update.message.reply_text("🎁 اشتراک تست رایگان بزودی فعال می‌شود.", reply_markup=get_main_keyboard())
        return

    if text == "📞 پشتیبانی":
        await update.message.reply_text("📞 پشتیبانی: https://t.me/teazadmin", reply_markup=get_main_keyboard())
        return

    if text == "💵 اعتبار رایگان":
        invite_link = f"https://t.me/teazvpn_bot?start={user_id}"
        await update.message.reply_text(
            f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\n"
            "برای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد.",
            reply_markup=get_main_keyboard()
        )
        return

    if text == "📂 اشتراک‌های من":
        await update.message.reply_text("📂 شما هنوز اشتراکی ندارید.", reply_markup=get_main_keyboard())
        return

    # ارسال فیش پرداخت به ادمین (عکس یا فایل)
    if update.message.photo or update.message.document:
        state = user_states.get(user_id)
        if state and (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_")):
            payment_id = int(state.split("_")[-1])
            payment = cursor.execute("SELECT amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
            amount, ptype = payment if payment else (0, "")
            caption = f"💳 فیش پرداختی از کاربر {user_id}:\nمبلغ: {amount}\nنوع: {'افزایش موجودی' if ptype == 'increase_balance' else 'خرید اشتراک'}"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تایید", callback_data=f"approve_{payment_id}"),
                    InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
                ]
            ])

            try:
                # پیام تست به ادمین
                await application.bot.send_message(chat_id=ADMIN_ID, text="🔔 یک فیش جدید ارسال شده، در حال دریافت...")

                if update.message.photo:
                    file_id = update.message.photo[-1].file_id
                    await application.bot.send_photo(
                        chat_id=ADMIN_ID,
                        photo=file_id,
                        caption=caption,
                        reply_markup=keyboard
                    )
                elif update.message.document:
                    doc_id = update.message.document.file_id
                    await application.bot.send_document(
                        chat_id=ADMIN_ID,
                        document=doc_id,
                        caption=caption,
                        reply_markup=keyboard
                    )
                await update.message.reply_text(
                    "✅ فیش شما با موفقیت برای ادمین ارسال شد، لطفا منتظر تایید باشید.",
                    reply_markup=get_main_keyboard()
                )
                user_states.pop(user_id, None)
            except Exception as e:
                logging.error(f"Error sending payment receipt to admin: {e}")
                logging.error(traceback.format_exc())
                await update.message.reply_text(
                    "⚠️ مشکلی در ارسال فیش به ادمین پیش آمد. لطفا بعدا تلاش کنید.",
                    reply_markup=get_main_keyboard()
                )
            return

    # دستور نامعتبر
    await update.message.reply_text("⚠️ دستور نامعتبر است. لطفا از دکمه‌ها استفاده کنید.", reply_markup=get_main_keyboard())

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("approve_") or data.startswith("reject_"):
        if update.effective_user.id != ADMIN_ID:
            await query.message.reply_text("⚠️ شما اجازه این کار را ندارید.")
            return

        payment_id = int(data.split("_")[1])
        payment = cursor.execute("SELECT user_id, amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment:
            await query.message.reply_text("⚠️ پرداخت یافت نشد.")
            return
        user_id, amount, ptype = payment

        if data.startswith("approve_"):
            update_payment_status(payment_id, "approved")
            if ptype == "increase_balance":
                add_balance(user_id, amount)
                await application.bot.send_message(user_id, f"💰 پرداخت شما تایید شد و موجودی شما {amount} تومان افزایش یافت.")
            elif ptype == "buy_subscription":
                await application.bot.send_message(user_id,
                    "✅ پرداخت شما تایید شد.\n"
                    "کانفیگ اشتراک شما حداکثر تا ۱ ساعت آینده ارسال خواهد شد."
                )
            await query.message.edit_reply_markup(None)
            await query.message.reply_text("✅ پرداخت تایید شد.")

        elif data.startswith("reject_"):
            update_payment_status(payment_id, "rejected")
            await application.bot.send_message(user_id, "❌ پرداخت شما رد شد. لطفا با پشتیبانی تماس بگیرید.")
            await query.message.edit_reply_markup(None)
            await query.message.reply_text("❌ پرداخت رد شد.")

async def start_with_param(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    args = context.args

    if args and len(args) > 0:
        invited_by = None
        try:
            invited_by = int(args[0])
        except:
            pass
        context.user_data["invited_by"] = invited_by

    await start(update, context)

# ======== اضافه کردن هندلرها ========
application.add_handler(CommandHandler("start", start_with_param))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
application.add_handler(CallbackQueryHandler(admin_callback_handler))

# ======== وبهوک ========
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

# ======== استارت و شات‌داون ========
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
