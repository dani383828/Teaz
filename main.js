// main.js - Teaz VPN Bot for Cloudflare Workers
// Cloudflare Workers از سینتکس ES Modules استفاده می‌کند

// متغیرهای محیطی
const TELEGRAM_API = `https://api.telegram.org/bot${process.env.BOT_TOKEN}`;
const CHANNEL_USERNAME = process.env.CHANNEL_USERNAME || "@teazvpn";
const ADMIN_ID = parseInt(process.env.ADMIN_ID || "5542927340");
const TRON_ADDRESS = process.env.TRON_ADDRESS || "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb";
const BANK_CARD = process.env.BANK_CARD || "6037 9975 9717 2684";

// وضعیت کاربران (در حافظه - موقت)
const userStates = new Map();
const userData = new Map();

// ========== توابع کمکی ==========
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function generateCouponCode(length = 8) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
}

// ========== توابع ارتباط با تلگرام ==========
async function sendTelegramMessage(chatId, text, replyMarkup = null) {
    const payload = {
        chat_id: chatId,
        text: text,
        parse_mode: 'HTML',
        disable_web_page_preview: true
    };

    if (replyMarkup) {
        payload.reply_markup = replyMarkup;
    }

    try {
        const response = await fetch(`${TELEGRAM_API}/sendMessage`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload)
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error sending message:', error);
        return null;
    }
}

async function answerCallbackQuery(callbackQueryId, text, showAlert = false) {
    try {
        const response = await fetch(`${TELEGRAM_API}/answerCallbackQuery`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                callback_query_id: callbackQueryId,
                text: text,
                show_alert: showAlert
            })
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error answering callback query:', error);
        return null;
    }
}

async function editMessageReplyMarkup(chatId, messageId, replyMarkup = null) {
    try {
        const payload = {
            chat_id: chatId,
            message_id: messageId
        };

        if (replyMarkup) {
            payload.reply_markup = replyMarkup;
        }

        const response = await fetch(`${TELEGRAM_API}/editMessageReplyMarkup`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload)
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error editing message:', error);
        return null;
    }
}

async function sendPhoto(chatId, photoUrl, caption = '', replyMarkup = null) {
    try {
        const payload = {
            chat_id: chatId,
            photo: photoUrl,
            caption: caption,
            parse_mode: 'HTML'
        };

        if (replyMarkup) {
            payload.reply_markup = replyMarkup;
        }

        const response = await fetch(`${TELEGRAM_API}/sendPhoto`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload)
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error sending photo:', error);
        return null;
    }
}

async function getChatMember(chatId, userId) {
    try {
        const response = await fetch(`${TELEGRAM_API}/getChatMember`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                chat_id: chatId,
                user_id: userId
            })
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error getting chat member:', error);
        return null;
    }
}

async function setMyCommands(commands) {
    try {
        const response = await fetch(`${TELEGRAM_API}/setMyCommands`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ commands })
        });
        
        return await response.json();
    } catch (error) {
        console.error('Error setting commands:', error);
        return null;
    }
}

// ========== ساختارهای کیبورد ==========
function getMainKeyboard() {
    return {
        keyboard: [
            [
                { text: "💰 موجودی" },
                { text: "💳 خرید اشتراک" }
            ],
            [
                { text: "🎁 اشتراک تست رایگان" },
                { text: "☎️ پشتیبانی" }
            ],
            [
                { text: "💵 اعتبار رایگان" },
                { text: "📂 اشتراک‌های من" }
            ],
            [
                { text: "💡 راهنمای اتصال" },
                { text: "🧑‍💼 درخواست نمایندگی" }
            ]
        ],
        resize_keyboard: true
    };
}

function getBalanceKeyboard() {
    return {
        keyboard: [
            [
                { text: "نمایش موجودی" },
                { text: "افزایش موجودی" }
            ],
            [
                { text: "بازگشت به منو" }
            ]
        ],
        resize_keyboard: true
    };
}

function getBackKeyboard() {
    return {
        keyboard: [[{ text: "⬅️ بازگشت به منو" }]],
        resize_keyboard: true
    };
}

