// index.js - کد اصلی بات تلگرام

const TelegramBot = require('node-telegram-bot-api');
const { Pool } = require('pg');
const crypto = require('crypto');
const { promisify } = require('util');
const fs = require('fs').promises;
const path = require('path');

// ---------- تنظیمات اولیه ----------
const TOKEN = process.env.BOT_TOKEN || "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg";
const CHANNEL_USERNAME = "@teazvpn";
const ADMIN_ID = 5542927340;
const TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb";
const BANK_CARD = "6037 9975 9717 2684";

// ---------- ایجاد نمونه بات ----------
const bot = new TelegramBot(TOKEN, { polling: process.env.NODE_ENV !== 'production' });

// ---------- لاگینگ ----------
const log = (level, message, data = {}) => {
    const timestamp = new Date().toISOString();
    const logMessage = `${timestamp} - ${level.toUpperCase()} - ${message}`;
    
    console.log(logMessage);
    if (Object.keys(data).length > 0) {
        console.log('Data:', data);
    }
};

// ---------- PostgreSQL connection ----------
const DATABASE_URL = process.env.DATABASE_URL;
let pool;

const initDbPool = async () => {
    if (!DATABASE_URL) {
        throw new Error("DATABASE_URL environment variable is not set.");
    }
    
    try {
        pool = new Pool({
            connectionString: DATABASE_URL,
            ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false
        });
        
        // Test connection
        await pool.query('SELECT 1');
        log('info', 'Database pool initialized successfully');
        return pool;
    } catch (error) {
        log('error', 'Failed to initialize database pool:', error);
        throw error;
    }
};

const dbQuery = async (text, params = []) => {
    if (!pool) {
        await initDbPool();
    }
    
    try {
        const result = await pool.query(text, params);
        return result;
    } catch (error) {
        log('error', 'Database query error:', { query: text, params, error: error.message });
        throw error;
    }
};

// ---------- ساخت جداول دیتابیس ----------
const createTables = async () => {
    try {
        // جدول کاربران
        await dbQuery(`
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance BIGINT DEFAULT 0,
                invited_by BIGINT,
                phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_agent BOOLEAN DEFAULT FALSE,
                is_new_user BOOLEAN DEFAULT TRUE
            )
        `);

        // جدول پرداخت‌ها
        await dbQuery(`
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                amount BIGINT,
                status TEXT,
                type TEXT,
                payment_method TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        `);

        // جدول اشتراک‌ها
        await dbQuery(`
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                payment_id INTEGER,
                plan TEXT,
                config TEXT,
                status TEXT DEFAULT 'pending',
                start_date TIMESTAMP,
                duration_days INTEGER
            )
        `);

        // جدول کوپن‌ها
        await dbQuery(`
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                discount_percent INTEGER,
                user_id BIGINT,
                is_used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expiry_date TIMESTAMP GENERATED ALWAYS AS (created_at + INTERVAL '3 days') STORED
            )
        `);

        // مهاجرت داده‌های موجود
        await dbQuery(`
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_new_user') THEN
                    ALTER TABLE users ADD COLUMN is_new_user BOOLEAN DEFAULT TRUE;
                END IF;
                
                UPDATE users SET is_new_user = FALSE WHERE is_new_user IS NULL;
            END $$;
        `);

        log('info', 'Database tables created and migrated successfully');
    } catch (error) {
        log('error', 'Error creating or migrating tables:', error);
    }
};

// ---------- وضعیت کاربران در حافظه ----------
const userStates = new Map();
const userData = new Map();

// ---------- تابع‌های کمکی ----------
const generateCouponCode = (length = 8) => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
};

const formatNumber = (num) => {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
};

