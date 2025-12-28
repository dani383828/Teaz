import os
import logging
import asyncio
import random
import string
import json
import tempfile
import subprocess
import urllib.parse
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, Bot, ChatMember
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, 
    ContextTypes, MessageHandler, filters, 
    CallbackQueryHandler, CallbackContext
)

# ========== تنظیمات اولیه ==========
TOKEN = os.getenv("BOT_TOKEN") or "7084280622:AAGlwBy4FmMM3mc4OjjLQqa00Cg4t3jJzNg"
CHANNEL_USERNAME = "@teazvpn"
ADMIN_ID = 5542927340
TRON_ADDRESS = "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb"
BANK_CARD = "6037 9975 9717 2684"
BANK_NAME = "بانک ملت"
BANK_OWNER = "فرهنگ"

# آدرس‌های رندر
RENDER_BASE_URL = os.getenv("RENDER_BASE_URL") or "https://teaz.onrender.com"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_BASE_URL}{WEBHOOK_PATH}"

# تنظیمات پیشرفته لاگ‌گیری
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== FastAPI App ==========
app = FastAPI(title="Teaz VPN Bot", version="2.0.0")

# ========== دیتابیس PostgreSQL ==========
import psycopg2
from psycopg2 import pool, extras

DATABASE_URL = os.getenv("DATABASE_URL")
db_pool: Optional[pool.ThreadedConnectionPool] = None

class Database:
    @staticmethod
    def init():
        """ایجاد پول اتصال به دیتابیس"""
        global db_pool
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        
        try:
            db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=DATABASE_URL,
                cursor_factory=extras.DictCursor
            )
            logger.info("✅ پول دیتابیس با موفقیت ایجاد شد")
            return True
        except Exception as e:
            logger.error(f"❌ خطا در ایجاد پول دیتابیس: {e}")
            raise
    
    @staticmethod
    def close():
        """بستن تمام اتصالات دیتابیس"""
        global db_pool
        if db_pool:
            db_pool.closeall()
            db_pool = None
            logger.info("✅ اتصالات دیتابیس بسته شد")
    
    @staticmethod
    def get_connection():
        """دریافت یک اتصال از پول"""
        if not db_pool:
            raise RuntimeError("پول دیتابیس راه‌اندازی نشده است")
        return db_pool.getconn()
    
    @staticmethod
    def return_connection(conn):
        """بازگرداندن اتصال به پول"""
        if db_pool:
            db_pool.putconn(conn)
    
    @staticmethod
    async def execute(query: str, params: tuple = (), fetch: bool = False, 
                     fetchone: bool = False, returning: bool = False) -> Any:
        """اجرای کوئری روی دیتابیس به صورت ناهمگام"""
        conn = None
        cursor = None
        try:
            conn = await asyncio.to_thread(Database.get_connection)
            cursor = conn.cursor()
            
            cursor.execute(query, params)
            
            result = None
            if returning:
                result = cursor.fetchone()[0] if cursor.rowcount > 0 else None
            elif fetchone:
                result = cursor.fetchone()
            elif fetch:
                result = cursor.fetchall()
            
            if not query.strip().upper().startswith(('SELECT', 'WITH')):
                conn.commit()
            
            return result
            
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ خطای دیتابیس در کوئری: {query[:100]}... | پارامترها: {params} | خطا: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                await asyncio.to_thread(Database.return_connection, conn)

# ========== ایجاد جداول دیتابیس ==========
async def create_tables():
    """ایجاد جداول مورد نیاز در دیتابیس"""
    tables = [
        # جدول کاربران
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            first_name VARCHAR(255),
            last_name VARCHAR(255),
            balance BIGINT DEFAULT 0,
            invited_by BIGINT,
            phone VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_agent BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            total_invited INTEGER DEFAULT 0,
            total_spent BIGINT DEFAULT 0,
            language_code VARCHAR(10) DEFAULT 'fa'
        )
        """,
        
        # جدول پرداخت‌ها
        """
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            amount BIGINT NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            type VARCHAR(100) NOT NULL,
            payment_method VARCHAR(50),
            description TEXT,
            transaction_id VARCHAR(255),
            receipt_file_id VARCHAR(255),
            admin_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP,
            approved_by BIGINT
        )
        """,
        
        # جدول اشتراک‌ها
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            payment_id INTEGER REFERENCES payments(id),
            plan VARCHAR(255) NOT NULL,
            config TEXT,
            config_file_id VARCHAR(255),
            status VARCHAR(50) DEFAULT 'pending',
            start_date TIMESTAMP,
            end_date TIMESTAMP,
            duration_days INTEGER DEFAULT 30,
            device_count INTEGER DEFAULT 2,
            is_unlimited BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # جدول کدهای تخفیف
        """
        CREATE TABLE IF NOT EXISTS coupons (
            code VARCHAR(50) PRIMARY KEY,
            discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
            user_id BIGINT REFERENCES users(user_id),
            created_by BIGINT,
            is_used BOOLEAN DEFAULT FALSE,
            used_at TIMESTAMP,
            used_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expiry_date TIMESTAMP GENERATED ALWAYS AS (created_at + INTERVAL '3 days') STORED,
            max_uses INTEGER DEFAULT 1,
            current_uses INTEGER DEFAULT 0
        )
        """,
        
        # جدول لاگ فعالیت‌ها
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            action VARCHAR(100) NOT NULL,
            details TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # جدول لاگ خطاها
        """
        CREATE TABLE IF NOT EXISTS error_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            error_type VARCHAR(100),
            error_message TEXT,
            stack_trace TEXT,
            additional_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # جدول اطلاعیه‌ها
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            sender_id BIGINT,
            receiver_id BIGINT,
            notification_type VARCHAR(50),
            title VARCHAR(255),
            message TEXT,
            is_read BOOLEAN DEFAULT FALSE,
            read_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # ایجاد ایندکس‌ها
        """
        CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by);
        CREATE INDEX IF NOT EXISTS idx_users_is_agent ON users(is_agent);
        CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON activity_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON activity_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_notifications_receiver_id ON notifications(receiver_id);
        """
    ]
    
    try:
        for table_sql in tables:
            await Database.execute(table_sql)
        logger.info("✅ تمام جداول با موفقیت ایجاد شدند")
    except Exception as e:
        logger.error(f"❌ خطا در ایجاد جداول: {e}")
        raise

# ========== مدیریت وضعیت کاربران ==========
class UserManager:
    """مدیریت وضعیت کاربران در حافظه"""
    
    _instance = None
    _user_states: Dict[int, str] = {}
    _user_data: Dict[int, Dict] = {}
    _admin_states: Dict[int, str] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def set_state(cls, user_id: int, state: str):
        """تنظیم وضعیت کاربر"""
        cls._user_states[user_id] = state
        logger.debug(f"✅ وضعیت کاربر {user_id} به '{state}' تغییر کرد")
    
    @classmethod
    def get_state(cls, user_id: int) -> Optional[str]:
        """دریافت وضعیت کاربر"""
        return cls._user_states.get(user_id)
    
    @classmethod
    def clear_state(cls, user_id: int):
        """پاک کردن وضعیت کاربر"""
        if user_id in cls._user_states:
            del cls._user_states[user_id]
            logger.debug(f"✅ وضعیت کاربر {user_id} پاک شد")
    
    @classmethod
    def set_admin_state(cls, admin_id: int, state: str):
        """تنظیم وضعیت ادمین"""
        cls._admin_states[admin_id] = state
        logger.debug(f"✅ وضعیت ادمین {admin_id} به '{state}' تغییر کرد")
    
    @classmethod
    def get_admin_state(cls, admin_id: int) -> Optional[str]:
        """دریافت وضعیت ادمین"""
        return cls._admin_states.get(admin_id)
    
    @classmethod
    def clear_admin_state(cls, admin_id: int):
        """پاک کردن وضعیت ادمین"""
        if admin_id in cls._admin_states:
            del cls._admin_states[admin_id]
    
    @classmethod
    def set_user_data(cls, user_id: int, key: str, value: Any):
        """ذخیره داده کاربر"""
        if user_id not in cls._user_data:
            cls._user_data[user_id] = {}
        cls._user_data[user_id][key] = value
    
    @classmethod
    def get_user_data(cls, user_id: int, key: str, default=None) -> Any:
        """دریافت داده کاربر"""
        return cls._user_data.get(user_id, {}).get(key, default)
    
    @classmethod
    def clear_user_data(cls, user_id: int):
        """پاک کردن داده‌های کاربر"""
        if user_id in cls._user_data:
            del cls._user_data[user_id]