function getSubscriptionKeyboard(isAgent = false) {
    if (isAgent) {
        return {
            keyboard: [
                [{ text: "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره" }],
                [{ text: "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره" }],
                [{ text: "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره" }],
                [{ text: "⬅️ بازگشت به منو" }]
            ],
            resize_keyboard: true
        };
    } else {
        return {
            keyboard: [
                [{ text: "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره" }],
                [{ text: "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره" }],
                [{ text: "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره" }],
                [{ text: "⬅️ بازگشت به منو" }]
            ],
            resize_keyboard: true
        };
    }
}

function getPaymentMethodKeyboard() {
    return {
        keyboard: [
            [{ text: "🏦 کارت به کارت" }],
            [{ text: "💎 پرداخت با ترون" }],
            [{ text: "💰 پرداخت با موجودی" }],
            [{ text: "⬅️ بازگشت به منو" }]
        ],
        resize_keyboard: true
    };
}

function getConnectionGuideKeyboard() {
    return {
        keyboard: [
            [
                { text: "📗 اندروید" },
                { text: "📕 آیفون/مک" }
            ],
            [
                { text: "📘 ویندوز" },
                { text: "📙 لینوکس" }
            ],
            [
                { text: "⬅️ بازگشت به منو" }
            ]
        ],
        resize_keyboard: true
    };
}

// ========== توابع پردازش پیام ==========
async function handleStart(msg) {
    const chatId = msg.chat.id;
    const userId = msg.from.id;
    const username = msg.from.username || "";
    const args = msg.text ? msg.text.split(' ') : [];

    // چک کردن عضویت در کانال
    try {
        const memberResult = await getChatMember(CHANNEL_USERNAME, userId);
        if (!memberResult || !memberResult.ok) {
            const inlineKeyboard = {
                inline_keyboard: [[
                    {
                        text: "📢 عضویت در کانال",
                        url: `https://t.me/${CHANNEL_USERNAME.replace('@', '')}`
                    }
                ]]
            };
            
            await sendTelegramMessage(
                chatId,
                "❌ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس مجدد /start را بزنید.",
                { reply_markup: inlineKeyboard }
            );
            return;
        }
    } catch (error) {
        console.error('Error checking membership:', error);
    }

    // پردازش دعوت‌کننده
    let invitedBy = null;
    if (args.length > 1) {
        try {
            invitedBy = parseInt(args[1]);
            if (invitedBy === userId) {
                invitedBy = null;
            }
        } catch (error) {
            invitedBy = null;
        }
    }

    // ذخیره کاربر (در این نسخه ساده فقط در حافظه)
    if (!userData.has(userId)) {
        userData.set(userId, {
            userId,
            username,
            balance: 0,
            invitedBy,
            isAgent: false,
            isNewUser: true,
            subscriptions: [],
            payments: []
        });
    } else {
        const user = userData.get(userId);
        user.isNewUser = false;
        userData.set(userId, user);
    }

    // پاک کردن وضعیت
    userStates.delete(userId);

    await sendTelegramMessage(
        chatId,
        "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
        { reply_markup: getMainKeyboard() }
    );
}

async function handleBalance(chatId, userId) {
    userStates.delete(userId);
    await sendTelegramMessage(
        chatId,
        "💰 بخش موجودی:\nیک گزینه را انتخاب کنید:",
        { reply_markup: getBalanceKeyboard() }
    );
}

async function showBalance(chatId, userId) {
    const user = userData.get(userId) || { balance: 0 };
    await sendTelegramMessage(
        chatId,
        `💰 موجودی شما: ${formatNumber(user.balance)} تومان`,
        { reply_markup: getBalanceKeyboard() }
    );
}

async function requestDepositAmount(chatId, userId) {
    userStates.set(userId, "awaiting_deposit_amount");
    await sendTelegramMessage(
        chatId,
        "💳 لطفاً مبلغ واریزی را به تومان وارد کنید (مثال: 90000):",
        { reply_markup: getBackKeyboard() }
    );
}