// ---------- توابع دیتابیس ----------
const ensureUser = async (user_id, username, invited_by = null) => {
    try {
        // بررسی وجود کاربر
        const existingUser = await dbQuery(
            'SELECT user_id FROM users WHERE user_id = $1',
            [user_id]
        );

        if (existingUser.rows.length === 0) {
            // کاربر جدید
            await dbQuery(
                `INSERT INTO users (user_id, username, invited_by, is_agent, is_new_user) 
                 VALUES ($1, $2, $3, FALSE, TRUE)`,
                [user_id, username, invited_by]
            );

            // اعتبار برای دعوت‌کننده
            if (invited_by && invited_by !== user_id) {
                const inviter = await dbQuery(
                    'SELECT user_id FROM users WHERE user_id = $1',
                    [invited_by]
                );
                
                if (inviter.rows.length > 0) {
                    await addBalance(invited_by, 10000);
                }
            }

            log('info', `New user registered: ${user_id} (@${username})`);
        } else {
            // برچسب کاربر جدید را غیرفعال کن
            await dbQuery(
                'UPDATE users SET is_new_user = FALSE WHERE user_id = $1',
                [user_id]
            );
            log('info', `Existing user marked as non-new: ${user_id}`);
        }
    } catch (error) {
        log('error', 'Error ensuring user:', error);
    }
};

const isUserMember = async (userId) => {
    try {
        const chatMember = await bot.getChatMember(CHANNEL_USERNAME, userId);
        return ['member', 'administrator', 'creator'].includes(chatMember.status);
    } catch (error) {
        log('error', 'Error checking channel membership:', error);
        return false;
    }
};

const addBalance = async (user_id, amount) => {
    try {
        await dbQuery(
            'UPDATE users SET balance = COALESCE(balance, 0) + $1 WHERE user_id = $2',
            [amount, user_id]
        );
        log('info', `Added ${amount} to balance for user ${user_id}`);
    } catch (error) {
        log('error', 'Error adding balance:', error);
    }
};

const deductBalance = async (user_id, amount) => {
    try {
        await dbQuery(
            'UPDATE users SET balance = COALESCE(balance, 0) - $1 WHERE user_id = $2',
            [amount, user_id]
        );
        log('info', `Deducted ${amount} from balance for user ${user_id}`);
    } catch (error) {
        log('error', 'Error deducting balance:', error);
    }
};

const getBalance = async (user_id) => {
    try {
        const result = await dbQuery(
            'SELECT balance FROM users WHERE user_id = $1',
            [user_id]
        );
        
        if (result.rows.length > 0) {
            return parseInt(result.rows[0].balance) || 0;
        }
        return 0;
    } catch (error) {
        log('error', 'Error getting balance:', error);
        return 0;
    }
};

const isUserAgent = async (user_id) => {
    try {
        const result = await dbQuery(
            'SELECT is_agent FROM users WHERE user_id = $1',
            [user_id]
        );
        
        if (result.rows.length > 0) {
            return result.rows[0].is_agent || false;
        }
        return false;
    } catch (error) {
        log('error', 'Error checking agent status:', error);
        return false;
    }
};

const setUserAgent = async (user_id, isAgent = true) => {
    try {
        await dbQuery(
            'UPDATE users SET is_agent = $1 WHERE user_id = $2',
            [isAgent, user_id]
        );
        log('info', `User ${user_id} agent status set to: ${isAgent}`);
    } catch (error) {
        log('error', 'Error setting agent status:', error);
    }
};

const addPayment = async (user_id, amount, type, payment_method, description = '', coupon_code = null) => {
    try {
        const result = await dbQuery(
            `INSERT INTO payments (user_id, amount, status, type, payment_method, description) 
             VALUES ($1, $2, 'pending', $3, $4, $5) 
             RETURNING id`,
            [user_id, amount, type, payment_method, description]
        );

        if (coupon_code) {
            await dbQuery(
                'UPDATE coupons SET is_used = TRUE WHERE code = $1',
                [coupon_code]
            );
        }

        log('info', `Payment added for user ${user_id}, amount: ${amount}, type: ${type}`);
        return result.rows[0].id;
    } catch (error) {
        log('error', 'Error adding payment:', error);
        return null;
    }
};

const addSubscription = async (user_id, payment_id, plan) => {
    try {
        const durationMapping = {
            "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": 30,
            "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": 90,
            "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": 180,
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 30,
            "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 90,
            "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 180
        };
        
        const duration_days = durationMapping[plan] || 30;
        
        await dbQuery(
            `INSERT INTO subscriptions (user_id, payment_id, plan, status, start_date, duration_days) 
             VALUES ($1, $2, $3, 'pending', CURRENT_TIMESTAMP, $4)`,
            [user_id, payment_id, plan, duration_days]
        );
        
        log('info', `Subscription added for user ${user_id}, plan: ${plan}, duration: ${duration_days} days`);
    } catch (error) {
        log('error', 'Error adding subscription:', error);
        throw error;
    }
};