# ========== سرویس کاربران ==========
class UserService:
    """سرویس مدیریت کاربران"""
    
    @staticmethod
    async def register_user(
        user_id: int, 
        username: str, 
        first_name: str = "", 
        last_name: str = "", 
        invited_by: int = None,
        language_code: str = "fa"
    ) -> bool:
        """ثبت کاربر جدید در سیستم"""
        try:
            # بررسی وجود کاربر
            existing = await Database.execute(
                "SELECT user_id FROM users WHERE user_id = %s",
                (user_id,), fetchone=True
            )
            
            if existing:
                # آپدیت اطلاعات کاربر موجود
                await Database.execute(
                    """
                    UPDATE users SET 
                        username = %s, 
                        first_name = %s, 
                        last_name = %s,
                        last_active = CURRENT_TIMESTAMP,
                        language_code = %s
                    WHERE user_id = %s
                    """,
                    (username, first_name, last_name, language_code, user_id)
                )
                logger.info(f"📝 کاربر موجود آپدیت شد: {user_id}")
                return False
            
            # ثبت کاربر جدید
            await Database.execute(
                """
                INSERT INTO users 
                (user_id, username, first_name, last_name, invited_by, language_code)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, username, first_name, last_name, invited_by, language_code)
            )
            
            # افزایش تعداد دعوت شده‌های دعوت‌کننده
            if invited_by:
                await Database.execute(
                    "UPDATE users SET total_invited = total_invited + 1 WHERE user_id = %s",
                    (invited_by,)
                )
            
            logger.info(f"🎉 کاربر جدید ثبت شد: {user_id} (@{username})")
            
            # لاگ فعالیت
            await LogService.log_activity(
                user_id, 
                "user_registered", 
                f"Invited by: {invited_by}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در ثبت کاربر {user_id}: {e}")
            await LogService.log_error(user_id, "user_registration", str(e))
            raise
    
    @staticmethod
    async def get_user(user_id: int) -> Optional[Dict]:
        """دریافت اطلاعات کاربر"""
        try:
            user = await Database.execute(
                """
                SELECT user_id, username, first_name, last_name, balance,
                       invited_by, phone, created_at, last_active, is_agent,
                       is_active, total_invited, total_spent, language_code
                FROM users WHERE user_id = %s
                """,
                (user_id,), fetchone=True
            )
            return dict(user) if user else None
        except Exception as e:
            logger.error(f"❌ خطا در دریافت اطلاعات کاربر {user_id}: {e}")
            return None
    
    @staticmethod
    async def update_balance(user_id: int, amount: int, reason: str = "") -> bool:
        """به‌روزرسانی موجودی کاربر"""
        try:
            if amount > 0:
                await Database.execute(
                    """
                    UPDATE users SET 
                        balance = COALESCE(balance, 0) + %s,
                        total_spent = total_spent + %s
                    WHERE user_id = %s
                    """,
                    (amount, amount, user_id)
                )
                logger.info(f"💰 {amount} تومان به موجودی کاربر {user_id} اضافه شد")
            else:
                await Database.execute(
                    "UPDATE users SET balance = COALESCE(balance, 0) + %s WHERE user_id = %s",
                    (amount, user_id)
                )
                logger.info(f"💰 {abs(amount)} تومان از موجودی کاربر {user_id} کسر شد")
            
            # لاگ فعالیت
            await LogService.log_activity(
                user_id,
                "balance_updated",
                f"Amount: {amount}, Reason: {reason}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در به‌روزرسانی موجودی کاربر {user_id}: {e}")
            return False
    
    @staticmethod
    async def get_balance(user_id: int) -> int:
        """دریافت موجودی کاربر"""
        try:
            result = await Database.execute(
                "SELECT balance FROM users WHERE user_id = %s",
                (user_id,), fetchone=True
            )
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"❌ خطا در دریافت موجودی کاربر {user_id}: {e}")
            return 0
    
    @staticmethod
    async def set_as_agent(user_id: int) -> bool:
        """تنظیم کاربر به عنوان نماینده"""
        try:
            await Database.execute(
                "UPDATE users SET is_agent = TRUE WHERE user_id = %s",
                (user_id,)
            )
            logger.info(f"👨‍💼 کاربر {user_id} به نماینده ارتقا یافت")
            
            await LogService.log_activity(user_id, "became_agent", "")
            return True
        except Exception as e:
            logger.error(f"❌ خطا در تنظیم نماینده برای کاربر {user_id}: {e}")
            return False
    
    @staticmethod
    async def is_agent(user_id: int) -> bool:
        """بررسی نماینده بودن کاربر"""
        try:
            result = await Database.execute(
                "SELECT is_agent FROM users WHERE user_id = %s",
                (user_id,), fetchone=True
            )
            return result[0] if result else False
        except Exception as e:
            logger.error(f"❌ خطا در بررسی نماینده بودن کاربر {user_id}: {e}")
            return False
    
    @staticmethod
    async def update_last_active(user_id: int):
        """به‌روزرسانی زمان آخرین فعالیت"""
        try:
            await Database.execute(
                "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = %s",
                (user_id,)
            )
        except Exception as e:
            logger.error(f"❌ خطا در به‌روزرسانی فعالیت کاربر {user_id}: {e}")

# ========== سرویس پرداخت‌ها ==========
class PaymentService:
    """سرویس مدیریت پرداخت‌ها"""
    
    @staticmethod
    async def create_payment(
        user_id: int,
        amount: int,
        payment_type: str,
        payment_method: str,
        description: str = "",
        coupon_code: str = None
    ) -> Optional[int]:
        """ایجاد رکورد پرداخت جدید"""
        try:
            # محاسبه مبلغ با در نظر گرفتن کد تخفیف
            final_amount = amount
            discount_info = ""
            
            if coupon_code:
                discount = await CouponService.validate_coupon(coupon_code, user_id)
                if discount and discount["valid"]:
                    discount_percent = discount["discount_percent"]
                    discount_amount = int(amount * discount_percent / 100)
                    final_amount = amount - discount_amount
                    discount_info = f"کد تخفیف: {coupon_code} ({discount_percent}%)"
            
            # ایجاد پرداخت
            result = await Database.execute(
                """
                INSERT INTO payments 
                (user_id, amount, type, payment_method, description)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, final_amount, payment_type, payment_method, description),
                returning=True
            )
            
            payment_id = result
            
            # استفاده از کد تخفیف
            if coupon_code and discount_info:
                await CouponService.use_coupon(coupon_code, user_id, payment_id)
            
            logger.info(f"💰 پرداخت جدید ایجاد شد: ID={payment_id}, User={user_id}, Amount={final_amount}")
            
            await LogService.log_activity(
                user_id,
                "payment_created",
                f"Payment ID: {payment_id}, Amount: {final_amount}, Type: {payment_type}"
            )
            
            return payment_id
            
        except Exception as e:
            logger.error(f"❌ خطا در ایجاد پرداخت برای کاربر {user_id}: {e}")
            await LogService.log_error(user_id, "payment_creation", str(e))
            return None
    
    @staticmethod
    async def update_payment_status(
        payment_id: int, 
        status: str, 
        admin_id: int = None,
        note: str = ""
    ) -> bool:
        """به‌روزرسانی وضعیت پرداخت"""
        try:
            # دریافت اطلاعات پرداخت
            payment = await Database.execute(
                """
                SELECT user_id, amount, type FROM payments 
                WHERE id = %s
                """,
                (payment_id,), fetchone=True
            )
            
            if not payment:
                logger.error(f"❌ پرداخت {payment_id} یافت نشد")
                return False
            
            user_id, amount, payment_type = payment
            
            # آپدیت وضعیت
            if status == "approved":
                await Database.execute(
                    """
                    UPDATE payments SET 
                        status = %s,
                        approved_at = CURRENT_TIMESTAMP,
                        approved_by = %s,
                        admin_note = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (status, admin_id, note, payment_id)
                )
                
                # اگر نوع پرداخت افزایش موجودی است
                if payment_type == "increase_balance":
                    await UserService.update_balance(user_id, amount, "افزایش موجودی")
                
                logger.info(f"✅ پرداخت {payment_id} تایید شد")
                
            elif status == "rejected":
                await Database.execute(
                    """
                    UPDATE payments SET 
                        status = %s,
                        admin_note = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (status, note, payment_id)
                )
                logger.info(f"❌ پرداخت {payment_id} رد شد")
            
            else:
                await Database.execute(
                    """
                    UPDATE payments SET 
                        status = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (status, payment_id)
                )
            
            # لاگ فعالیت
            await LogService.log_activity(
                user_id if admin_id else admin_id,
                "payment_status_updated",
                f"Payment ID: {payment_id}, Status: {status}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در به‌روزرسانی وضعیت پرداخت {payment_id}: {e}")
            return False
    
    @staticmethod
    async def get_payment(payment_id: int) -> Optional[Dict]:
        """دریافت اطلاعات پرداخت"""
        try:
            payment = await Database.execute(
                """
                SELECT p.*, u.username, u.first_name, u.last_name
                FROM payments p
                LEFT JOIN users u ON p.user_id = u.user_id
                WHERE p.id = %s
                """,
                (payment_id,), fetchone=True
            )
            return dict(payment) if payment else None
        except Exception as e:
            logger.error(f"❌ خطا در دریافت اطلاعات پرداخت {payment_id}: {e}")
            return None
    
    @staticmethod
    async def save_receipt(payment_id: int, file_id: str) -> bool:
        """ذخیره فایل فیش پرداخت"""
        try:
            await Database.execute(
                "UPDATE payments SET receipt_file_id = %s WHERE id = %s",
                (file_id, payment_id)
            )
            logger.info(f"📄 فیش پرداخت برای پرداخت {payment_id} ذخیره شد")
            return True
        except Exception as e:
            logger.error(f"❌ خطا در ذخیره فیش پرداخت {payment_id}: {e}")
            return False

# ========== سرویس اشتراک‌ها ==========
class SubscriptionService:
    """سرویس مدیریت اشتراک‌ها"""
    
    # نقشه قیمت‌ها
    PRICE_MAP = {
        # قیمت‌های معمولی
        "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": 90000,
        "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": 250000,
        "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": 450000,
        
        # قیمت‌های نمایندگان
        "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 70000,
        "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 210000,
        "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 380000,
    }
    
    # نقشه مدت زمان
    DURATION_MAP = {
        "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره": 30,
        "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره": 90,
        "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره": 180,
        "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 30,
        "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 90,
        "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره": 180,
    }
    
    @staticmethod
    def get_price(plan_name: str, is_agent: bool = False) -> int:
        """دریافت قیمت پلن"""
        return SubscriptionService.PRICE_MAP.get(plan_name, 0)
    
    @staticmethod
    def get_duration(plan_name: str) -> int:
        """دریافت مدت زمان پلن به روز"""
        return SubscriptionService.DURATION_MAP.get(plan_name, 30)
    
    @staticmethod
    async def create_subscription(
        user_id: int,
        payment_id: int,
        plan_name: str
    ) -> Optional[int]:
        """ایجاد اشتراک جدید"""
        try:
            duration_days = SubscriptionService.get_duration(plan_name)
            start_date = datetime.now()
            end_date = start_date + timedelta(days=duration_days)
            
            result = await Database.execute(
                """
                INSERT INTO subscriptions 
                (user_id, payment_id, plan, duration_days, start_date, end_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, payment_id, plan_name, duration_days, start_date, end_date),
                returning=True
            )
            
            subscription_id = result
            
            logger.info(f"📦 اشتراک جدید ایجاد شد: ID={subscription_id}, User={user_id}, Plan={plan_name}")
            
            await LogService.log_activity(
                user_id,
                "subscription_created",
                f"Subscription ID: {subscription_id}, Plan: {plan_name}"
            )
            
            return subscription_id
            
        except Exception as e:
            logger.error(f"❌ خطا در ایجاد اشتراک برای کاربر {user_id}: {e}")
            return None
    
    @staticmethod
    async def update_config(
        subscription_id: int,
        config_text: str,
        config_file_id: str = None
    ) -> bool:
        """به‌روزرسانی کانفیگ اشتراک"""
        try:
            await Database.execute(
                """
                UPDATE subscriptions SET 
                    config = %s,
                    config_file_id = %s,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (config_text, config_file_id, subscription_id)
            )
            
            logger.info(f"✅ کانفیگ اشتراک {subscription_id} به‌روزرسانی شد")
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در به‌روزرسانی کانفیگ اشتراک {subscription_id}: {e}")
            return False
    
    @staticmethod
    async def get_user_subscriptions(user_id: int) -> List[Dict]:
        """دریافت اشتراک‌های کاربر"""
        try:
            subscriptions = await Database.execute(
                """
                SELECT s.*, p.amount, p.status as payment_status
                FROM subscriptions s
                LEFT JOIN payments p ON s.payment_id = p.id
                WHERE s.user_id = %s
                ORDER BY s.created_at DESC
                """,
                (user_id,), fetch=True
            )
            
            result = []
            current_time = datetime.now()
            
            for sub in subscriptions:
                sub_dict = dict(sub)
                
                # بررسی انقضا
                if sub_dict["status"] == "active" and sub_dict["end_date"]:
                    if current_time > sub_dict["end_date"]:
                        await Database.execute(
                            "UPDATE subscriptions SET status = 'expired' WHERE id = %s",
                            (sub_dict["id"],)
                        )
                        sub_dict["status"] = "expired"
                
                result.append(sub_dict)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ خطا در دریافت اشتراک‌های کاربر {user_id}: {e}")
            return []
    
    @staticmethod
    async def get_subscription(subscription_id: int) -> Optional[Dict]:
        """دریافت اطلاعات اشتراک"""
        try:
            subscription = await Database.execute(
                """
                SELECT s.*, u.username, u.user_id
                FROM subscriptions s
                LEFT JOIN users u ON s.user_id = u.user_id
                WHERE s.id = %s
                """,
                (subscription_id,), fetchone=True
            )
            return dict(subscription) if subscription else None
        except Exception as e:
            logger.error(f"❌ خطا در دریافت اشتراک {subscription_id}: {e}")
            return None

# ========== سرویس کدهای تخفیف ==========
class CouponService:
    """سرویس مدیریت کدهای تخفیف"""
    
    @staticmethod
    def generate_code(length: int = 8) -> str:
        """تولید کد تخفیف"""
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choices(chars, k=length))
    
    @staticmethod
    async def create_coupon(
        discount_percent: int,
        created_by: int,
        user_id: int = None,
        max_uses: int = 1
    ) -> Optional[str]:
        """ایجاد کد تخفیف جدید"""
        try:
            code = CouponService.generate_code()
            
            await Database.execute(
                """
                INSERT INTO coupons 
                (code, discount_percent, created_by, user_id, max_uses)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (code, discount_percent, created_by, user_id, max_uses)
            )
            
            logger.info(f"🎫 کد تخفیف ایجاد شد: {code} ({discount_percent}%)")
            
            await LogService.log_activity(
                created_by,
                "coupon_created",
                f"Code: {code}, Discount: {discount_percent}%"
            )
            
            return code
            
        except Exception as e:
            logger.error(f"❌ خطا در ایجاد کد تخفیف: {e}")
            return None
    
    @staticmethod
    async def validate_coupon(code: str, user_id: int) -> Dict:
        """اعتبارسنجی کد تخفیف"""
        try:
            coupon = await Database.execute(
                """
                SELECT discount_percent, user_id, is_used, 
                       expiry_date, max_uses, current_uses
                FROM coupons WHERE code = %s
                """,
                (code,), fetchone=True
            )
            
            if not coupon:
                return {"valid": False, "message": "کد تخفیف نامعتبر است"}
            
            discount_percent, coupon_user_id, is_used, expiry_date, max_uses, current_uses = coupon
            
            # بررسی استفاده شده
            if is_used or current_uses >= max_uses:
                return {"valid": False, "message": "این کد تخفیف قبلاً استفاده شده است"}
            
            # بررسی تاریخ انقضا
            if datetime.now() > expiry_date:
                return {"valid": False, "message": "این کد تخفیف منقضی شده است"}
            
            # بررسی اختصاصی بودن کد
            if coupon_user_id and coupon_user_id != user_id:
                return {"valid": False, "message": "این کد تخفیف برای شما نیست"}
            
            # بررسی نماینده بودن
            is_agent = await UserService.is_agent(user_id)
            if is_agent:
                return {"valid": False, "message": "نمایندگان نمی‌توانند از کد تخفیف استفاده کنند"}
            
            return {
                "valid": True,
                "discount_percent": discount_percent,
                "message": "کد تخفیف معتبر است"
            }
            
        except Exception as e:
            logger.error(f"❌ خطا در اعتبارسنجی کد تخفیف {code}: {e}")
            return {"valid": False, "message": "خطا در بررسی کد تخفیف"}
    
    @staticmethod
    async def use_coupon(code: str, user_id: int, payment_id: int = None) -> bool:
        """استفاده از کد تخفیف"""
        try:
            await Database.execute(
                """
                UPDATE coupons SET 
                    is_used = TRUE,
                    used_at = CURRENT_TIMESTAMP,
                    used_by = %s,
                    current_uses = current_uses + 1
                WHERE code = %s
                """,
                (user_id, code)
            )
            
            logger.info(f"🎫 کد تخفیف {code} توسط کاربر {user_id} استفاده شد")
            
            await LogService.log_activity(
                user_id,
                "coupon_used",
                f"Code: {code}, Payment ID: {payment_id}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در استفاده از کد تخفیف {code}: {e}")
            return False