async function handleDepositAmount(chatId, userId, text) {
    const amount = parseInt(text);
    
    if (isNaN(amount) || amount <= 0) {
        await sendTelegramMessage(
            chatId,
            "⚠️ لطفاً یک مبلغ معتبر وارد کنید.",
            { reply_markup: getBackKeyboard() }
        );
        return;
    }

    // ایجاد پرداخت
    const paymentId = Date.now(); // ID ساده
    userStates.set(userId, `awaiting_deposit_receipt_${paymentId}`);
    
    await sendTelegramMessage(
        chatId,
        `لطفاً ${formatNumber(amount)} تومان واریز کنید و فیش را ارسال کنید:\n\n` +
        `💎 آدرس کیف پول TRON:\n<code>${TRON_ADDRESS}</code>\n\n` +
        `یا\n\n🏦 شماره کارت بانکی:\n<code>${BANK_CARD}</code>\nفرهنگ`,
        { 
            reply_markup: getBackKeyboard() 
        }
    );
}

async function handlePaymentReceipt(chatId, userId, paymentId, text, msg) {
    try {
        const caption = `💳 فیش پرداختی از کاربر ${userId} (${msg.from.username || 'بدون نام'}):\n` +
                       `مبلغ: ${formatNumber(100000)} تومان\n` +
                       `نوع: افزایش موجودی`;

        const inlineKeyboard = {
            inline_keyboard: [[
                { text: "✅ تایید", callback_data: `approve_${paymentId}_${userId}_100000` },
                { text: "❌ رد", callback_data: `reject_${paymentId}_${userId}` }
            ]]
        };

        // ارسال به ادمین
        if (msg.photo && msg.photo.length > 0) {
            const photoId = msg.photo[msg.photo.length - 1].file_id;
            // در این نسخه ساده فقط پیام می‌فرستیم
            await sendTelegramMessage(
                ADMIN_ID,
                caption + "\n\n📸 فیش پیوست شده است.",
                { reply_markup: inlineKeyboard }
            );
        } else {
            await sendTelegramMessage(
                ADMIN_ID,
                caption + "\n\n📄 پیام متنی ارسال شده: " + (text || "بدون متن"),
                { reply_markup: inlineKeyboard }
            );
        }

        await sendTelegramMessage(
            chatId,
            "✅ فیش شما برای ادمین ارسال شد، لطفاً منتظر تایید باشید.",
            { reply_markup: getMainKeyboard() }
        );

        userStates.delete(userId);

    } catch (error) {
        console.error('Error processing payment receipt:', error);
        await sendTelegramMessage(chatId, "⚠️ خطا در پردازش فیش پرداخت.");
    }
}

async function handleSubscriptionPurchase(chatId, userId) {
    const user = userData.get(userId) || { isAgent: false };
    await sendTelegramMessage(
        chatId,
        "💳 پلن را انتخاب کنید:",
        { reply_markup: getSubscriptionKeyboard(user.isAgent) }
    );
}

async function handlePlanSelection(chatId, userId, planText) {
    const planMapping = {
        "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": 90000,
        "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": 250000,
        "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": 450000,
        "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 70000,
        "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 210000,
        "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 380000
    };

    const amount = planMapping[planText];
    
    if (!amount) {
        await sendTelegramMessage(
            chatId,
            "⚠️ پلن انتخاب شده نامعتبر است.",
            { reply_markup: getMainKeyboard() }
        );
        return;
    }

    userStates.set(userId, `awaiting_payment_method_${amount}_${planText}`);
    
    await sendTelegramMessage(
        chatId,
        `💳 روش خرید را برای پلن "${planText}" انتخاب کنید:`,
        { reply_markup: getPaymentMethodKeyboard() }
    );
}

