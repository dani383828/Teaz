import os
import logging
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import requests

# توکن و تنظیمات
TOKEN = "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://sea-2ri6.onrender.com{WEBHOOK_PATH}"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"

# ⚙️ لاگ‌گیری
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# 📦 FastAPI app
app = FastAPI()

# 🎯 ساخت ربات تلگرام
application = Application.builder().token(TOKEN).build()

# 🛡️ بررسی عضویت در کانال
async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Error checking membership: {e}")
        return False

# 📌 هندلر برای /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # بررسی عضویت در کانال
    if not await check_channel_membership(user_id, context):
        keyboard = [[InlineKeyboardButton("عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🏴‍☠️ لطفاً برای استفاده از بات، ابتدا در کانال {CHANNEL_USERNAME} عضو شوید!",
            reply_markup=reply_markup
        )
        return

    # منوی اصلی
    keyboard = [
        [InlineKeyboardButton("🛒 خرید VPN", callback_data="buy_vpn")],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data="support")],
        [InlineKeyboardButton("💳 کیف پول ترون", callback_data="tron_wallet")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏴‍☠️ خوش اومدی به دنیای دزدان دریایی!\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=reply_markup
    )

# 🔄 هندلر برای دکمه‌ها
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_vpn":
        await query.message.reply_text("🛒 برای خرید VPN، لطفاً مقدار ارز ترون (TRX) رو به آدرس زیر بفرستید:\n"
                                      f"`{TRON_ADDRESS}`\n"
                                      "سپس کد تراکنش (TXID) رو برای پشتیبانی ارسال کنید.")
    elif query.data == "support":
        await query.message.reply_text(f"📞 برای پشتیبانی با ادمین تماس بگیرید: {ADMIN_ID}")
    elif query.data == "tron_wallet":
        await query.message.reply_text(f"💳 آدرس کیف پول ترون:\n`{TRON_ADDRESS}`")

# 🔗 ثبت هندلرها
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_callback))

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