# ========== سرویس لاگ و گزارش‌گیری ==========
class LogService:
    """سرویس لاگ و گزارش‌گیری"""
    
    @staticmethod
    async def log_activity(user_id: int, action: str, details: str = ""):
        """ثبت فعالیت کاربر"""
        try:
            await Database.execute(
                """
                INSERT INTO activity_log (user_id, action, details)
                VALUES (%s, %s, %s)
                """,
                (user_id, action, details)
            )
        except Exception as e:
            logger.error(f"❌ خطا در ثبت فعالیت کاربر {user_id}: {e}")
    
    @staticmethod
    async def log_error(
        user_id: int, 
        error_type: str, 
        error_message: str,
        stack_trace: str = "",
        additional_info: str = ""
    ):
        """ثبت خطا"""
        try:
            await Database.execute(
                """
                INSERT INTO error_log 
                (user_id, error_type, error_message, stack_trace, additional_info)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, error_type, error_message, stack_trace, additional_info)
            )
        except Exception as e:
            logger.error(f"❌ خطا در ثبت خطا برای کاربر {user_id}: {e}")
    
    @staticmethod
    async def get_stats() -> Dict:
        """دریافت آمار سیستم"""
        try:
            # تعداد کاربران
            total_users = await Database.execute(
                "SELECT COUNT(*) FROM users", fetchone=True
            )
            
            # کاربران فعال امروز
            active_today = await Database.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM activity_log 
                WHERE created_at >= CURRENT_DATE
                """, fetchone=True
            )
            
            # اشتراک‌های فعال
            active_subs = await Database.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'",
                fetchone=True
            )
            
            # درآمد امروز
            income_today = await Database.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM payments 
                WHERE status = 'approved' AND created_at >= CURRENT_DATE
                """, fetchone=True
            )
            
            # درآمد این ماه
            income_month = await Database.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM payments 
                WHERE status = 'approved' AND created_at >= DATE_TRUNC('month', CURRENT_DATE)
                """, fetchone=True
            )
            
            # کل درآمد
            total_income = await Database.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved'",
                fetchone=True
            )
            
            # پرداخت‌های در انتظار
            pending_payments = await Database.execute(
                "SELECT COUNT(*) FROM payments WHERE status = 'pending'",
                fetchone=True
            )
            
            # نمایندگان
            agents_count = await Database.execute(
                "SELECT COUNT(*) FROM users WHERE is_agent = TRUE",
                fetchone=True
            )
            
            return {
                "total_users": total_users[0] if total_users else 0,
                "active_today": active_today[0] if active_today else 0,
                "active_subscriptions": active_subs[0] if active_subs else 0,
                "income_today": income_today[0] if income_today else 0,
                "income_month": income_month[0] if income_month else 0,
                "total_income": total_income[0] if total_income else 0,
                "pending_payments": pending_payments[0] if pending_payments else 0,
                "agents_count": agents_count[0] if agents_count else 0,
                "last_updated": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ خطا در دریافت آمار: {e}")
            return {}

# ========== سرویس اطلاع‌رسانی ==========
class NotificationService:
    """سرویس اطلاع‌رسانی"""
    
    @staticmethod
    async def send_to_all_users(
        message: str,
        sender_id: int,
        exclude_agents: bool = False
    ) -> Dict:
        """ارسال پیام به تمام کاربران"""
        try:
            query = "SELECT user_id FROM users WHERE is_active = TRUE"
            if exclude_agents:
                query += " AND is_agent = FALSE"
            
            users = await Database.execute(query, fetch=True)
            
            if not users:
                return {"sent": 0, "failed": 0, "total": 0}
            
            sent = 0
            failed = 0
            
            for user in users:
                try:
                    # اینجا باید بات تلگرام ارسال کند
                    # به صورت موقت فقط لاگ می‌کنیم
                    logger.info(f"📢 ارسال به کاربر {user[0]}: {message[:50]}...")
                    sent += 1
                    
                    # ذخیره در دیتابیس
                    await Database.execute(
                        """
                        INSERT INTO notifications 
                        (sender_id, receiver_id, notification_type, message)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (sender_id, user[0], "broadcast", message)
                    )
                    
                except Exception as e:
                    logger.error(f"❌ خطا در ارسال به کاربر {user[0]}: {e}")
                    failed += 1
            
            return {
                "sent": sent,
                "failed": failed,
                "total": len(users)
            }
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال گروهی: {e}")
            return {"sent": 0, "failed": 0, "total": 0}
    
    @staticmethod
    async def send_to_agents(message: str, sender_id: int) -> Dict:
        """ارسال پیام به نمایندگان"""
        try:
            users = await Database.execute(
                "SELECT user_id FROM users WHERE is_agent = TRUE AND is_active = TRUE",
                fetch=True
            )
            
            if not users:
                return {"sent": 0, "failed": 0, "total": 0}
            
            sent = 0
            failed = 0
            
            for user in users:
                try:
                    logger.info(f"📢 ارسال به نماینده {user[0]}: {message[:50]}...")
                    sent += 1
                    
                    await Database.execute(
                        """
                        INSERT INTO notifications 
                        (sender_id, receiver_id, notification_type, message)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (sender_id, user[0], "agents_broadcast", message)
                    )
                    
                except Exception as e:
                    logger.error(f"❌ خطا در ارسال به نماینده {user[0]}: {e}")
                    failed += 1
            
            return {
                "sent": sent,
                "failed": failed,
                "total": len(users)
            }
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال به نمایندگان: {e}")
            return {"sent": 0, "failed": 0, "total": 0}
    
    @staticmethod
    async def send_to_user(user_id: int, message: str, sender_id: int) -> bool:
        """ارسال پیام به کاربر خاص"""
        try:
            logger.info(f"📢 ارسال به کاربر {user_id}: {message[:50]}...")
            
            await Database.execute(
                """
                INSERT INTO notifications 
                (sender_id, receiver_id, notification_type, message)
                VALUES (%s, %s, %s, %s)
                """,
                (sender_id, user_id, "direct", message)
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال به کاربر {user_id}: {e}")
            return False

# ========== مدیریت کانال ==========
class ChannelManager:
    """مدیریت کانال تلگرام"""
    
    @staticmethod
    async def check_membership(bot, user_id: int, channel_username: str = CHANNEL_USERNAME) -> bool:
        """بررسی عضویت کاربر در کانال"""
        try:
            if not channel_username.startswith('@'):
                channel_username = '@' + channel_username
            
            member = await bot.get_chat_member(channel_username, user_id)
            is_member = member.status in ["member", "administrator", "creator"]
            
            logger.debug(f"👥 بررسی عضویت کاربر {user_id} در کانال {channel_username}: {is_member}")
            return is_member
            
        except Exception as e:
            logger.error(f"❌ خطا در بررسی عضویت کاربر {user_id}: {e}")
            return False
    
    @staticmethod
    def get_channel_button() -> InlineKeyboardMarkup:
        """دریافت دکمه عضویت در کانال"""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📢 عضویت در کانال", 
                url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}"
            )
        ]])