const updatePaymentStatus = async (payment_id, status) => {
    try {
        await dbQuery(
            'UPDATE payments SET status = $1 WHERE id = $2',
            [status, payment_id]
        );
        log('info', `Payment ${payment_id} status updated to: ${status}`);
    } catch (error) {
        log('error', 'Error updating payment status:', error);
    }
};

const updateSubscriptionConfig = async (payment_id, config) => {
    try {
        await dbQuery(
            `UPDATE subscriptions SET config = $1, status = 'active' 
             WHERE payment_id = $2`,
            [config, payment_id]
        );
        log('info', `Subscription config updated for payment ${payment_id}`);
    } catch (error) {
        log('error', 'Error updating subscription config:', error);
    }
};

const getUserSubscriptions = async (user_id) => {
    try {
        const result = await dbQuery(
            `SELECT s.id, s.plan, s.config, s.status, s.payment_id, 
                    s.start_date, s.duration_days, u.username
             FROM subscriptions s
             LEFT JOIN users u ON s.user_id = u.user_id
             WHERE s.user_id = $1
             ORDER BY s.status DESC, s.start_date DESC`,
            [user_id]
        );

        const subscriptions = [];
        const now = new Date();

        for (const row of result.rows) {
            const startDate = row.start_date || now;
            const durationDays = row.duration_days || 30;
            const endDate = new Date(startDate.getTime() + durationDays * 24 * 60 * 60 * 1000);
            
            let status = row.status;
            if (status === 'active' && now > endDate) {
                status = 'inactive';
                await dbQuery(
                    'UPDATE subscriptions SET status = $1 WHERE id = $2',
                    ['inactive', row.id]
                );
            }

            subscriptions.push({
                id: row.id,
                plan: row.plan,
                config: row.config,
                status: status,
                payment_id: row.payment_id,
                start_date: startDate,
                duration_days: durationDays,
                username: row.username || user_id.toString(),
                end_date: endDate
            });
        }

        return subscriptions;
    } catch (error) {
        log('error', 'Error getting user subscriptions:', error);
        return [];
    }
};

// ---------- توابع کیبورد ----------
const getMainKeyboard = () => {
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
};

const getBalanceKeyboard = () => {
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
};

const getBackKeyboard = () => {
    return {
        keyboard: [[{ text: "⬅️ بازگشت به منو" }]],
        resize_keyboard: true
    };
};

const getSubscriptionKeyboard = (isAgent = false) => {
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
};

const getPaymentMethodKeyboard = () => {
    return {
        keyboard: [
            [{ text: "🏦 کارت به کارت" }],
            [{ text: "💎 پرداخت با ترون" }],
            [{ text: "💰 پرداخت با موجودی" }],
            [{ text: "⬅️ بازگشت به منو" }]
        ],
        resize_keyboard: true
    };
};

