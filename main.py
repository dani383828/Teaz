import logging
import sqlite3
from fastapi import FastAPI, Request
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
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
cursor.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    payment_id INTEGER,
    plan TEXT,
    config TEXT,
    status TEXT DEFAULT 'active'
)""")
conn.commit()

# کلیدهای کیبورد
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

# بررسی عضویت
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# ذخیره یا اطمینان از وجود کاربر
def ensure_user(user_id, username, invited_by=None):
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users(user_id, username, invited_by) VALUES (?, ?, ?)",
                       (user_id, username, invited_by))
        conn.commit()
        # اضافه کردن پاداش به دعوت‌کننده
        if invited_by and invited_by != user_id:
            cursor.execute("SELECT user_id FROM users WHERE user_id=?", (invited_by,))
            if cursor.fetchone():
                add_balance(invited_by, 25000)  # پاداش 25,000 تومان به دعوت‌کننده
                conn.commit()

# ذخیره شماره تماس کاربر
def save_user_phone(user_id, phone):
    cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    conn.commit()

# دریافت شماره تماس کاربر
def get_user_phone(user_id):
    cursor.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

# افزایش موجودی
def add_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

# دریافت موجودی
def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

# ثبت پرداخت جدید
def add_payment(user_id, amount, ptype, description=""):
    cursor.execute(
        "INSERT INTO payments(user_id, amount, status, type, description) VALUES (?, ?, 'pending', ?, ?)",
        (user_id, amount, ptype, description)
    )
    conn.commit()
    return cursor.lastrowid

# ثبت اشتراک جدید
def add_subscription(user_id, payment_id, plan):
    cursor.execute(
        "INSERT INTO subscriptions(user_id, payment_id, plan, status) VALUES (?, ?, ?, 'active')",
        (user_id, payment_id, plan)
    )
    conn.commit()

# آپدیت کانفیگ اشتراک
def update_subscription_config(payment_id, config):
    cursor.execute("UPDATE subscriptions SET config=? WHERE payment_id=?", (config, payment_id))
    conn.commit()

# آپدیت وضعیت پرداخت
def update_payment_status(payment_id, status):
    cursor.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
    conn.commit()

# دریافت اشتراک‌های کاربر
def get_user_subscriptions(user_id):
    cursor.execute("SELECT id, plan, config, status, payment_id FROM subscriptions WHERE user_id=?", (user_id,))
    return cursor.fetchall()

# نگهداری وضعیت کاربر
user_states = {}

# تنظیم منوی دستورات
async def set_bot_commands():
    commands = [BotCommand(command="/start", description="شروع ربات")]
    await application.bot.set_my_commands(commands)

# /start
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
        [[KeyboardButton("ارسال شماره تماس", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "✅ لطفا شماره تماس خود را ارسال کنید.",
        reply_markup=contact_keyboard
    )
    user_states[user_id] = "awaiting_contact"

# دریافت شماره تماس
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

    # ارسال پیام به ادمین
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📞 کاربر {user_id} (@{update.effective_user.username or 'NoUsername'}) شماره تماس خود را ارسال کرد:\n{phone_number}"
    )

    # بررسی دعوت‌کننده و ارسال پیام پاداش
    cursor.execute("SELECT invited_by FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    invited_by = result[0] if result and result[0] else None
    if invited_by and invited_by != user_id:
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (invited_by,))
        if cursor.fetchone():
            await context.bot.send_message(
                chat_id=invited_by,
                text=f"🎉 دوست شما (@{update.effective_user.username or 'NoUsername'}) با موفقیت مراحل ثبت‌نام را تکمیل کرد!\n💰 ۲۵,۰۰۰ تومان به موجودی شما اضافه شد."
            )

    await update.message.reply_text(
        "🌐 به فروشگاه VPN ما خوش آمدید!\nیک گزینه را انتخاب کنید:",
        reply_markup=get_main_keyboard()
    )
    user_states.pop(user_id, None)

# هندل پیام‌ها
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""

    # ====== بررسی فیش پرداخت یا کانفیگ ارسالی توسط ادمین ======
    if update.message.photo or update.message.document or update.message.text:
        state = user_states.get(user_id)
        if state and (state.startswith("awaiting_deposit_receipt_") or state.startswith("awaiting_subscription_receipt_")):
            try:
                payment_id = int(state.split("_")[-1])
            except:
                payment_id = None

            if payment_id:
                payment = cursor.execute("SELECT amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
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
        # مدیریت ارسال کانفیگ توسط ادمین
        elif state and state.startswith("awaiting_config_"):
            try:
                payment_id = int(state.split("_")[-1])
            except:
                payment_id = None

            if payment_id:
                payment = cursor.execute("SELECT user_id, description FROM payments WHERE id=?", (payment_id,)).fetchone()
                if payment:
                    buyer_id, description = payment
                    if update.message.text:
                        config = update.message.text
                        update_subscription_config(payment_id, config)
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

    # بقیه بخش‌ها
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
        await update.message.reply_text("💳 لطفا مبلغ واریزی را وارد کنید:", reply_markup=get_back_keyboard())
        user_states[user_id] = "awaiting_deposit_amount"
        return

    if user_states.get(user_id) == "awaiting_deposit_amount":
        if text.isdigit():
            amount = int(text)
            payment_id = add_payment(user_id, amount, "increase_balance")
            await update.message.reply_text(
                f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n💎 {TRON_ADDRESS}\n🏦 {BANK_CARD}",
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
        payment_id = add_payment(user_id, amount, "buy_subscription", description=text)
        add_subscription(user_id, payment_id, text)
        await update.message.reply_text(
            f"لطفا {amount} تومان واریز کنید و فیش را ارسال کنید:\n💎 {TRON_ADDRESS}\n🏦 {BANK_CARD}",
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
        with open("invite_image.jpg", "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=(
                    f"💵 لینک اختصاصی شما برای دعوت دوستان:\n{invite_link}\n\n"
                    "برای هر دعوت موفق، ۲۵,۰۰۰ تومان به موجودی شما اضافه خواهد شد."
                ),
                reply_markup=get_main_keyboard()
            )
        return

    if text == "📂 اشتراک‌های من":
        subscriptions = get_user_subscriptions(user_id)
        if not subscriptions:
            await update.message.reply_text("📂 شما هنوز اشتراکی ندارید.", reply_markup=get_main_keyboard())
            return
        response = "📂 اشتراک‌های شما:\n\n"
        for sub in subscriptions:
            sub_id, plan, config, status, payment_id = sub
            response += f"🔹 اشتراک: {plan}\nکد خرید: #{payment_id}\nوضعیت: {'فعال' if status == 'active' else 'غیرفعال'}\n"
            if config:
                response += f"کانفیگ:\n```\n{config}\n```\n"
            response += "--------------------\n"
        await update.message.reply_text(response, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return

    await update.message.reply_text("⚠️ دستور نامعتبر است. لطفا از دکمه‌ها استفاده کنید.", reply_markup=get_main_keyboard())

# تایید/رد توسط ادمین
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
            payment = cursor.execute("SELECT user_id, amount, type, description FROM payments WHERE id=?", (payment_id,)).fetchone()
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            user_id, amount, ptype, description = payment

            update_payment_status(payment_id, "approved")
            if ptype == "increase_balance":
                add_balance(user_id, amount)
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

        elif data.startswith("reject_"):
            payment_id = int(data.split("_")[1])
            payment = cursor.execute("SELECT user_id, amount, type FROM payments WHERE id=?", (payment_id,)).fetchone()
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            user_id, amount, ptype = payment

            update_payment_status(payment_id, "rejected")
            await context.bot.send_message(user_id, "❌ پرداخت شما رد شد. با پشتیبانی تماس بگیرید.")
            await query.message.edit_reply_markup(None)
            await query.message.reply_text("❌ پرداخت رد شد.")

        elif data.startswith("send_config_"):
            payment_id = int(data.split("_")[-1])
            payment = cursor.execute("SELECT user_id, description FROM payments WHERE id=?", (payment_id,)).fetchone()
            if not payment:
                await query.message.reply_text("⚠️ پرداخت یافت نشد.")
                return
            await query.message.reply_text("لطفا کانفیگ را ارسال کنید.")
            user_states[ADMIN_ID] = f"awaiting_config_{payment_id}"

# استارت با پارامتر
async def start_with_param(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and len(args) > 0:
        try:
            invited_by = int(args[0])
            if invited_by != update.effective_user.id:  # اطمینان از اینکه کاربر خودش نیست
                context.user_data["invited_by"] = invited_by
        except:
            context.user_data["invited_by"] = None
    await start(update, context)

# ثبت هندلرها
application.add_handler(CommandHandler("start", start_with_param))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), message_handler))
application.add_handler(CallbackQueryHandler(admin_callback_handler))

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await set_bot_commands()  # تنظیم منوی دستورات
    print("✅ Webhook set:", WEBHOOK_URL)
    await application.initialize()
    await application.start()

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