# ========== کیبوردها ==========
class Keyboards:
    """کلاس تولید کیبوردها"""
    
    @staticmethod
    def get_main_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد اصلی"""
        keyboard = [
            [KeyboardButton("💰 موجودی"), KeyboardButton("💳 خرید اشتراک")],
            [KeyboardButton("🎁 اشتراک تست رایگان"), KeyboardButton("☎️ پشتیبانی")],
            [KeyboardButton("💵 اعتبار رایگان"), KeyboardButton("📂 اشتراک‌های من")],
            [KeyboardButton("💡 راهنمای اتصال"), KeyboardButton("🧑‍💼 درخواست نمایندگی")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    @staticmethod
    def get_balance_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد موجودی"""
        keyboard = [
            [KeyboardButton("📊 نمایش موجودی"), KeyboardButton("💸 افزایش موجودی")],
            [KeyboardButton("📈 تاریخچه تراکنش‌ها")],
            [KeyboardButton("⬅️ بازگشت به منو")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def get_subscription_keyboard(is_agent: bool = False) -> ReplyKeyboardMarkup:
        """کیبورد خرید اشتراک"""
        if is_agent:
            keyboard = [
                [KeyboardButton("🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("⬅️ بازگشت به منو")]
            ]
        else:
            keyboard = [
                [KeyboardButton("🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره")],
                [KeyboardButton("⬅️ بازگشت به منو")]
            ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def get_payment_method_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد روش پرداخت"""
        keyboard = [
            [KeyboardButton("🏦 کارت به کارت")],
            [KeyboardButton("💎 پرداخت با ترون")],
            [KeyboardButton("💰 پرداخت با موجودی")],
            [KeyboardButton("⬅️ بازگشت به منو")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def get_back_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد بازگشت"""
        return ReplyKeyboardMarkup([[KeyboardButton("⬅️ بازگشت به منو")]], resize_keyboard=True)
    
    @staticmethod
    def get_connection_guide_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد راهنمای اتصال"""
        keyboard = [
            [KeyboardButton("📱 اندروید")],
            [KeyboardButton("🍎 آیفون/مک")],
            [KeyboardButton("🪟 ویندوز")],
            [KeyboardButton("🐧 لینوکس")],
            [KeyboardButton("⬅️ بازگشت به منو")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def get_admin_main_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد اصلی ادمین"""
        keyboard = [
            [KeyboardButton("📊 آمار ربات"), KeyboardButton("👥 مدیریت کاربران")],
            [KeyboardButton("💰 مدیریت پرداخت‌ها"), KeyboardButton("🎫 کدهای تخفیف")],
            [KeyboardButton("📢 اطلاع‌رسانی"), KeyboardButton("⚙️ تنظیمات")],
            [KeyboardButton("💾 پشتیبان‌گیری"), KeyboardButton("🔄 بازیابی")],
            [KeyboardButton("⬅️ منوی کاربری")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def get_yes_no_keyboard() -> ReplyKeyboardMarkup:
        """کیبورد تایید/لغو"""
        keyboard = [
            [KeyboardButton("✅ تایید"), KeyboardButton("❌ انصراف")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ========== ساخت بات ==========
# ایجاد اپلیکیشن بات
application = ApplicationBuilder().token(TOKEN).build()

# ========== دستورات اصلی بات ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع /start"""
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    logger.info(f"🚀 کاربر {user_id} (@{username}) دستور /start را اجرا کرد")
    
    # بررسی پارامتر دعوت
    invited_by = None
    if context.args and len(context.args) > 0:
        try:
            invited_by = int(context.args[0])
            if invited_by == user_id:
                invited_by = None
            else:
                logger.info(f"🎯 کاربر {user_id} توسط {invited_by} دعوت شده است")
        except:
            invited_by = None
    
    # بررسی عضویت در کانال
    is_member = await ChannelManager.check_membership(
        context.bot, user_id, CHANNEL_USERNAME
    )
    
    if not is_member:
        await update.message.reply_text(
            "❌ **برای استفاده از ربات، ابتدا در کانال ما عضو شوید:**\n\n"
            f"کانال: {CHANNEL_USERNAME}\n\n"
            "✅ پس از عضویت، مجدد /start را بزنید.",
            reply_markup=ChannelManager.get_channel_button(),
            parse_mode="Markdown"
        )
        return
    
    # ثبت/آپدیت کاربر
    is_new = await UserService.register_user(
        user_id, username, first_name, last_name, invited_by
    )
    
    # به‌روزرسانی آخرین فعالیت
    await UserService.update_last_active(user_id)
    
    # پیام خوش‌آمد
    if is_new:
        welcome_message = (
            "🎉 **به فروشگاه تیز VPN خوش آمدید!** 🚀\n\n"
            "✅ عضویت شما با موفقیت ثبت شد!\n"
            "💎 از خدمات با کیفیت و پرسرعت ما لذت ببرید.\n\n"
            "📱 برای شروع، یکی از گزینه‌های زیر را انتخاب کنید:"
        )
        
        # اطلاع به ادمین
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🎉 **کاربر جدید ثبت نام کرد!**\n\n"
                f"🆔 ID: `{user_id}`\n"
                f"👤 نام: {first_name} {last_name}\n"
                f"📛 یوزرنیم: @{username}\n"
                f"🎯 دعوت‌کننده: {invited_by or 'مستقیم'}\n"
                f"🕒 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"❌ خطا در اطلاع به ادمین: {e}")
    else:
        welcome_message = (
            "👋 **دوباره سلام!** 🤗\n\n"
            "✅ خوش آمدید بازگشت.\n"
            "💎 از خدمات با کیفیت و پرسرعت ما لذت ببرید.\n\n"
            "📱 برای ادامه، یکی از گزینه‌های زیر را انتخاب کنید:"
        )
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )
    
    # پاک کردن وضعیت‌های قبلی
    UserManager.clear_state(user_id)
    UserManager.clear_user_data(user_id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های متنی"""
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip() if update.message.text else ""
    
    logger.debug(f"📨 پیام از کاربر {user_id}: '{text[:50]}...'")
    
    # به‌روزرسانی آخرین فعالیت
    await UserService.update_last_active(user_id)
    
    # اگر کاربر ادمین است
    if user_id == ADMIN_ID:
        await handle_admin_message(update, context)
        return
    
    # مدیریت دستورات معمولی کاربران
    await handle_user_message(update, context)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های کاربران عادی"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # بررسی وضعیت فعلی کاربر
    current_state = UserManager.get_state(user_id)
    
    # بازگشت به منو
    if text in ["⬅️ بازگشت به منو", "بازگشت", "منو"]:
        await update.message.reply_text(
            "🏠 **منوی اصلی:**",
            reply_markup=Keyboards.get_main_keyboard(),
            parse_mode="Markdown"
        )
        UserManager.clear_state(user_id)
        return
    
    # مدیریت وضعیت‌های مختلف
    if current_state:
        await handle_user_state(update, context, current_state)
        return
    
    # دستورات اصلی
    if text == "💰 موجودی":
        await show_balance_menu(update, context)
    
    elif text == "💳 خرید اشتراک":
        await show_subscription_plans(update, context)
    
    elif text == "🎁 اشتراک تست رایگان":
        await show_free_trial(update, context)
    
    elif text == "☎️ پشتیبانی":
        await show_support(update, context)
    
    elif text == "💵 اعتبار رایگان":
        await show_invite_reward(update, context)
    
    elif text == "📂 اشتراک‌های من":
        await show_user_subscriptions(update, context)
    
    elif text == "💡 راهنمای اتصال":
        await show_connection_guide(update, context)
    
    elif text == "🧑‍💼 درخواست نمایندگی":
        await show_agency_request(update, context)
    
    else:
        await update.message.reply_text(
            "❌ دستور نامعتبر است. لطفاً از دکمه‌های زیر استفاده کنید:",
            reply_markup=Keyboards.get_main_keyboard()
        )

async def handle_user_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str):
    """مدیریت وضعیت‌های کاربر"""
    user_id = update.effective_user.id
    text = update.message.text
    
    logger.debug(f"🔄 مدیریت وضعیت کاربر {user_id}: {state}")
    
    # وضعیت‌های مختلف
    if state.startswith("awaiting_deposit_amount"):
        await handle_deposit_amount(update, context, text)
    
    elif state.startswith("awaiting_deposit_receipt_"):
        payment_id = int(state.split("_")[-1])
        await handle_deposit_receipt(update, context, payment_id)
    
    elif state.startswith("awaiting_subscription_receipt_"):
        payment_id = int(state.split("_")[-1])
        await handle_subscription_receipt(update, context, payment_id)
    
    elif state.startswith("awaiting_coupon_code_"):
        await handle_coupon_input(update, context, state, text)
    
    elif state.startswith("awaiting_payment_method_"):
        await handle_payment_method(update, context, state, text)
    
    elif state.startswith("awaiting_agency_receipt_"):
        payment_id = int(state.split("_")[-1])
        await handle_agency_receipt(update, context, payment_id)
    
    else:
        await update.message.reply_text(
            "⚠️ وضعیت نامعتبر. لطفاً مجدد از منو انتخاب کنید:",
            reply_markup=Keyboards.get_main_keyboard()
        )
        UserManager.clear_state(user_id)

async def show_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی موجودی"""
    user_id = update.effective_user.id
    balance = await UserService.get_balance(user_id)
    
    message = (
        f"💰 **موجودی کیف پول شما:**\n\n"
        f"🔹 **مبلغ:** `{balance:,}` تومان\n\n"
        f"📊 **گزینه‌های مدیریت موجودی:**"
    )
    
    await update.message.reply_text(
        message,
        reply_markup=Keyboards.get_balance_keyboard(),
        parse_mode="Markdown"
    )

async def handle_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, amount_text: str):
    """مدیریت مبلغ واریزی"""
    user_id = update.effective_user.id
    
    try:
        amount = int(amount_text.replace(',', ''))
        
        if amount < 10000:
            await update.message.reply_text(
                "❌ حداقل مبلغ واریز ۱۰,۰۰۰ تومان است.\n"
                "لطفاً مبلغ معتبر وارد کنید:",
                reply_markup=Keyboards.get_back_keyboard()
            )
            return
        
        # ذخیره مبلغ در داده کاربر
        UserManager.set_user_data(user_id, "deposit_amount", amount)
        UserManager.set_state(user_id, "awaiting_deposit_method")
        
        await update.message.reply_text(
            f"💳 **مبلغ واریزی:** `{amount:,}` تومان\n\n"
            "📌 **لطفاً روش پرداخت را انتخاب کنید:**",
            reply_markup=Keyboards.get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ لطفاً یک عدد معتبر وارد کنید (مثال: 50000):",
            reply_markup=Keyboards.get_back_keyboard()
        )

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پلن‌های اشتراک"""
    user_id = update.effective_user.id
    
    # بررسی نماینده بودن
    is_agent = await UserService.is_agent(user_id)
    
    await update.message.reply_text(
        "📦 **پلن‌های اشتراک VPN**\n\n"
        "✅ **تمام پلن‌ها شامل:**\n"
        "• اتصال نامحدود حجم و سرعت\n"
        "• پشتیبانی از ۲ دستگاه همزمان\n"
        "• پشتیبانی ۲۴/۷\n\n"
        "📌 **لطفاً یک پلن را انتخاب کنید:**",
        reply_markup=Keyboards.get_subscription_keyboard(is_agent),
        parse_mode="Markdown"
    )

async def handle_subscription_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_name: str):
    """مدیریت انتخاب پلن اشتراک"""
    user_id = update.effective_user.id
    
    # بررسی نماینده بودن
    is_agent = await UserService.is_agent(user_id)
    
    # دریافت قیمت
    price = SubscriptionService.get_price(plan_name, is_agent)
    
    if price == 0:
        await update.message.reply_text(
            "❌ خطا در دریافت قیمت پلن. لطفاً مجدد تلاش کنید.",
            reply_markup=Keyboards.get_main_keyboard()
        )
        return
    
    # ذخیره اطلاعات پلن
    UserManager.set_user_data(user_id, "selected_plan", plan_name)
    UserManager.set_user_data(user_id, "plan_price", price)
    
    # اگر نماینده است، مستقیم به روش پرداخت برو
    if is_agent:
        UserManager.set_state(user_id, f"awaiting_payment_method_{price}_{plan_name}")
        await update.message.reply_text(
            f"💳 **پلن انتخاب شده:** {plan_name}\n"
            f"💰 **مبلغ قابل پرداخت:** `{price:,}` تومان\n\n"
            "📌 **لطفاً روش پرداخت را انتخاب کنید:**",
            reply_markup=Keyboards.get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
    else:
        # از کاربر کد تخفیف بگیر
        UserManager.set_state(user_id, f"awaiting_coupon_code_{price}_{plan_name}")
        await update.message.reply_text(
            f"💳 **پلن انتخاب شده:** {plan_name}\n"
            f"💰 **مبلغ قابل پرداخت:** `{price:,}` تومان\n\n"
            "🎫 **اگر کد تخفیف دارید، وارد کنید:**\n"
            "📝 در غیر این صورت روی 'ادامه' کلیک کنید.",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("ادامه")],
                [KeyboardButton("⬅️ بازگشت به منو")]
            ], resize_keyboard=True),
            parse_mode="Markdown"
        )

async def handle_coupon_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, coupon_code: str):
    """مدیریت ورود کد تخفیف"""
    user_id = update.effective_user.id
    parts = state.split("_")
    original_price = int(parts[3])
    plan_name = "_".join(parts[4:])
    
    if coupon_code == "ادامه":
        # ادامه بدون کد تخفیف
        UserManager.set_state(user_id, f"awaiting_payment_method_{original_price}_{plan_name}")
        await update.message.reply_text(
            f"💳 **پلن انتخاب شده:** {plan_name}\n"
            f"💰 **مبلغ قابل پرداخت:** `{original_price:,}` تومان\n\n"
            "📌 **لطفاً روش پرداخت را انتخاب کنید:**",
            reply_markup=Keyboards.get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    # اعتبارسنجی کد تخفیف
    validation = await CouponService.validate_coupon(coupon_code, user_id)
    
    if not validation["valid"]:
        await update.message.reply_text(
            f"❌ {validation['message']}\n\n"
            "🎫 **کد تخفیف دیگری وارد کنید یا روی 'ادامه' کلیک کنید:**",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("ادامه")],
                [KeyboardButton("⬅️ بازگشت به منو")]
            ], resize_keyboard=True)
        )
        return
    
    # محاسبه مبلغ با تخفیف
    discount_percent = validation["discount_percent"]
    discount_amount = int(original_price * discount_percent / 100)
    final_price = original_price - discount_amount
    
    UserManager.set_user_data(user_id, "coupon_code", coupon_code)
    UserManager.set_state(user_id, f"awaiting_payment_method_{final_price}_{plan_name}_{coupon_code}")
    
    await update.message.reply_text(
        f"🎉 **کد تخفیف اعمال شد!**\n\n"
        f"💳 **پلن:** {plan_name}\n"
        f"💰 **قیمت اصلی:** `{original_price:,}` تومان\n"
        f"🎫 **تخفیف:** `{discount_percent}%` ({discount_amount:,} تومان)\n"
        f"💎 **مبلغ نهایی:** `{final_price:,}` تومان\n\n"
        "📌 **لطفاً روش پرداخت را انتخاب کنید:**",
        reply_markup=Keyboards.get_payment_method_keyboard(),
        parse_mode="Markdown"
    )

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, method: str):
    """مدیریت انتخاب روش پرداخت"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    # استخراج اطلاعات از وضعیت
    parts = state.split("_")
    amount = int(parts[3])
    plan_name = "_".join(parts[4:]) if len(parts) <= 5 else "_".join(parts[4:-1])
    coupon_code = parts[-1] if len(parts) > 5 else None
    
    # تعیین نوع پرداخت
    payment_type = "buy_subscription" if "plan" in state else "increase_balance"
    
    # ایجاد رکورد پرداخت
    payment_id = await PaymentService.create_payment(
        user_id, amount, payment_type, 
        "card_to_card" if method == "🏦 کارت به کارت" else "tron",
        plan_name if payment_type == "buy_subscription" else "افزایش موجودی",
        coupon_code
    )
    
    if not payment_id:
        await update.message.reply_text(
            "❌ خطا در ثبت پرداخت. لطفاً با پشتیبانی تماس بگیرید.",
            reply_markup=Keyboards.get_main_keyboard()
        )
        UserManager.clear_state(user_id)
        return
    
    # ذخیره payment_id
    UserManager.set_user_data(user_id, "current_payment_id", payment_id)
    
    if method == "🏦 کارت به کارت":
        payment_info = (
            f"🏦 **واریز کارت به کارت**\n\n"
            f"💰 **مبلغ:** `{amount:,}` تومان\n"
            f"💳 **شماره کارت:** `{BANK_CARD}`\n"
            f"🏛️ **بانک:** {BANK_NAME}\n"
            f"👤 **به نام:** {BANK_OWNER}\n\n"
            f"📌 **لطفاً پس از واریز، فیش پرداخت را ارسال کنید.**"
        )
        state_suffix = "subscription" if payment_type == "buy_subscription" else "deposit"
        UserManager.set_state(user_id, f"awaiting_{state_suffix}_receipt_{payment_id}")
        
    elif method == "💎 پرداخت با ترون":
        payment_info = (
            f"💎 **واریز از طریق TRON**\n\n"
            f"💰 **مبلغ:** `{amount:,}` تومان\n"
            f"🔗 **آدرس کیف پول:**\n`{TRON_ADDRESS}`\n\n"
            f"📌 **لطفاً پس از واریز، فیش پرداخت را ارسال کنید.**"
        )
        state_suffix = "subscription" if payment_type == "buy_subscription" else "deposit"
        UserManager.set_state(user_id, f"awaiting_{state_suffix}_receipt_{payment_id}")
        
    elif method == "💰 پرداخت با موجودی":
        # بررسی موجودی کافی
        balance = await UserService.get_balance(user_id)
        
        if balance < amount:
            await update.message.reply_text(
                f"❌ **موجودی شما کافی نیست!**\n\n"
                f"💰 **موجودی فعلی:** `{balance:,}` تومان\n"
                f"💳 **مبلغ مورد نیاز:** `{amount:,}` تومان\n"
                f"📉 **کمبود:** `{amount - balance:,}` تومان\n\n"
                "لطفاً ابتدا موجودی خود را افزایش دهید.",
                reply_markup=Keyboards.get_main_keyboard(),
                parse_mode="Markdown"
            )
            UserManager.clear_state(user_id)
            return
        
        # کسر از موجودی و تایید خودکار
        await UserService.update_balance(user_id, -amount, f"پرداخت {payment_type}")
        await PaymentService.update_payment_status(payment_id, "approved", ADMIN_ID)
        
        if payment_type == "buy_subscription":
            # ایجاد اشتراک
            subscription_id = await SubscriptionService.create_subscription(
                user_id, payment_id, plan_name
            )
            
            if subscription_id:
                await update.message.reply_text(
                    f"✅ **پرداخت با موفقیت انجام شد!**\n\n"
                    f"📦 **پلن:** {plan_name}\n"
                    f"💰 **مبلغ پرداختی:** `{amount:,}` تومان\n"
                    f"🆔 **کد خرید:** `#{payment_id}`\n\n"
                    f"📝 کانفیگ شما حداکثر تا ۱ ساعت آینده ارسال خواهد شد.",
                    reply_markup=Keyboards.get_main_keyboard(),
                    parse_mode="Markdown"
                )
                
                # اطلاع به ادمین
                await context.bot.send_message(
                    ADMIN_ID,
                    f"💰 **پرداخت با موجودی تأیید شد**\n\n"
                    f"👤 کاربر: @{username} (ID: `{user_id}`)\n"
                    f"📦 پلن: {plan_name}\n"
                    f"💳 مبلغ: `{amount:,}` تومان\n"
                    f"🆔 کد پرداخت: `#{payment_id}`\n"
                    f"📦 کد اشتراک: `#{subscription_id}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "🟣 ارسال کانفیگ",
                            callback_data=f"send_config_{payment_id}"
                        )
                    ]])
                )
            else:
                await update.message.reply_text(
                    "⚠️ خطا در ایجاد اشتراک. لطفاً با پشتیبانی تماس بگیرید.",
                    reply_markup=Keyboards.get_main_keyboard()
                )
        else:
            # افزایش موجودی
            await update.message.reply_text(
                f"✅ **موجودی شما با موفقیت افزایش یافت!**\n\n"
                f"💰 **مبلغ افزایش:** `{amount:,}` تومان\n"
                f"💎 **موجودی جدید:** `{balance - amount:,}` تومان",
                reply_markup=Keyboards.get_main_keyboard(),
                parse_mode="Markdown"
            )
        
        UserManager.clear_state(user_id)
        return
    
    await update.message.reply_text(
        payment_info,
        reply_markup=Keyboards.get_back_keyboard(),
        parse_mode="Markdown"
    )

async def handle_deposit_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """مدیریت دریافت فیش واریز"""
    user_id = update.effective_user.id
    
    # بررسی ارسال فایل
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text(
            "❌ لطفاً فیش پرداخت را به صورت عکس یا فایل ارسال کنید.",
            reply_markup=Keyboards.get_back_keyboard()
        )
        return
    
    # ذخیره فایل
    await PaymentService.save_receipt(payment_id, file_id)
    
    # اطلاع به ادمین
    payment_info = await PaymentService.get_payment(payment_id)
    
    if payment_info:
        caption = (
            f"💰 **درخواست افزایش موجودی**\n\n"
            f"👤 کاربر: @{update.effective_user.username or 'بدون یوزرنیم'}\n"
            f"🆔 ID: `{user_id}`\n"
            f"💳 مبلغ: `{payment_info['amount']:,}` تومان\n"
            f"📅 زمان: {payment_info['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"🆔 کد پرداخت: `#{payment_id}`"
        )
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{payment_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
        ]])
        
        if update.message.photo:
            await context.bot.send_photo(
                ADMIN_ID,
                photo=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await context.bot.send_document(
                ADMIN_ID,
                document=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
    
    await update.message.reply_text(
        "✅ **فیش پرداخت شما دریافت شد.**\n\n"
        "📋 درخواست شما برای مدیریت ارسال شد.\n"
        "⏳ لطفاً منتظر تأیید باشید.",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )
    
    UserManager.clear_state(user_id)

async def handle_subscription_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """مدیریت دریافت فیش خرید اشتراک"""
    user_id = update.effective_user.id
    
    # بررسی ارسال فایل
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text(
            "❌ لطفاً فیش پرداخت را به صورت عکس یا فایل ارسال کنید.",
            reply_markup=Keyboards.get_back_keyboard()
        )
        return
    
    # ذخیره فایل
    await PaymentService.save_receipt(payment_id, file_id)
    
    # دریافت اطلاعات پرداخت
    payment_info = await PaymentService.get_payment(payment_id)
    
    if payment_info:
        caption = (
            f"🛒 **درخواست خرید اشتراک**\n\n"
            f"👤 کاربر: @{update.effective_user.username or 'بدون یوزرنیم'}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📦 پلن: {payment_info['description']}\n"
            f"💳 مبلغ: `{payment_info['amount']:,}` تومان\n"
            f"📅 زمان: {payment_info['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"🆔 کد پرداخت: `#{payment_id}`"
        )
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{payment_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
        ]])
        
        if update.message.photo:
            await context.bot.send_photo(
                ADMIN_ID,
                photo=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await context.bot.send_document(
                ADMIN_ID,
                document=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
    
    await update.message.reply_text(
        "✅ **فیش پرداخت شما دریافت شد.**\n\n"
        "📋 درخواست شما برای مدیریت ارسال شد.\n"
        "⏳ لطفاً منتظر تأیید و ارسال کانفیگ باشید.",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )
    
    UserManager.clear_state(user_id)

async def show_free_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات اشتراک تست رایگان"""
    await update.message.reply_text(
        "🎁 **اشتراک تست رایگان VPN**\n\n"
        "✅ **مشخصات تست رایگان:**\n"
        "• مدت زمان: ۲۴ ساعت\n"
        "• حجم: ۲ گیگابایت\n"
        "• سرعت: کامل\n"
        "• پشتیبانی از ۱ دستگاه\n\n"
        "📞 **برای دریافت اشتراک تست، با پشتیبانی تماس بگیرید:**\n"
        f"👉 @teazadmin",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات پشتیبانی"""
    await update.message.reply_text(
        "📞 **پشتیبانی تیز VPN**\n\n"
        "✅ **راه‌های ارتباطی:**\n"
        "• پشتیبانی تلگرام: @teazadmin\n"
        "• پاسخگویی: ۲۴ ساعته\n\n"
        "🕒 **ساعات کاری:**\n"
        "• همه روزه، حتی تعطیلات\n\n"
        "💡 **برای دریافت سریع‌ترین پاسخ:**\n"
        "• مستقیم به پشتیبانی پیام دهید\n"
        "• شماره پرداخت خود را ذکر کنید",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )

async def show_invite_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات اعتبار رایگان از دعوت"""
    user_id = update.effective_user.id
    
    # ایجاد لینک دعوت
    invite_link = f"https://t.me/teazvpn_bot?start={user_id}"
    
    await update.message.reply_text(
        f"🎁 **سیستم دعوت دوستان**\n\n"
        f"💰 **پاداش هر دعوت:** ۱۰,۰۰۰ تومان\n\n"
        f"🔗 **لینک دعوت اختصاصی شما:**\n"
        f"`{invite_link}`\n\n"
        f"📋 **شرایط دریافت پاداش:**\n"
        f"۱. دوستان باید از لینک شما استفاده کنند\n"
        f"۲. دوستان باید در کانال عضو شوند\n"
        f"۳. دوستان باید حداقل یک خرید انجام دهند\n\n"
        f"✅ **پاداش بلافاصله پس از خرید دوستان به موجودی شما اضافه می‌شود.**",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )

async def show_user_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اشتراک‌های کاربر"""
    user_id = update.effective_user.id
    
    subscriptions = await SubscriptionService.get_user_subscriptions(user_id)
    
    if not subscriptions:
        await update.message.reply_text(
            "📭 **شما هنوز اشتراکی ندارید.**\n\n"
            "💡 برای خرید اشتراک جدید، گزینه '💳 خرید اشتراک' را انتخاب کنید.",
            reply_markup=Keyboards.get_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    message = "📦 **لیست اشتراک‌های شما:**\n\n"
    
    for sub in subscriptions:
        status_icon = "✅" if sub["status"] == "active" else "⏳" if sub["status"] == "pending" else "❌"
        status_text = "فعال" if sub["status"] == "active" else "در انتظار" if sub["status"] == "pending" else "منقضی"
        
        message += f"🔹 **اشتراک #{sub['id']}**\n"
        message += f"   📌 پلن: {sub['plan']}\n"
        message += f"   🏷️ وضعیت: {status_icon} {status_text}\n"
        message += f"   🆔 کد پرداخت: #{sub['payment_id']}\n"
        
        if sub["status"] == "active" and sub["end_date"]:
            remaining_days = (sub["end_date"] - datetime.now()).days
            message += f"   ⏳ زمان باقی‌مانده: {remaining_days} روز\n"
        
        if sub["config"]:
            message += f"   🔐 کانفیگ: موجود ✅\n"
        
        message += "\n"
    
    await update.message.reply_text(
        message,
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )

async def show_connection_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش راهنمای اتصال"""
    await update.message.reply_text(
        "📚 **راهنمای اتصال به VPN**\n\n"
        "✅ **نرم‌افزارهای پیشنهادی:**\n\n"
        "📱 **اندروید:** V2RayNG, Hiddify\n"
        "🍎 **آیفون/مک:** Singbox, Streisand, V2box\n"
        "🪟 **ویندوز:** V2rayN, Clash\n"
        "🐧 **لینوکس:** V2rayN, Clash\n\n"
        "💡 **پس از دریافت کانفیگ، آن را در نرم‌افزار وارد کنید.**",
        reply_markup=Keyboards.get_connection_guide_keyboard(),
        parse_mode="Markdown"
    )

async def show_agency_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات درخواست نمایندگی"""
    user_id = update.effective_user.id
    
    # بررسی آیا کاربر قبلاً نماینده است
    is_agent = await UserService.is_agent(user_id)
    
    if is_agent:
        await update.message.reply_text(
            "✅ **شما هم‌اکنون نماینده هستید!**\n\n"
            "💼 از پنل نمایندگی خود لذت ببرید.\n"
            "📊 قیمت‌های ویژه نمایندگان برای شما فعال است.",
            reply_markup=Keyboards.get_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    agency_info = (
        "🚀 **درخواست نمایندگی تیز VPN**\n\n"
        "💼 **مزایای نمایندگی:**\n"
        "• قیمت‌های ویژه عمده\n"
        "• پنل مدیریت اختصاصی\n"
        "• پشتیبانی VIP\n"
        "• درآمدزایی بالا\n\n"
        "💰 **هزینه فعال‌سازی:** ۱,۰۰۰,۰۰۰ تومان\n\n"
        "📦 **قیمت‌های نمایندگان:**\n"
        "• ۱ ماهه: ۷۰,۰۰۰ تومان\n"
        "• ۳ ماهه: ۲۱۰,۰۰۰ تومان\n"
        "• ۶ ماهه: ۳۸۰,۰۰۰ تومان\n\n"
        "✅ **پس از پرداخت، ۱,۰۰۰,۰۰۰ تومان به موجودی شما اضافه می‌شود.**\n\n"
        "📌 **برای ادامه، روش پرداخت را انتخاب کنید:**"
    )
    
    UserManager.set_state(user_id, "awaiting_agency_payment_method")
    
    await update.message.reply_text(
        agency_info,
        reply_markup=Keyboards.get_payment_method_keyboard(),
        parse_mode="Markdown"
    )

async def handle_agency_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """مدیریت دریافت فیش نمایندگی"""
    user_id = update.effective_user.id
    
    # بررسی ارسال فایل
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text(
            "❌ لطفاً فیش پرداخت را به صورت عکس یا فایل ارسال کنید.",
            reply_markup=Keyboards.get_back_keyboard()
        )
        return
    
    # ذخیره فایل
    await PaymentService.save_receipt(payment_id, file_id)
    
    # اطلاع به ادمین
    caption = (
        f"👨‍💼 **درخواست نمایندگی**\n\n"
        f"👤 کاربر: @{update.effective_user.username or 'بدون یوزرنیم'}\n"
        f"🆔 ID: `{user_id}`\n"
        f"💳 مبلغ: `1,000,000` تومان\n"
        f"🆔 کد پرداخت: `#{payment_id}`"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأیید نمایندگی", callback_data=f"approve_agency_{payment_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"reject_{payment_id}")
    ]])
    
    if update.message.photo:
        await context.bot.send_photo(
            ADMIN_ID,
            photo=file_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        await context.bot.send_document(
            ADMIN_ID,
            document=file_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    await update.message.reply_text(
        "✅ **فیش پرداخت شما دریافت شد.**\n\n"
        "📋 درخواست نمایندگی شما برای مدیریت ارسال شد.\n"
        "⏳ لطفاً منتظر تأیید باشید.",
        reply_markup=Keyboards.get_main_keyboard(),
        parse_mode="Markdown"
    )
    
    UserManager.clear_state(user_id)

# ========== مدیریت ادمین ==========
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های ادمین"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # بررسی وضعیت ادمین
    admin_state = UserManager.get_admin_state(user_id)
    
    # بازگشت به منو
    if text in ["⬅️ منوی کاربری", "بازگشت"]:
        await update.message.reply_text(
            "🏠 **منوی کاربری:**",
            reply_markup=Keyboards.get_main_keyboard(),
            parse_mode="Markdown"
        )
        UserManager.clear_admin_state(user_id)
        return
    
    # اگر ادمین در وضعیت خاصی است
    if admin_state:
        await handle_admin_state(update, context, admin_state)
        return
    
    # دستورات ادمین
    if text == "📊 آمار ربات":
        await show_admin_stats(update, context)
    
    elif text == "👥 مدیریت کاربران":
        await show_user_management(update, context)
    
    elif text == "💰 مدیریت پرداخت‌ها":
        await show_payment_management(update, context)
    
    elif text == "🎫 کدهای تخفیف":
        await show_coupon_management(update, context)
    
    elif text == "📢 اطلاع‌رسانی":
        await show_notification_menu(update, context)
    
    elif text == "⚙️ تنظیمات":
        await show_admin_settings(update, context)
    
    elif text == "💾 پشتیبان‌گیری":
        await backup_database(update, context)
    
    elif text == "🔄 بازیابی":
        await restore_database_prompt(update, context)
    
    else:
        # اگر دستور خاصی نبود، بررسی کن شاید کاربر معمولی است
        await handle_user_message(update, context)

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش آمار ربات"""
    stats = await LogService.get_stats()
    
    message = (
        "📊 **آمار کامل ربات**\n\n"
        f"👥 **کاربران:**\n"
        f"• کل کاربران: `{stats['total_users']:,}` نفر\n"
        f"• فعال امروز: `{stats['active_today']}` نفر\n"
        f"• نمایندگان: `{stats['agents_count']}` نفر\n\n"
        f"💰 **مالی:**\n"
        f"• درآمد امروز: `{stats['income_today']:,}` تومان\n"
        f"• درآمد این ماه: `{stats['income_month']:,}` تومان\n"
        f"• کل درآمد: `{stats['total_income']:,}` تومان\n\n"
        f"📦 **اشتراک‌ها:**\n"
        f"• اشتراک‌های فعال: `{stats['active_subscriptions']}` عدد\n"
        f"• پرداخت‌های در انتظار: `{stats['pending_payments']}` عدد\n\n"
        f"🕒 آخرین به‌روزرسانی: {stats['last_updated']}"
    )
    
    await update.message.reply_text(
        message,
        reply_markup=Keyboards.get_admin_main_keyboard(),
        parse_mode="Markdown"
    )

async def show_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی مدیریت کاربران"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("📋 لیست کاربران"), KeyboardButton("🔍 جستجوی کاربر")],
        [KeyboardButton("💰 تغییر موجودی"), KeyboardButton("👨‍💼 مدیریت نمایندگان")],
        [KeyboardButton("📊 آمار کاربران"), KeyboardButton("⬅️ بازگشت")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "👥 **مدیریت کاربران**\n\n"
        "📌 **گزینه مورد نظر را انتخاب کنید:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def show_payment_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی مدیریت پرداخت‌ها"""
    # تعداد پرداخت‌های در انتظار
    pending_count = await Database.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'pending'",
        fetchone=True
    )
    
    pending = pending_count[0] if pending_count else 0
    
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("⏳ پرداخت‌های در انتظار"), KeyboardButton("✅ پرداخت‌های تایید شده")],
        [KeyboardButton("❌ پرداخت‌های رد شده"), KeyboardButton("📊 آمار پرداخت‌ها")],
        [KeyboardButton("⬅️ بازگشت")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        f"💰 **مدیریت پرداخت‌ها**\n\n"
        f"⏳ **پرداخت‌های در انتظار:** `{pending}` عدد\n\n"
        f"📌 **گزینه مورد نظر را انتخاب کنید:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def show_coupon_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی مدیریت کدهای تخفیف"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("🎫 ایجاد کد تخفیف"), KeyboardButton("📋 لیست کدها")],
        [KeyboardButton("🔍 بررسی کد"), KeyboardButton("🗑️ حذف کد")],
        [KeyboardButton("📊 آمار کدها"), KeyboardButton("⬅️ بازگشت")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "🎫 **مدیریت کدهای تخفیف**\n\n"
        "📌 **گزینه مورد نظر را انتخاب کنید:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def show_notification_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی اطلاع‌رسانی"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("📢 ارسال به همه"), KeyboardButton("👨‍💼 ارسال به نمایندگان")],
        [KeyboardButton("👤 ارسال به کاربر خاص"), KeyboardButton("📋 تاریخچه اطلاعیه‌ها")],
        [KeyboardButton("⬅️ بازگشت")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "📢 **سیستم اطلاع‌رسانی**\n\n"
        "📌 **گزینه مورد نظر را انتخاب کنید:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تنظیمات ادمین"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("💳 تغییر اطلاعات بانکی"), KeyboardButton("🔑 تغییر TRON")],
        [KeyboardButton("📢 تغییر کانال"), KeyboardButton("⚡ تنظیمات پیشرفته")],
        [KeyboardButton("⬅️ بازگشت")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "⚙️ **تنظیمات ربات**\n\n"
        f"🏦 **بانک:** {BANK_NAME}\n"
        f"💳 **کارت:** `{BANK_CARD}`\n"
        f"👤 **به نام:** {BANK_OWNER}\n"
        f"💎 **TRON:** `{TRON_ADDRESS}`\n"
        f"📢 **کانال:** {CHANNEL_USERNAME}\n\n"
        f"📌 **گزینه مورد نظر را انتخاب کنید:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def backup_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پشتیبان‌گیری از دیتابیس"""
    await update.message.reply_text("🔄 در حال تهیه پشتیبان از دیتابیس...")
    
    try:
        # استخراج اطلاعات اتصال
        parsed = urllib.parse.urlparse(DATABASE_URL)
        db_host = parsed.hostname
        db_port = parsed.port or 5432
        db_name = parsed.path[1:]
        db_user = parsed.username
        db_password = parsed.password
        
        # ایجاد فایل موقت
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{timestamp}.sql"
        
        # دستور pg_dump
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-f', backup_file,
            '-F', 'p'
        ]
        
        # تنظیم محیط
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        # اجرای دستور
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8') if stderr else "خطای نامشخص"
            await update.message.reply_text(f"❌ خطا در پشتیبان‌گیری:\n{error_msg}")
            return
        
        # ارسال فایل
        with open(backup_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                filename=f"teazvpn_backup_{timestamp}.sql",
                caption="✅ پشتیبان دیتابیس با موفقیت تهیه شد."
            )
        
        # حذف فایل موقت
        os.remove(backup_file)
        
        await update.message.reply_text("✅ پشتیبان‌گیری با موفقیت انجام شد.")
        
    except Exception as e:
        logger.error(f"❌ خطا در پشتیبان‌گیری: {e}")
        await update.message.reply_text(f"❌ خطا در پشتیبان‌گیری: {str(e)}")

async def restore_database_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست فایل بازیابی"""
    UserManager.set_admin_state(ADMIN_ID, "awaiting_backup_file")
    
    await update.message.reply_text(
        "📤 **لطفاً فایل پشتیبان دیتابیس را ارسال کنید:**\n\n"
        "⚠️ **هشدار:** این عملیات تمام داده‌های فعلی را جایگزین می‌کند!",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("❌ انصراف")]
        ], resize_keyboard=True)
    )

async def handle_admin_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str):
    """مدیریت وضعیت‌های ادمین"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if state == "awaiting_backup_file":
        if text == "❌ انصراف":
            UserManager.clear_admin_state(user_id)
            await update.message.reply_text(
                "❌ عملیات بازیابی لغو شد.",
                reply_markup=Keyboards.get_admin_main_keyboard()
            )
            return
        
        # دریافت فایل
        if update.message.document:
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                backup_file = f"restore_backup.sql"
                
                await file.download_to_drive(backup_file)
                
                await update.message.reply_text("🔄 در حال بازیابی دیتابیس...")
                
                # بازیابی
                parsed = urllib.parse.urlparse(DATABASE_URL)
                db_host = parsed.hostname
                db_port = parsed.port or 5432
                db_name = parsed.path[1:]
                db_user = parsed.username
                db_password = parsed.password
                
                cmd = [
                    'psql',
                    '-h', db_host,
                    '-p', str(db_port),
                    '-U', db_user,
                    '-d', db_name,
                    '-f', backup_file
                ]
                
                env = os.environ.copy()
                env['PGPASSWORD'] = db_password
                
                process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate()
                
                if process.returncode != 0:
                    error_msg = stderr.decode('utf-8') if stderr else "خطای نامشخص"
                    await update.message.reply_text(f"❌ خطا در بازیابی:\n{error_msg}")
                else:
                    await update.message.reply_text(
                        "✅ دیتابیس با موفقیت بازیابی شد!",
                        reply_markup=Keyboards.get_admin_main_keyboard()
                    )
                
                # حذف فایل
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                
                UserManager.clear_admin_state(user_id)
                
            except Exception as e:
                logger.error(f"❌ خطا در بازیابی: {e}")
                await update.message.reply_text(f"❌ خطا در بازیابی: {str(e)}")
        else:
            await update.message.reply_text("❌ لطفاً یک فایل پشتیبان ارسال کنید.")

# ========== مدیریت Callback Query ==========
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت کلیک روی دکمه‌های اینلاین"""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    await query.answer()
    
    logger.info(f"🔄 Callback Query: {data} from {user_id}")
    
    # بررسی دسترسی ادمین
    if user_id != ADMIN_ID:
        await query.message.reply_text("❌ شما مجوز انجام این عمل را ندارید.")
        return
    
    if data.startswith("approve_"):
        if data.startswith("approve_agency_"):
            payment_id = int(data.split("_")[-1])
            await approve_agency_request(query, context, payment_id)
        else:
            payment_id = int(data.split("_")[-1])
            await approve_payment(query, context, payment_id)
    
    elif data.startswith("reject_"):
        payment_id = int(data.split("_")[-1])
        await reject_payment(query, context, payment_id)
    
    elif data.startswith("send_config_"):
        payment_id = int(data.split("_")[-1])
        await send_config_prompt(query, context, payment_id)
    
    elif data.startswith("config_sent_"):
        payment_id = int(data.split("_")[-1])
        await mark_config_sent(query, context, payment_id)

async def approve_payment(query, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """تایید پرداخت"""
    # دریافت اطلاعات پرداخت
    payment = await PaymentService.get_payment(payment_id)
    
    if not payment:
        await query.message.reply_text("❌ پرداخت یافت نشد.")
        return
    
    user_id = payment["user_id"]
    amount = payment["amount"]
    payment_type = payment["type"]
    
    # آپدیت وضعیت پرداخت
    success = await PaymentService.update_payment_status(
        payment_id, "approved", ADMIN_ID, "پرداخت تایید شد"
    )
    
    if not success:
        await query.message.edit_reply_markup(None)
        await query.message.reply_text("❌ خطا در تایید پرداخت.")
        return
    
    # بر اساس نوع پرداخت عمل کن
    if payment_type == "increase_balance":
        # افزایش موجودی
        await UserService.update_balance(user_id, amount, "افزایش موجودی تایید شده")
        
        # اطلاع به کاربر
        await context.bot.send_message(
            user_id,
            f"✅ **پرداخت شما تایید شد!**\n\n"
            f"💰 **مبلغ:** `{amount:,}` تومان\n"
            f"💎 **به موجودی شما اضافه شد.**\n"
            f"🆔 **کد پرداخت:** `#{payment_id}`",
            parse_mode="Markdown"
        )
        
        await query.message.edit_reply_markup(None)
        await query.message.reply_text(
            f"✅ پرداخت #{payment_id} تایید و موجودی کاربر افزایش یافت."
        )
    
    elif payment_type == "buy_subscription":
        # ایجاد اشتراک
        plan_name = payment["description"]
        subscription_id = await SubscriptionService.create_subscription(
            user_id, payment_id, plan_name
        )
        
        if subscription_id:
            # اطلاع به کاربر
            await context.bot.send_message(
                user_id,
                f"✅ **پرداخت شما تایید شد!**\n\n"
                f"📦 **پلن:** {plan_name}\n"
                f"💰 **مبلغ:** `{amount:,}` تومان\n"
                f"🆔 **کد پرداخت:** `#{payment_id}`\n"
                f"📦 **کد اشتراک:** `#{subscription_id}`\n\n"
                f"📝 کانفیگ شما حداکثر تا ۱ ساعت آینده ارسال خواهد شد.",
                parse_mode="Markdown"
            )
            
            # دکمه ارسال کانفیگ
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🟣 ارسال کانفیگ",
                    callback_data=f"send_config_{payment_id}"
                )
            ]])
            
            await query.message.edit_reply_markup(None)
            await query.message.reply_text(
                f"✅ پرداخت #{payment_id} تایید و اشتراک ایجاد شد.",
                reply_markup=keyboard
            )
        else:
            await query.message.edit_reply_markup(None)
            await query.message.reply_text("❌ خطا در ایجاد اشتراک.")

async def approve_agency_request(query, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """تایید درخواست نمایندگی"""
    payment = await PaymentService.get_payment(payment_id)
    
    if not payment:
        await query.message.reply_text("❌ پرداخت یافت نشد.")
        return
    
    user_id = payment["user_id"]
    
    # تایید پرداخت
    success = await PaymentService.update_payment_status(
        payment_id, "approved", ADMIN_ID, "نمایندگی تایید شد"
    )
    
    if not success:
        await query.message.edit_reply_markup(None)
        await query.message.reply_text("❌ خطا در تایید پرداخت.")
        return
    
    # تنظیم کاربر به عنوان نماینده
    await UserService.set_as_agent(user_id)
    
    # اضافه کردن مبلغ به موجودی
    await UserService.update_balance(user_id, 1000000, "فعال‌سازی نمایندگی")
    
    # اطلاع به کاربر
    await context.bot.send_message(
        user_id,
        f"🎉 **تبریک! شما نماینده شدید!**\n\n"
        f"👨‍💼 **حساب شما ارتقا یافت.**\n"
        f"💰 **مبلغ ۱,۰۰۰,۰۰۰ تومان به موجودی شما اضافه شد.**\n"
        f"📦 **اکنون می‌توانید با قیمت نمایندگان خرید کنید.**\n\n"
        f"💼 از پنل نمایندگی خود لذت ببرید!",
        parse_mode="Markdown"
    )
    
    await query.message.edit_reply_markup(None)
    await query.message.reply_text(
        f"✅ نمایندگی کاربر {user_id} تایید و حساب ارتقا یافت."
    )

async def reject_payment(query, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """رد پرداخت"""
    payment = await PaymentService.get_payment(payment_id)
    
    if not payment:
        await query.message.reply_text("❌ پرداخت یافت نشد.")
        return
    
    user_id = payment["user_id"]
    
    # رد پرداخت
    success = await PaymentService.update_payment_status(
        payment_id, "rejected", ADMIN_ID, "پرداخت رد شد"
    )
    
    if not success:
        await query.message.edit_reply_markup(None)
        await query.message.reply_text("❌ خطا در رد پرداخت.")
        return
    
    # اطلاع به کاربر
    await context.bot.send_message(
        user_id,
        f"❌ **پرداخت شما رد شد.**\n\n"
        f"📌 **دلیل:** ممکن است فیش نامعتبر باشد.\n"
        f"💡 **راه حل:** با پشتیبانی تماس بگیرید.\n"
        f"🆔 **کد پرداخت:** `#{payment_id}`",
        parse_mode="Markdown"
    )
    
    await query.message.edit_reply_markup(None)
    await query.message.reply_text(f"❌ پرداخت #{payment_id} رد شد.")

async def send_config_prompt(query, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """درخواست ارسال کانفیگ"""
    UserManager.set_admin_state(ADMIN_ID, f"awaiting_config_{payment_id}")
    
    await query.message.reply_text(
        f"📝 **لطفاً کانفیگ را برای پرداخت #{payment_id} ارسال کنید:**\n\n"
        "📌 می‌توانید به صورت متن یا فایل ارسال کنید.",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("❌ انصراف")]
        ], resize_keyboard=True)
    )

async def mark_config_sent(query, context: ContextTypes.DEFAULT_TYPE, payment_id: int):
    """علامت‌گذاری کانفیگ ارسال شده"""
    # دریافت اشتراک مرتبط
    subscription = await Database.execute(
        "SELECT id FROM subscriptions WHERE payment_id = %s",
        (payment_id,), fetchone=True
    )
    
    if subscription:
        subscription_id = subscription[0]
        
        # آپدیت وضعیت
        await Database.execute(
            "UPDATE subscriptions SET config_sent = TRUE WHERE id = %s",
            (subscription_id,)
        )
        
        await query.message.edit_reply_markup(None)
        await query.message.reply_text(f"✅ کانفیگ برای اشتراک #{subscription_id} ارسال شد.")

# ========== Webhook Handlers ==========
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """هندلر Webhook"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        
        logger.debug(f"🌐 Webhook received: {update.update_id}")
        
        # پردازش در پس‌زمینه
        background_tasks.add_task(process_webhook_update, update)
        
        return {"ok": True, "update_id": update.update_id}
        
    except Exception as e:
        logger.error(f"❌ Error in webhook: {e}")
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)}
        )

async def process_webhook_update(update: Update):
    """پردازش آپدیت Webhook"""
    try:
        await application.update_queue.put(update)
    except Exception as e:
        logger.error(f"❌ Error processing update: {e}")

# ========== Endpoint های HTTP ==========
@app.get("/")
async def health_check():
    """بررسی سلامت"""
    return {
        "status": "up",
        "service": "Teaz VPN Bot",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "health": "/",
            "status": "/status",
            "ping": "/ping",
            "stats": "/stats",
            "webhook": WEBHOOK_PATH
        }
    }