// ---------- دستور /start ----------
const handleStart = async (msg) => {
    const chatId = msg.chat.id;
    const userId = msg.from.id;
    const username = msg.from.username || "";
    const args = msg.text ? msg.text.split(' ') : [];

    // چک کردن عضویت در کانال
    const isMember = await isUserMember(userId);
    if (!isMember) {
        const inlineKeyboard = {
            inline_keyboard: [[
                {
                    text: "📢 عضویت در کانال",
                    url: `https://t.me/${CHANNEL_USERNAME.replace('@', '')}`
                }
            ]]
        };
        
        await bot.sendMessage(
            chatId,
            "❌ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس مجدد /start را بزنید.",
            { reply_markup: inlineKeyboard }
        );
        return;
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

    // ثبت کاربر در دیتابیس
    await ensureUser(userId, username, invitedBy);

    // پاک کردن وضعیت
    userStates.delete(userId);
    userData.delete(userId);

    await bot.sendMessage(
        chatId,
        "🌐 به فروشگاه تیز VPN خوش آمدید!\n\nیک گزینه را انتخاب کنید:",
        { reply_markup: getMainKeyboard() }
    );
};

// ---------- پردازش پیام‌های متنی ----------
const handleMessage = async (msg) => {
    const chatId = msg.chat.id;
    const userId = msg.from.id;
    const text = msg.text || "";
    
    log('info', `User ${userId} sent message: ${text}`);

    // بازگشت به منو
    if (text === "بازگشت به منو" || text === "⬅️ بازگشت به منو") {
        userStates.delete(userId);
        userData.delete(userId);
        await bot.sendMessage(chatId, "🌐 منوی اصلی:", { reply_markup: getMainKeyboard() });
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
            await bot.sendMessage(chatId, "📞 پشتیبانی: https://t.me/teazadmin");
            break;
            
        case "💵 اعتبار رایگان":
            await showInviteLink(chatId, userId);
            break;
            
        case "🎁 اشتراک تست رایگان":
            await bot.sendMessage(
                chatId,
                "🎁 برای دریافت اشتراک تست رایگان، لطفا با پشتیبانی تماس بگیرید: https://t.me/teazadmin"
            );
            break;
            
        default:
            // پردازش انتخاب پلن
            if (text.includes("ماهه")) {
                await handlePlanSelection(chatId, userId, text);
            } else {
                await bot.sendMessage(
                    chatId,
                    "⚠️ لطفاً از دکمه‌های کیبورد استفاده کنید.",
                    { reply_markup: getMainKeyboard() }
                );
            }
    }
};

// ---------- توابع پردازش وضعیت‌ها ----------
const handleBalance = async (chatId, userId) => {
    userStates.delete(userId);
    await bot.sendMessage(
        chatId,
        "💰 بخش موجودی:\nیک گزینه را انتخاب کنید:",
        { reply_markup: getBalanceKeyboard() }
    );
};

const showBalance = async (chatId, userId) => {
    const balance = await getBalance(userId);
    await bot.sendMessage(
        chatId,
        `💰 موجودی شما: ${formatNumber(balance)} تومان`,
        { reply_markup: getBalanceKeyboard() }
    );
};

const requestDepositAmount = async (chatId, userId) => {
    userStates.set(userId, "awaiting_deposit_amount");
    await bot.sendMessage(
        chatId,
        "💳 لطفاً مبلغ واریزی را به تومان وارد کنید (مثال: 90000):",
        { reply_markup: getBackKeyboard() }
    );
};

const handleDepositAmount = async (chatId, userId, text) => {
    const amount = parseInt(text);
    
    if (isNaN(amount) || amount <= 0) {
        await bot.sendMessage(
            chatId,
            "⚠️ لطفاً یک مبلغ معتبر وارد کنید.",
            { reply_markup: getBackKeyboard() }
        );
        return;
    }

    const paymentId = await addPayment(userId, amount, "increase_balance", "card_to_card");
    
    if (paymentId) {
        userStates.set(userId, `awaiting_deposit_receipt_${paymentId}`);
        userData.set(userId, { paymentId, amount });
        
        await bot.sendMessage(
            chatId,
            `لطفاً ${formatNumber(amount)} تومان واریز کنید و فیش را ارسال کنید:\n\n` +
            `💎 آدرس کیف پول TRON:\n\`${TRON_ADDRESS}\`\n\n` +
            `یا\n\n🏦 شماره کارت بانکی:\n\`${BANK_CARD}\`\nفرهنگ`,
            { 
                parse_mode: 'Markdown',
                reply_markup: getBackKeyboard() 
            }
        );
    } else {
        await bot.sendMessage(
            chatId,
            "⚠️ خطا در ثبت پرداخت. لطفاً دوباره تلاش کنید.",
            { reply_markup: getMainKeyboard() }
        );
        userStates.delete(userId);
    }
};

const handlePaymentReceipt = async (chatId, userId, paymentId, text, msg) => {
    try {
        // دریافت اطلاعات پرداخت
        const paymentResult = await dbQuery(
            'SELECT amount, type, description FROM payments WHERE id = $1',
            [paymentId]
        );

        if (paymentResult.rows.length === 0) {
            await bot.sendMessage(chatId, "⚠️ پرداخت یافت نشد.");
            return;
        }

        const { amount, type, description } = paymentResult.rows[0];
        const caption = `💳 فیش پرداختی از کاربر ${userId} (@${msg.from.username || 'بدون نام'}):\n` +
                       `مبلغ: ${formatNumber(amount)} تومان\n` +
                       `نوع: ${type === 'agency_request' ? 'درخواست نمایندگی' : type}`;

        const inlineKeyboard = {
            inline_keyboard: [[
                { text: "✅ تایید", callback_data: `approve_${paymentId}` },
                { text: "❌ رد", callback_data: `reject_${paymentId}` }
            ]]
        };

        // ارسال به ادمین
        if (msg.photo) {
            const photoId = msg.photo[msg.photo.length - 1].file_id;
            await bot.sendPhoto(ADMIN_ID, photoId, {
                caption: caption,
                reply_markup: inlineKeyboard
            });
        } else if (msg.document) {
            const docId = msg.document.file_id;
            await bot.sendDocument(ADMIN_ID, docId, {
                caption: caption,
                reply_markup: inlineKeyboard
            });
        } else {
            await bot.sendMessage(chatId, "⚠️ لطفاً فیش پرداخت را به صورت عکس یا فایل ارسال کنید.");
            return;
        }

        await bot.sendMessage(
            chatId,
            "✅ فیش شما برای ادمین ارسال شد، لطفاً منتظر تایید باشید.",
            { reply_markup: getMainKeyboard() }
        );

        userStates.delete(userId);
        userData.delete(userId);

    } catch (error) {
        log('error', 'Error processing payment receipt:', error);
        await bot.sendMessage(chatId, "⚠️ خطا در پردازش فیش پرداخت.");
    }
};

const handleSubscriptionPurchase = async (chatId, userId) => {
    const isAgent = await isUserAgent(userId);
    await bot.sendMessage(
        chatId,
        "💳 پلن را انتخاب کنید:",
        { reply_markup: getSubscriptionKeyboard(isAgent) }
    );
};

const handlePlanSelection = async (chatId, userId, planText) => {
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
        await bot.sendMessage(
            chatId,
            "⚠️ پلن انتخاب شده نامعتبر است.",
            { reply_markup: getMainKeyboard() }
        );
        return;
    }

    userStates.set(userId, `awaiting_payment_method_${amount}_${planText}`);
    userData.set(userId, { plan: planText, amount: amount });

    await bot.sendMessage(
        chatId,
        "💳 روش خرید را انتخاب کنید:",
        { reply_markup: getPaymentMethodKeyboard() }
    );
};