async function showSubscriptions(chatId, userId) {
    const user = userData.get(userId) || { subscriptions: [] };
    
    if (user.subscriptions.length === 0) {
        await sendTelegramMessage(
            chatId,
            "📂 شما هنوز اشتراکی ندارید.",
            { reply_markup: getMainKeyboard() }
        );
        return;
    }

    let message = "📂 لیست کامل اشتراک‌های شما:\n\n";
    const now = new Date();

    for (const sub of user.subscriptions) {
        message += `🔹 اشتراک #${sub.id}\n`;
        message += `📌 پلن: ${sub.plan}\n`;
        message += `📊 وضعیت: ${sub.status === 'active' ? '✅ فعال' : '⏳ در انتظار'}\n`;
        
        if (sub.status === 'active') {
            const endDate = new Date(sub.startDate.getTime() + sub.durationDays * 24 * 60 * 60 * 1000);
            const remainingDays = Math.max(0, Math.floor((endDate - now) / (1000 * 60 * 60 * 24)));
            message += `⏳ زمان باقی‌مانده: ${remainingDays} روز\n`;
            message += `📅 تاریخ شروع: ${sub.startDate.toLocaleString('fa-IR')}\n`;
            message += `📅 تاریخ انقضا: ${endDate.toLocaleString('fa-IR')}\n`;
        }
        
        if (sub.config) {
            message += `🔐 کانفیگ:\n<code>${sub.config}</code>\n`;
        }
        
        message += "------------------------\n\n";
    }

    await sendTelegramMessage(
        chatId,
        message,
        { 
            reply_markup: getMainKeyboard()
        }
    );
}

async function showConnectionGuide(chatId) {
    const guideTexts = {
        "📗 اندروید": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های V2RayNG یا Hiddify(پیشنهادی) است ✅\nبا این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📕 آیفون/مک": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های Singbox(پیشنهادی) یا Streisand یا V2box(پیشنهادی) هست ✅\nبا این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📘 ویندوز": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\nبا این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📙 لینوکس": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\nبا این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀"
    };

    await sendTelegramMessage(
        chatId,
        "💡 راهنمای راه‌اندازی\nدستگاه خود را انتخاب کنید:",
        { reply_markup: getConnectionGuideKeyboard() }
    );
}

async function handleAgencyRequest(chatId, userId) {
    const user = userData.get(userId) || { isAgent: false };
    
    if (user.isAgent) {
        await sendTelegramMessage(
            chatId,
            "💳 پلن را انتخاب کنید:",
            { reply_markup: getSubscriptionKeyboard(true) }
        );
        return;
    }

    const agencyText = `
🚀 اعطای نمایندگی رسمی تیز وی پی ان 🚀

اگر به دنبال یک فرصت درآمدزایی پایدار و بدون محدودیت هستید، حالا بهترین زمان برای پیوستن به تیم ماست!
ما به تعداد محدودی نماینده رسمی می‌پذیریم که بتوانند با فروش سرویس‌های پرسرعت و پایدار تیز وی پی ان، کسب‌وکار خودشان را راه‌اندازی کنند.

💰 شرایط دریافت نمایندگی:
برای شروع همکاری و فعال‌سازی پنل اختصاصی، کافیست ۱ میلیون تومان واریز کنید.
پس از واریز، شما به یک پنل کامل و شخصی دسترسی خواهید داشت که امکان ساخت و مدیریت اکانت‌ها را برایتان فراهم می‌کند.

📦 قیمت پلن‌ها برای نمایندگان:
🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۲۲ کاهش)
🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۱۶ کاهش)
🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره (٪۱۶ کاهش)

🔹 اکانت‌ها کاملاً نامحدود هستند (بدون محدودیت حجم یا سرعت)
🔹 شما تعیین‌کننده قیمت فروش به مشتری هستید
🔹 پشتیبانی کامل و ۲۴ ساعته

🔻 در صورت تایید موارد بالا روش پرداخت خود را انتخاب کنید
    `;

    userStates.set(userId, "awaiting_agency_payment_method");
    
    await sendTelegramMessage(
        chatId,
        agencyText,
        { reply_markup: getPaymentMethodKeyboard() }
    );
}

async function showInviteLink(chatId, userId) {
    const inviteLink = `https://t.me/${process.env.BOT_USERNAME || 'teazvpn_bot'}?start=${userId}`;
    
    const message = `
💵 لینک اختصاصی شما برای دعوت دوستان:
${inviteLink}

برای هر دعوت موفق، ۱۰,۰۰۰ تومان به موجودی شما اضافه خواهد شد.
    `;
    
    try {
        await sendTelegramMessage(
            chatId,
            message,
            { reply_markup: getMainKeyboard() }
        );
    } catch (error) {
        console.error('Error sending invite link:', error);
    }
}