@app.get("/ping")
async def ping():
    """Endpoint برای UptimeRobot"""
    return {
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "service": "teaz-vpn-bot"
    }

@app.get("/status")
async def system_status():
    """وضعیت سیستم"""
    try:
        # بررسی دیتابیس
        db_ok = False
        if db_pool:
            try:
                result = await Database.execute("SELECT 1")
                db_ok = result is not None
            except:
                db_ok = False
        
        # آمار
        stats = await LogService.get_stats()
        
        return {
            "status": "healthy" if db_ok else "degraded",
            "database": "connected" if db_ok else "disconnected",
            "telegram_bot": "running",
            "uptime": "unknown",  # می‌توانید با psutil محاسبه کنید
            "statistics": stats,
            "last_checked": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error in status endpoint: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/stats")
async def api_stats():
    """API آمار"""
    try:
        stats = await LogService.get_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"❌ Error in stats API: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========== Keep-Alive System ==========
async def keep_alive_task():
    """نگه‌داری بات فعال"""
    while True:
        try:
            # ارسال درخواست به خود
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{RENDER_BASE_URL}/ping") as resp:
                    if resp.status == 200:
                        logger.debug("♻️ Keep-alive ping successful")
                    else:
                        logger.warning(f"⚠️ Keep-alive failed: {resp.status}")
            
            # بررسی دیتابیس
            try:
                await Database.execute("SELECT 1")
                logger.debug("✅ Database connection check successful")
            except Exception as e:
                logger.error(f"❌ Database check failed: {e}")
            
            # 5 دقیقه صبر کن
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"❌ Error in keep-alive task: {e}")
            await asyncio.sleep(60)