const showSubscriptions = async (chatId, userId) => {
    try {
        const subscriptions = await getUserSubscriptions(userId);
        
        if (subscriptions.length === 0) {
            await bot.sendMessage(
                chatId,
                "📂 شما هنوز اشتراکی ندارید.",
                { reply_markup: getMainKeyboard() }
            );
            return;
        }

        let message = "📂 لیست کامل اشتراک‌های شما:\n\n";
        const now = new Date();

        for (const sub of subscriptions) {
            message += `🔹 اشتراک #${sub.id}\n`;
            message += `📌 پلن: ${sub.plan}\n`;
            message += `🆔 کد خرید: #${sub.payment_id}\n`;
            message += `📊 وضعیت: ${sub.status === 'active' ? '✅ فعال' : '⏳ در انتظار'}\n`;
            
            if (sub.status === 'active') {
                const remainingDays = Math.max(0, Math.floor((sub.end_date - now) / (1000 * 60 * 60 * 24)));
                message += `⏳ زمان باقی‌مانده: ${remainingDays} روز\n`;
                message += `📅 تاریخ شروع: ${sub.start_date.toLocaleString('fa-IR')}\n`;
                message += `📅 تاریخ انقضا: ${sub.end_date.toLocaleString('fa-IR')}\n`;
            }
            
            if (sub.config) {
                message += `🔐 کانفیگ:\n\`\`\`\n${sub.config}\n\`\`\`\n`;
            }
            
            message += "------------------------\n\n";
        }

        // تقسیم پیام به قسمت‌های کوچکتر
        const maxLength = 4000;
        if (message.length > maxLength) {
            const parts = [];
            let currentPart = "";
            
            const lines = message.split('\n');
            for (const line of lines) {
                if (currentPart.length + line.length + 1 > maxLength) {
                    parts.push(currentPart);
                    currentPart = line + '\n';
                } else {
                    currentPart += line + '\n';
                }
            }
            
            if (currentPart) {
                parts.push(currentPart);
            }
            
            for (let i = 0; i < parts.length; i++) {
                const options = i === parts.length - 1 ? { reply_markup: getMainKeyboard() } : {};
                await bot.sendMessage(chatId, parts[i], options);
            }
        } else {
            await bot.sendMessage(
                chatId,
                message,
                { 
                    reply_markup: getMainKeyboard(),
                    parse_mode: 'Markdown'
                }
            );
        }

    } catch (error) {
        log('error', 'Error showing subscriptions:', error);
        await bot.sendMessage(
            chatId,
            "⚠️ خطا در نمایش اشتراک‌ها. لطفاً دوباره تلاش کنید.",
            { reply_markup: getMainKeyboard() }
        );
    }
};