async function handleCallbackQuery(callbackQuery) {
    const userId = callbackQuery.from.id;
    const chatId = callbackQuery.message.chat.id;
    const data = callbackQuery.data;

    // فقط ادمین مجاز است
    if (userId !== ADMIN_ID) {
        await answerCallbackQuery(callbackQuery.id, "⚠️ شما اجازه این کار را ندارید.");
        return;
    }

    await answerCallbackQuery(callbackQuery.id);

    if (data.startsWith("approve_")) {
        const parts = data.split("_");
        const paymentId = parts[1];
        const targetUserId = parseInt(parts[2]);
        const amount = parseInt(parts[3]);
        
        await approvePayment(paymentId, targetUserId, amount, chatId, callbackQuery.message.message_id);
    } else if (data.startsWith("reject_")) {
        const parts = data.split("_");
        const paymentId = parts[1];
        const targetUserId = parseInt(parts[2]);
        
        await rejectPayment(paymentId, targetUserId, chatId, callbackQuery.message.message_id);
    }
}

async function approvePayment(paymentId, targetUserId, amount, adminChatId, messageId) {
    try {
        // بروزرسانی وضعیت کاربر
        let user = userData.get(targetUserId) || { balance: 0 };
        user.balance = (user.balance || 0) + amount;
        userData.set(targetUserId, user);
        
        // اطلاع به کاربر
        await sendTelegramMessage(
            targetUserId,
            `💰 پرداخت تایید شد. موجودی ${formatNumber(amount)} تومان اضافه شد.`
        );
        
        // حذف کیبورد از پیام ادمین
        await editMessageReplyMarkup(adminChatId, messageId, { inline_keyboard: [] });
        
        // اطلاع به ادمین
        await sendTelegramMessage(adminChatId, "✅ پرداخت تایید شد.");

    } catch (error) {
        console.error('Error approving payment:', error);
        await sendTelegramMessage(adminChatId, "⚠️ خطا در تایید پرداخت.");
    }
}

async function rejectPayment(paymentId, targetUserId, adminChatId, messageId) {
    try {
        // اطلاع به کاربر
        await sendTelegramMessage(
            targetUserId,
            "❌ پرداخت شما رد شد. با پشتیبانی تماس بگیرید."
        );
        
        // حذف کیبورد از پیام ادمین
        await editMessageReplyMarkup(adminChatId, messageId, { inline_keyboard: [] });
        
        // اطلاع به ادمین
        await sendTelegramMessage(adminChatId, "❌ پرداخت رد شد.");

    } catch (error) {
        console.error('Error rejecting payment:', error);
        await sendTelegramMessage(adminChatId, "⚠️ خطا در رد پرداخت.");
    }
}