# ========== Startup & Shutdown ==========
@app.on_event("startup")
async def startup():
    """رویداد راه‌اندازی"""
    logger.info("🚀 Starting Teaz VPN Bot...")
    
    try:
        # راه‌اندازی دیتابیس
        Database.init()
        await create_tables()
        logger.info("✅ Database initialized")
        
        # راه‌اندازی بات تلگرام
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
        logger.info(f"✅ Webhook set: {WEBHOOK_URL}")
        
        # تنظیم دستورات بات
        commands = [
            BotCommand("start", "شروع ربات"),
            BotCommand("help", "راهنما"),
            BotCommand("balance", "موجودی"),
            BotCommand("subscriptions", "اشتراک‌های من")
        ]
        
        await application.bot.set_my_commands(commands)
        logger.info("✅ Bot commands set")
        
        # راه‌اندازی اپلیکیشن
        await application.initialize()
        await application.start()
        
        # شروع Keep-Alive
        asyncio.create_task(keep_alive_task())
        logger.info("✅ Keep-alive task started")
        
        # اطلاع به ادمین
        try:
            await application.bot.send_message(
                ADMIN_ID,
                f"✅ **ربات با موفقیت راه‌اندازی شد!**\n\n"
                f"🕒 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🌐 آدرس: {RENDER_BASE_URL}\n"
                f"🔗 Webhook: {WEBHOOK_URL}\n"
                f"📊 وضعیت: {RENDER_BASE_URL}/status",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"❌ Failed to notify admin: {e}")
        
        print("\n" + "="*60)
        print("🚀 TEAZ VPN BOT STARTED SUCCESSFULLY!")
        print("="*60)
        print(f"📊 Health Check: {RENDER_BASE_URL}/")
        print(f"📈 Status: {RENDER_BASE_URL}/status")
        print(f"🔄 Webhook: {WEBHOOK_URL}")
        print(f"🤖 Bot: @teazvpn_bot")
        print(f"👨‍💼 Admin ID: {ADMIN_ID}")
        print("="*60 + "\n")
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    """رویداد خاموش‌سازی"""
    logger.info("🛑 Shutting down Teaz VPN Bot...")
    
    try:
        # اطلاع به ادمین
        try:
            await application.bot.send_message(
                ADMIN_ID,
                f"⚠️ **ربات در حال خاموش‌سازی...**\n\n"
                f"🕒 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown"
            )
        except:
            pass
        
        # توقف اپلیکیشن
        await application.stop()
        await application.shutdown()
        
        # بستن دیتابیس
        Database.close()
        
        logger.info("✅ Shutdown completed")
        
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# ========== ثبت هندلرها ==========
def setup_handlers():
    """تنظیم هندلرهای بات"""
    
    # Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("balance", show_balance_menu))
    
    # Message Handlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_message
    ))
    
    # مدیریت انتخاب پلن
    async def handle_plan_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        if text in SubscriptionService.PRICE_MAP:
            await handle_subscription_plan(update, context, text)
    
    application.add_handler(MessageHandler(
        filters.Text([
            "🥉۱ ماهه | ۹۰ هزار تومان | نامحدود | ۲ کاربره",
            "🥈۳ ماهه | ۲۵۰ هزار تومان | نامحدود | ۲ کاربره", 
            "🥇۶ ماهه | ۴۵۰ هزار تومان | نامحدود | ۲ کاربره",
            "🥉۱ ماهه | ۷۰,۰۰۰ تومان | نامحدود | ۲ کاربره",
            "🥈۳ ماهه | ۲۱۰,۰۰۰ تومان | نامحدود | ۲ کاربره", 
            "🥇۶ ماهه | ۳۸۰,۰۰۰ تومان | نامحدود | ۲ کاربره"
        ]), handle_plan_selection
    ))
    
    # Callback Query Handler
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # هندلر برای عکس و فایل (فیش پرداخت)
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.DOCUMENT,
        async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            state = UserManager.get_state(user_id)
            
            if state and ("receipt" in state or "config" in state):
                # فایل در وضعیت مناسب دریافت شده
                pass
            else:
                await update.message.reply_text(
                    "📁 **برای ارسال فایل، لطفاً ابتدا مراحل پرداخت را طی کنید.**",
                    reply_markup=Keyboards.get_main_keyboard()
                )
    ))
    
    logger.info("✅ Handlers registered successfully")

# ========== اجرای برنامه ==========
if __name__ == "__main__":
    import uvicorn
    
    # تنظیم هندلرها
    setup_handlers()
    
    # تنظیمات سرور
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        log_level="info",
        access_log=True,
        timeout_keep_alive=30,
        reload=False
    )
    
    server = uvicorn.Server(config)
    
    print("\n" + "="*60)
    print("🤖 TELEGRAM VPN BOT - READY TO LAUNCH")
    print("="*60)
    print(f"🌐 Host: 0.0.0.0")
    print(f"🔌 Port: {os.getenv('PORT', 10000)}")
    print(f"📁 Database: {'Connected' if DATABASE_URL else 'Not Configured'}")
    print(f"🤖 Bot Token: {'***' + TOKEN[-5:] if TOKEN else 'Not Set'}")
    print(f"👑 Admin: {ADMIN_ID}")
    print(f"📢 Channel: {CHANNEL_USERNAME}")
    print("="*60)
    print("⚡ Starting server...\n")
    
    # اجرای سرور
    asyncio.run(server.serve())
