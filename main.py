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


# 📌 /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 خرید VPN", callback_data="buy_vpn")],
        [InlineKeyboardButton("📢 کانال ما", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]
    ]
    await update.message.reply_text(
        "🌐 خوش اومدی به فروشگاه VPN ما\n\n"
        "برای خرید روی دکمه زیر بزن 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# 📌 هندلر دکمه‌ها
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_vpn":
        await query.message.reply_text(
            f"💳 لطفاً مبلغ رو به آدرس ترون زیر ارسال کنید:\n\n"
            f"`{TRON_ADDRESS}`\n\n"
            "سپس رسید رو برای ادمین ارسال کنید.",
            parse_mode="Markdown"
        )
        await query.message.reply_text(f"📞 ارتباط با ادمین: [ادمین](tg://user?id={ADMIN_ID})", parse_mode="Markdown")


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