// ========== تابع اصلی هندلر ==========
async function handleMessage(msg) {
    const chatId = msg.chat.id;
    const userId = msg.from.id;
    const text = msg.text || "";
    
    console.log(`User ${userId} sent message: ${text}`);

    // بازگشت به منو
    if (text === "بازگشت به منو" || text === "⬅️ بازگشت به منو") {
        userStates.delete(userId);
        await sendTelegramMessage(chatId, "🌐 منوی اصلی:", { reply_markup: getMainKeyboard() });
        return;
    }

    const state = userStates.get(userId);
    
    // پردازش وضعیت‌های مختلف
    if (state) {
        if (state === "awaiting_deposit_amount") {
            await handleDepositAmount(chatId, userId, text);
            return;
        } else if (state.startsWith("awaiting_deposit_receipt_")) {
            const paymentId = state.split("_")[2];
            await handlePaymentReceipt(chatId, userId, paymentId, text, msg);
            return;
        }
    }

    // پردازش دستورات اصلی
    switch (text) {
        case "💰 موجودی":
            await handleBalance(chatId, userId);
            break;
            
        case "نمایش موجودی":
            await showBalance(chatId, userId);
            break;
            
        case "افزایش موجودی":
            await requestDepositAmount(chatId, userId);
            break;
            
        case "💳 خرید اشتراک":
            await handleSubscriptionPurchase(chatId, userId);
            break;
            
        case "📂 اشتراک‌های من":
            await showSubscriptions(chatId, userId);
            break;
            
        case "💡 راهنمای اتصال":
            await showConnectionGuide(chatId);
            break;
            
        case "🧑‍💼 درخواست نمایندگی":
            await handleAgencyRequest(chatId, userId);
            break;
            
        case "☎️ پشتیبانی":
            await sendTelegramMessage(chatId, "📞 پشتیبانی: https://t.me/teazadmin");
            break;
            
        case "💵 اعتبار رایگان":
            await showInviteLink(chatId, userId);
            break;
            
        case "🎁 اشتراک تست رایگان":
            await sendTelegramMessage(
                chatId,
                "🎁 برای دریافت اشتراک تست رایگان، لطفا با پشتیبانی تماس بگیرید: https://t.me/teazadmin"
            );
            break;
            
        default:
            // پردازش انتخاب پلن
            if (text.includes("ماهه")) {
                await handlePlanSelection(chatId, userId, text);
            } else {
                await sendTelegramMessage(
                    chatId,
                    "⚠️ لطفاً از دکمه‌های کیبورد استفاده کنید.",
                    { reply_markup: getMainKeyboard() }
                );
            }
    }
}

// ========== Worker Entry Point ==========
export default {
    async fetch(request, env, ctx) {
        try {
            // Set environment variables
            process.env.BOT_TOKEN = env.BOT_TOKEN || process.env.BOT_TOKEN;
            process.env.CHANNEL_USERNAME = env.CHANNEL_USERNAME || process.env.CHANNEL_USERNAME;
            process.env.ADMIN_ID = env.ADMIN_ID || process.env.ADMIN_ID;
            process.env.TRON_ADDRESS = env.TRON_ADDRESS || process.env.TRON_ADDRESS;
            process.env.BANK_CARD = env.BANK_CARD || process.env.BANK_CARD;
            process.env.BOT_USERNAME = env.BOT_USERNAME || "teazvpn_bot";

            const url = new URL(request.url);
            
            // Webhook endpoint
            if (url.pathname === '/webhook' && request.method === 'POST') {
                const update = await request.json();
                
                // Process update
                if (update.message) {
                    if (update.message.text && update.message.text.startsWith('/start')) {
                        await handleStart(update.message);
                    } else {
                        await handleMessage(update.message);
                    }
                } else if (update.callback_query) {
                    await handleCallbackQuery(update.callback_query);
                }
                
                return new Response('OK', { status: 200 });
            }
            
            // Health check endpoint
            if (url.pathname === '/health' || url.pathname === '/') {
                return new Response(JSON.stringify({
                    status: 'healthy',
                    service: 'Teaz VPN Bot',
                    timestamp: new Date().toISOString(),
                    environment: process.env.NODE_ENV || 'production'
                }), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                });
            }
            
            // Set webhook endpoint
            if (url.pathname === '/set-webhook' && request.method === 'GET') {
                const webhookUrl = `${url.origin}/webhook`;
                const setWebhookResponse = await fetch(`${TELEGRAM_API}/setWebhook`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        url: webhookUrl
                    })
                });
                
                const result = await setWebhookResponse.json();
                
                return new Response(JSON.stringify({
                    success: result.ok,
                    message: result.description,
                    webhook_url: webhookUrl
                }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }
            
            // Set commands
            if (url.pathname === '/set-commands' && request.method === 'GET') {
                await setMyCommands([
                    { command: "start", description: "شروع ربات" }
                ]);
                
                return new Response('Commands set successfully', { status: 200 });
            }
            
            // Default response
            return new Response('Teaz VPN Bot API\n\nEndpoints:\n- POST /webhook\n- GET /health\n- GET /set-webhook\n- GET /set-commands', {
                status: 200,
                headers: { 'Content-Type': 'text/plain' }
            });
            
        } catch (error) {
            console.error('Error:', error);
            return new Response('Internal Server Error', { 
                status: 500,
                headers: { 'Content-Type': 'text/plain' }
            });
        }
    }
};