const showConnectionGuide = async (chatId) => {
    const guideTexts = {
        "📗 اندروید": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های V2RayNG یا Hiddify(پیشنهادی) است ✅\nبا این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📕 آیفون/مک": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن‌های Singbox(پیشنهادی) یا Streisand یا V2box(پیشنهادی) هست ✅\nبا این برنامه‌ها می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📘 ویندوز": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\nبا این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀",
        "📙 لینوکس": "برای استفاده از کانفیگ، پیشنهاد ما استفاده از اپلیکیشن V2rayN هست ✅\nبا این برنامه‌ می‌تونی خیلی راحت و سریع کانفیگ رو وارد کنی و به اینترنت بدون محدودیت وصل بشی 🚀"
    };

    const keyboard = {
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

    await bot.sendMessage(
        chatId,
        "💡 راهنمای راه‌اندازی\nدستگاه خود را انتخاب کنید:",
        { reply_markup: keyboard }
    );

    // هندلر برای انتخاب دستگاه
    bot.once('message', async (msg) => {
        if (msg.chat.id === chatId && guideTexts[msg.text]) {
            await bot.sendMessage(
                chatId,
                guideTexts[msg.text],
                { reply_markup: keyboard }
            );
        }
    });
};

const handleAgencyRequest = async (chatId, userId) => {
    const isAgent = await isUserAgent(userId);
    
    if (isAgent) {
        await bot.sendMessage(
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
    
    await bot.sendMessage(
        chatId,
        agencyText,
        { reply_markup: getPaymentMethodKeyboard() }
    );
};

const showInviteLink = async (chatId, userId) => {
    const inviteLink = `https://t.me/teazvpn_bot?start=${userId}`;
    
    const message = `
💵 لینک اختصاصی شما برای دعوت دوستان:
${inviteLink}

برای هر دعوت موفق، ۱۰,۰۰۰ تومان به موجودی شما اضافه خواهد شد.
    `;
    
    try {
        // تلاش برای ارسال عکس
        await bot.sendPhoto(
            chatId,
            'https://via.placeholder.com/600x400/1a73e8/ffffff?text=Teaz+VPN',
            {
                caption: message,
                reply_markup: getMainKeyboard()
            }
        );
    } catch (error) {
        // اگر ارسال عکس با مشکل مواجه شد، فقط متن ارسال کن
        await bot.sendMessage(
            chatId,
            message,
            { reply_markup: getMainKeyboard() }
        );
    }
};

// ---------- پردازش کیبورد اینلاین ----------
const handleCallbackQuery = async (callbackQuery) => {
    const userId = callbackQuery.from.id;
    const chatId = callbackQuery.message.chat.id;
    const data = callbackQuery.data;

    // فقط ادمین مجاز است
    if (userId !== ADMIN_ID) {
        await bot.answerCallbackQuery(callbackQuery.id, {
            text: "⚠️ شما اجازه این کار را ندارید."
        });
        return;
    }

    await bot.answerCallbackQuery(callbackQuery.id);

    if (data.startsWith("approve_")) {
        const paymentId = data.split("_")[1];
        await approvePayment(paymentId, chatId);
    } else if (data.startsWith("reject_")) {
        const paymentId = data.split("_")[1];
        await rejectPayment(paymentId, chatId);
    } else if (data.startsWith("send_config_")) {
        const paymentId = data.split("_")[2];
        userStates.set(ADMIN_ID, `awaiting_config_${paymentId}`);
        await bot.sendMessage(chatId, "لطفاً کانفیگ را ارسال کنید:");
    }
};

const approvePayment = async (paymentId, adminChatId) => {
    try {
        const paymentResult = await dbQuery(
            'SELECT user_id, amount, type, description FROM payments WHERE id = $1',
            [paymentId]
        );

        if (paymentResult.rows.length === 0) {
            await bot.sendMessage(adminChatId, "⚠️ پرداخت یافت نشد.");
            return;
        }

        const { user_id, amount, type, description } = paymentResult.rows[0];
        
        await updatePaymentStatus(paymentId, "approved");

        switch (type) {
            case "increase_balance":
                await addBalance(user_id, amount);
                await bot.sendMessage(
                    user_id,
                    `💰 پرداخت تایید شد. موجودی ${formatNumber(amount)} تومان اضافه شد.`
                );
                await bot.sendMessage(adminChatId, "✅ پرداخت تایید شد.");
                break;

            case "buy_subscription":
                await bot.sendMessage(
                    user_id,
                    `✅ پرداخت تایید شد. اشتراک شما (کد خرید: #${paymentId}) ارسال خواهد شد.`
                );
                
                const configKeyboard = {
                    inline_keyboard: [[
                        { text: "🟣 ارسال کانفیگ", callback_data: `send_config_${paymentId}` }
                    ]]
                };
                
                await bot.sendMessage(
                    adminChatId,
                    `✅ پرداخت برای اشتراک (${description}) تایید شد.`,
                    { reply_markup: configKeyboard }
                );
                break;

            case "agency_request":
                await setUserAgent(user_id, true);
                await addBalance(user_id, amount);
                await bot.sendMessage(
                    user_id,
                    "✅ فیش شما تایید و نمایندگی به شما اعطا شد! ۱,۰۰۰,۰۰۰ تومان به موجودی شما اضافه شد."
                );
                await bot.sendMessage(adminChatId, "✅ درخواست نمایندگی تایید شد.");
                break;
        }

        // حذف کیبورد از پیام قبلی
        try {
            await bot.editMessageReplyMarkup(
                { inline_keyboard: [] },
                {
                    chat_id: adminChatId,
                    message_id: callbackQuery.message.message_id
                }
            );
        } catch (error) {
            log('error', 'Error removing inline keyboard:', error);
        }

    } catch (error) {
        log('error', 'Error approving payment:', error);
        await bot.sendMessage(adminChatId, "⚠️ خطا در تایید پرداخت.");
    }
};

const rejectPayment = async (paymentId, adminChatId) => {
    try {
        const paymentResult = await dbQuery(
            'SELECT user_id, amount, type FROM payments WHERE id = $1',
            [paymentId]
        );

        if (paymentResult.rows.length === 0) {
            await bot.sendMessage(adminChatId, "⚠️ پرداخت یافت نشد.");
            return;
        }

        const { user_id, amount, type } = paymentResult.rows[0];
        
        await updatePaymentStatus(paymentId, "rejected");
        await bot.sendMessage(
            user_id,
            "❌ پرداخت شما رد شد. با پشتیبانی تماس بگیرید."
        );
        await bot.sendMessage(adminChatId, "❌ پرداخت رد شد.");

        // حذف کیبورد از پیام قبلی
        try {
            await bot.editMessageReplyMarkup(
                { inline_keyboard: [] },
                {
                    chat_id: adminChatId,
                    message_id: callbackQuery.message.message_id
                }
            );
        } catch (error) {
            log('error', 'Error removing inline keyboard:', error);
        }

    } catch (error) {
        log('error', 'Error rejecting payment:', error);
        await bot.sendMessage(adminChatId, "⚠️ خطا در رد پرداخت.");
    }
};

// ---------- تنظیم دستورات بات ----------
const setBotCommands = async () => {
    try {
        const commands = [
            { command: "/start", description: "شروع ربات" }
        ];

        if (process.env.NODE_ENV !== 'production') {
            commands.push(
                { command: "/stats", description: "آمار ربات (ادمین)" },
                { command: "/users", description: "لیست کاربران (ادمین)" }
            );
        }

        await bot.setMyCommands(commands);
        log('info', 'Bot commands set successfully');
    } catch (error) {
        log('error', 'Error setting bot commands:', error);
    }
};

// ---------- راه‌اندازی سرور ----------
const startServer = async () => {
    try {
        log('info', 'Starting Telegram Bot...');
        
        // اتصال به دیتابیس
        await initDbPool();
        
        // ساخت جداول
        await createTables();
        
        // تنظیم دستورات
        await setBotCommands();
        
        // تنظیم هندلرها
        bot.on('message', async (msg) => {
            try {
                if (msg.text && msg.text.startsWith('/start')) {
                    await handleStart(msg);
                } else {
                    await handleMessage(msg);
                }
            } catch (error) {
                log('error', 'Error processing message:', error);
            }
        });

        bot.on('callback_query', async (callbackQuery) => {
            try {
                await handleCallbackQuery(callbackQuery);
            } catch (error) {
                log('error', 'Error processing callback query:', error);
            }
        });

        // هندلر برای وضعیت‌های خاص
        bot.on('message', async (msg) => {
            try {
                const userId = msg.from.id;
                const state = userStates.get(userId);
                
                if (state && state.startsWith("awaiting_config_")) {
                    const paymentId = state.split("_")[2];
                    
                    if (msg.text) {
                        const config = msg.text;
                        
                        // دریافت اطلاعات پرداخت
                        const paymentResult = await dbQuery(
                            'SELECT user_id, description FROM payments WHERE id = $1',
                            [paymentId]
                        );
                        
                        if (paymentResult.rows.length > 0) {
                            const { user_id, description } = paymentResult.rows[0];
                            
                            // آپدیت کانفیگ
                            await updateSubscriptionConfig(paymentId, config);
                            
                            // ارسال کانفیگ به کاربر
                            await bot.sendMessage(
                                user_id,
                                `✅ کانفیگ اشتراک شما (${description})\nکد خرید: #${paymentId}\nدریافت شد:\n\`\`\`\n${config}\n\`\`\``,
                                { parse_mode: 'Markdown' }
                            );
                            
                            // پیام به ادمین
                            await bot.sendMessage(
                                ADMIN_ID,
                                "✅ کانفیگ با موفقیت به خریدار ارسال شد."
                            );
                            
                            userStates.delete(userId);
                        }
                    }
                }
            } catch (error) {
                log('error', 'Error processing config:', error);
            }
        });

        log('info', '✅ Bot started successfully!');
        
        // ارسال پیام راه‌اندازی به ادمین
        try {
            await bot.sendMessage(
                ADMIN_ID,
                `🤖 ربات تیز VPN با موفقیت راه‌اندازی شد!\n⏰ زمان: ${new Date().toLocaleString('fa-IR')}`
            );
        } catch (error) {
            log('error', 'Error sending startup message to admin:', error);
        }

    } catch (error) {
        log('error', '❌ Error starting bot:', error);
        process.exit(1);
    }
};

// ---------- کنترل graceful shutdown ----------
process.on('SIGTERM', async () => {
    log('info', 'Received SIGTERM, shutting down gracefully...');
    
    try {
        // ارسال پیام خاموشی به ادمین
        await bot.sendMessage(
            ADMIN_ID,
            `⚠️ ربات تیز VPN در حال خاموش شدن...\n⏰ زمان: ${new Date().toLocaleString('fa-IR')}`
        );
    } catch (error) {
        log('error', 'Error sending shutdown message:', error);
    }
    
    if (pool) {
        await pool.end();
    }
    
    log('info', 'Bot shut down successfully');
    process.exit(0);
});

process.on('SIGINT', async () => {
    log('info', 'Received SIGINT, shutting down...');
    
    if (pool) {
        await pool.end();
    }
    
    process.exit(0);
});

// ---------- شروع برنامه ----------
if (process.env.NODE_ENV === 'production') {
    // برای اجرا روی سرور با webhook
    const express = require('express');
    const app = express();
    
    app.use(express.json());
    
    app.post(`/webhook/${TOKEN}`, async (req, res) => {
        try {
            const update = req.body;
            await bot.processUpdate(update);
            res.sendStatus(200);
        } catch (error) {
            log('error', 'Webhook error:', error);
            res.sendStatus(500);
        }
    });
    
    app.get('/health', (req, res) => {
        res.json({ status: 'healthy', timestamp: new Date().toISOString() });
    });
    
    const PORT = process.env.PORT || 3000;
    app.listen(PORT, async () => {
        log('info', `Server running on port ${PORT}`);
        await startServer();
    });
} else {
    // برای اجرای محلی با polling
    startServer();
}
