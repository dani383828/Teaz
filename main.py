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


# 📌 بررسی عضویت کاربر
async def is_user_member(user_id):
    try:
        member = await application.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False


# 📌 /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_member(user_id):
        join_btn = [[InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]]
        await update.message.reply_text(
            "❌ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس مجدد /start را بزنید.",
            reply_markup=InlineKeyboardMarkup(join_btn)
        )
        return

    keyboard = [
        [InlineKeyboardButton("💰 موجودی", callback_data="balance")],
        [InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_vpn")],
        [InlineKeyboardButton("🎁 اشتراک تست رایگان", callback_data="free_test")],
        [InlineKeyboardButton("📞 پشتیبانی", url="https://t.me/teazadmin")],
        [InlineKeyboardButton("💵 اعتبار رایگان", callback_data="free_credit")],
        [InlineKeyboardButton("📂 اشتراک‌های من", callback_data="my_subs")]
    ]
    await update.message.reply_text(
        "🌐 به فروشگاه VPN ما خوش آمدید!\nیک گزینه را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# 📌 هندلر دکمه‌ها
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_vpn":
        await query.message.reply_text(
            f"💳 لطفاً مبلغ را به آدرس ترون زیر ارسال کنید:\n\n"
            f"`{TRON_ADDRESS}`\n\n"
            "سپس رسید را برای ادمین ارسال کنید.",
            parse_mode="Markdown"
        )
        await query.message.reply_text(f"📞 ارتباط با ادمین: [ادمین](tg://user?id={ADMIN_ID})", parse_mode="Markdown")

    elif query.data == "balance":
        await query.message.reply_text("💰 موجودی شما: 0 تومان")

    elif query.data == "free_test":
        await query.message.reply_text("🎁 اشتراک تست رایگان بزودی فعال می‌شود.")

    elif query.data == "free_credit":
        await query.message.reply_text("💵 بزودی می‌توانید اعتبار رایگان دریافت کنید.")

    elif query.data == "my_subs":
        await query.message.reply_text("📂 شما هیچ اشتراکی ندارید.")


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
