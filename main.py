import os
import logging
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)

# 📌 اطلاعات ربات
TOKEN = "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://teaz.onrender.com{WEBHOOK_PATH}"

# ⚙️ لاگ‌گیری
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# 📦 FastAPI app
app = FastAPI()

# 🎯 ساخت اپلیکیشن ربات
application = Application.builder().token(TOKEN).build()

# 📌 بررسی عضویت در کانال
async def check_channel_membership(user_id: int, bot: Application.bot) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# 📌 /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_channel_membership(user_id, context.bot):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text(
            "🌐 لطفاً برای استفاده از ربات ابتدا در کانال ما عضو شوید:\n\n"
            f"{CHANNEL_USERNAME}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    keyboard = [
        [InlineKeyboardButton("💰 موجودی", callback_data="balance")],
        [InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_vpn")],
        [InlineKeyboardButton("🧪 اشتراک تست رایگان (بزودی فعال می‌شود)", callback_data="free_test")],
        [InlineKeyboardButton("📞 پشتیبانی", url="https://t.me/teazadmin")],
        [InlineKeyboardButton("🎁 اعتبار رایگان", callback_data="free_credit")],
        [InlineKeyboardButton("📋 اشتراک‌های من", callback_data="my_subscriptions")]
    ]
    await update.message.reply_text(
        "🌐 خوش اومدی به فروشگاه VPN ما\n\n"
        "لطفاً یکی از گزینه‌های زیر رو انتخاب کن 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# 📌 هندلر دکمه‌ها
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not await check_channel_membership(user_id, context.bot):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await query.message.reply_text(
            "🌐 لطفاً برای استفاده از ربات ابتدا در کانال ما عضو شوید:\n\n"
            f"{CHANNEL_USERNAME}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == "buy_vpn":
        await query.message.reply_text(
            f"💳 لطفاً مبلغ رو به آدرس ترون زیر ارسال کنید:\n\n"
            f"`{TRON_ADDRESS}`\n\n"
            "سپس رسید رو برای ادمین ارسال کنید.",
            parse_mode="Markdown"
        )
        await query.message.reply_text(f"📞 ارتباط با ادمین: [ادمین](tg://user?id={ADMIN_ID})", parse_mode="Markdown")
    elif query.data == "balance":
        await query.message.reply_text("💰 موجودی شما: در حال حاضر این قابلیت فعال نیست.")
    elif query.data == "free_test":
        await query.message.reply_text("🧪 اشتراک تست رایگان: بزودی فعال می‌شود!")
    elif query.data == "free_credit":
        await query.message.reply_text("🎁 اعتبار رایگان: در حال حاضر این قابلیت فعال نیست.")
    elif query.data == "my_subscriptions":
        await query.message.reply_text("📋 اشتراک‌های من: در حال حاضر هیچ اشتراکی ثبت نشده است.")

# 🔗 ثبت هندلرها
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))

# 🔁 وب‌هوک تلگرام
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

# 🔥 زمان بالا آمدن سرور
@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Webhook set:", WEBHOOK_URL)
    await application.initialize()
    await application.start()

# 🛑 هنگام خاموشی
@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
