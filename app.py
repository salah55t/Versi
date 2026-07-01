"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           🔥 CYBERPUNK DISTRIBUTOR CORE v5.2 PRO EDITION 🔥                 ║
║                                                                              ║
║  Professional Telegram Bot Bridge for OpenBullet Account Distribution       ║
║  Features: Advanced User Management, Audit Trail, Reservations,            ║
║            Smart Notifications, Rate Limiting, Statistics Dashboard         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
import httpx
import os
import logging
import hashlib
import time
import json
import datetime
import re
import asyncio
import csv
import io
from functools import wraps
from typing import Optional, List, Dict, Any
from collections import defaultdict, deque
from contextlib import contextmanager
import tempfile

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, func,
    DateTime, Text, ForeignKey, Index, desc, asc, exc as sqlalchemy_exc,
    inspect, text, case
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════
#                    LOGGING SYSTEM
# ═══════════════════════════════════════════════════════════

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{self.BOLD}{color}{record.levelname}{self.RESET}"
        return super().format(record)


os.makedirs("logs", exist_ok=True)
log_filename = f"logs/cyberpunk_core_{datetime.datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

console_handler = logging.getLogger().handlers[-1]
console_handler.setFormatter(ColoredFormatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S"
))

logger = logging.getLogger("CYBER-CORE")
logger.info("╔═══════════════════════════════════════════════════════════════╗")
logger.info("║  🔥 CYBERPUNK DISTRIBUTOR CORE v5.2 PRO - INITIALIZING... 🔥  ║")
logger.info("╚═══════════════════════════════════════════════════════════════╝")

# ═══════════════════════════════════════════════════════════
#                    APP & CONFIG
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="Cyberpunk Distributor Core",
    description="Professional Account Distribution System with Advanced Management",
    version="5.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_IDS", "6624995237").split(",") if uid.strip()]
OPENBULLET_URL = os.getenv("OPENBULLET_URL", "")
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

CLAIM_COOLDOWN_SECONDS = int(os.getenv("CLAIM_COOLDOWN_SECONDS", "86400"))
RESERVATION_MINUTES = int(os.getenv("RESERVATION_MINUTES", "10"))
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))
RATE_LIMIT_CLAIMS = int(os.getenv("RATE_LIMIT_CLAIMS", "5"))
NOTIFY_ADMINS_ON_LOW_STOCK = os.getenv("NOTIFY_ADMINS_ON_LOW_STOCK", "true").lower() == "true"

logger.info(f"⚙️  Admin IDs: {ADMIN_IDS}")
logger.info(f"⚙️  Claim Cooldown: {CLAIM_COOLDOWN_SECONDS}s")
logger.info(f"⚙️  Reservation Duration: {RESERVATION_MINUTES}min")
logger.info(f"⚙️  Low Stock Threshold: {LOW_STOCK_THRESHOLD}")

# ═══════════════════════════════════════════════════════════
#                    DATABASE MODELS
# ═══════════════════════════════════════════════════════════

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String(255), index=True)
    account_data = Column(String(1000), unique=True, index=True)
    captured_data = Column(Text)
    is_given = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    given_at = Column(DateTime, nullable=True)
    given_to = Column(String(50), nullable=True, index=True)
    source_job = Column(String(255), nullable=True)
    quality_score = Column(Integer, default=100)
    tags = Column(String(500), default="")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), unique=True, index=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    join_date = Column(DateTime, default=datetime.datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.datetime.utcnow)
    total_claims = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String(500), nullable=True)
    reputation_score = Column(Integer, default=100)
    notes = Column(Text, nullable=True)


class DeliveredAccount(Base):
    __tablename__ = "delivered_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    config_name = Column(String(255), nullable=True)
    delivered_at = Column(DateTime, default=datetime.datetime.utcnow)
    delivery_type = Column(String(20), default="claim")


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    config_name = Column(String(255))
    account_id = Column(Integer, ForeignKey("accounts.id"))
    reserved_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime)
    status = Column(String(20), default="active")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    user_id = Column(String(50), nullable=True, index=True)
    action = Column(String(50), index=True)
    details = Column(Text)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)


class BannedUser(Base):
    __tablename__ = "banned_users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), unique=True, index=True)
    banned_at = Column(DateTime, default=datetime.datetime.utcnow)
    banned_by = Column(String(50))
    reason = Column(String(500))
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    notification_type = Column(String(30), default="general")


class SystemStats(Base):
    __tablename__ = "system_stats"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_time = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    total_accounts = Column(Integer, default=0)
    available_accounts = Column(Integer, default=0)
    given_accounts = Column(Integer, default=0)
    total_users = Column(Integer, default=0)
    active_reservations = Column(Integer, default=0)
    claims_today = Column(Integer, default=0)
    top_config = Column(String(255), nullable=True)
    avg_quality = Column(Integer, default=0)


# ═══════════════════════════════════════════════════════════
#         ★★★ DATABASE MIGRATION (الإصلاح الأساسي) ★★★
# ═══════════════════════════════════════════════════════════

def migrate_database():
    """
    إضافة الأعمدة الناقصة للجداول الموجودة.
    create_all() لا يضيف أعمدة جديدة - لذلك نستخدم ALTER TABLE.
    """
    db_inspector = inspect(engine)

    with engine.begin() as conn:
        # ── هجرة جدول accounts ──
        if 'accounts' in db_inspector.get_table_names():
            existing = {c['name'] for c in db_inspector.get_columns('accounts')}
            cols_to_add = {
                'created_at':    "TIMESTAMP DEFAULT NOW()",
                'given_at':      "TIMESTAMP",
                'given_to':      "VARCHAR(50)",
                'source_job':    "VARCHAR(255)",
                'quality_score': "INTEGER DEFAULT 100",
                'tags':          "VARCHAR(500) DEFAULT ''",
            }
            for col_name, col_def in cols_to_add.items():
                if col_name not in existing:
                    try:
                        conn.execute(text(
                            f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}"
                        ))
                        logger.info(f"✅ Migration: Added accounts.{col_name}")
                    except Exception as e:
                        logger.error(f"❌ Migration failed accounts.{col_name}: {e}")

        # ── هجرة جدول delivered_accounts ──
        if 'delivered_accounts' in db_inspector.get_table_names():
            existing = {c['name'] for c in db_inspector.get_columns('delivered_accounts')}
            cols_to_add = {
                'account_id':    "INTEGER REFERENCES accounts(id)",
                'delivery_type': "VARCHAR(20) DEFAULT 'claim'",
            }
            for col_name, col_def in cols_to_add.items():
                if col_name not in existing:
                    try:
                        conn.execute(text(
                            f"ALTER TABLE delivered_accounts ADD COLUMN {col_name} {col_def}"
                        ))
                        logger.info(f"✅ Migration: Added delivered_accounts.{col_name}")
                    except Exception as e:
                        logger.error(f"❌ Migration failed delivered_accounts.{col_name}: {e}")

        # ── هجرة جدول users (أعمدة محتملة ناقصة) ──
        if 'users' in db_inspector.get_table_names():
            existing = {c['name'] for c in db_inspector.get_columns('users')}
            cols_to_add = {
                'is_banned':        "BOOLEAN DEFAULT FALSE",
                'ban_reason':       "VARCHAR(500)",
                'reputation_score': "INTEGER DEFAULT 100",
                'notes':            "TEXT",
            }
            for col_name, col_def in cols_to_add.items():
                if col_name not in existing:
                    try:
                        conn.execute(text(
                            f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                        ))
                        logger.info(f"✅ Migration: Added users.{col_name}")
                    except Exception as e:
                        logger.error(f"❌ Migration failed users.{col_name}: {e}")

    logger.info("✅ Database migration completed")


# Create tables first, then migrate
Base.metadata.create_all(bind=engine)
migrate_database()
logger.info("✅ Database tables initialized & migrated")

# ═══════════════════════════════════════════════════════════
#                    IN-MEMORY TRACKING
# ═══════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self):
        self._windows: Dict[str, deque] = defaultdict(deque)
        self._claim_windows: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        window = self._windows[key]
        while window and window[0] < now - window_seconds:
            window.popleft()
        return len(window) < max_requests

    def record(self, key: str):
        self._windows[key].append(time.time())

    def get_remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        now = time.time()
        window = self._windows[key]
        while window and window[0] < now - window_seconds:
            window.popleft()
        return max(0, max_requests - len(window))

    def is_claim_allowed(self, user_id: str) -> bool:
        now = time.time()
        window = self._claim_windows[user_id]
        while window and window[0] < now - 3600:
            window.popleft()
        return len(window) < RATE_LIMIT_CLAIMS

    def record_claim(self, user_id: str):
        self._claim_windows[user_id].append(time.time())


rate_limiter = RateLimiter()

# ═══════════════════════════════════════════════════════════
#                    PENDING ACTIONS
# ═══════════════════════════════════════════════════════════

pending_broadcasts: Dict[str, bool] = {}
pending_bans: Dict[str, bool] = {}
pending_unbans: Dict[str, bool] = {}
pending_user_search: Dict[str, bool] = {}

# ═══════════════════════════════════════════════════════════
#                    HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def config_hash(name: str) -> str:
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def safe_md(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("`", "'").replace("\\", "/")


def escape_html(text: str) -> str:
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def resolve_progress(val) -> str:
    try:
        p = float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return "0.0"
    if p <= 1.0:
        return f"{p * 100:.1f}"
    return f"{p:.1f}"


def format_datetime(dt: Optional[datetime.datetime]) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def time_ago(dt: Optional[datetime.datetime]) -> str:
    if not dt:
        return "never"
    now = datetime.datetime.utcnow()
    diff = now - dt
    if diff.total_seconds() < 60:
        return f"{int(diff.total_seconds())}s ago"
    elif diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() / 60)}m ago"
    elif diff.total_seconds() < 86400:
        return f"{int(diff.total_seconds() / 3600)}h ago"
    else:
        return f"{int(diff.total_seconds() / 86400)}d ago"


@contextmanager
def get_db():
    """
    ★ مُصلح: لا يُcommit تلقائياً.
    كل handler يتحكم بcommit الخاص به لتجنب مشاكل الجلسة.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


def log_audit(db, user_id: Optional[str], action: str, details: str, request: Request = None):
    try:
        ip = None
        ua = None
        if request is not None:
            try:
                ip = request.client.host if request.client else None
                ua = request.headers.get("user-agent")
            except Exception:
                pass
        entry = AuditLog(
            user_id=user_id,
            action=action,
            details=details,
            ip_address=ip,
            user_agent=ua
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        logger.error(f"Audit log error: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def get_or_create_user(db, user_id: str, user_info: dict = None):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        user = User(
            user_id=user_id,
            username=user_info.get("username") if user_info else None,
            first_name=user_info.get("first_name") if user_info else None,
            last_name=user_info.get("last_name") if user_info else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"👤 New user registered: {user_id}")
    else:
        user.last_activity = datetime.datetime.utcnow()
        if user_info:
            if user_info.get("username"):
                user.username = user_info.get("username")
            if user_info.get("first_name"):
                user.first_name = user_info.get("first_name")
            if user_info.get("last_name"):
                user.last_name = user_info.get("last_name")
        db.commit()
    return user


def is_user_banned(db, user_id: str) -> tuple:
    ban = db.query(BannedUser).filter(
        BannedUser.user_id == user_id,
        BannedUser.is_active == True
    ).first()
    if not ban:
        return False, None
    if ban.expires_at and ban.expires_at < datetime.datetime.utcnow():
        ban.is_active = False
        db.commit()
        return False, None
    return True, ban.reason


def clean_expired_reservations(db):
    now = datetime.datetime.utcnow()
    expired = db.query(Reservation).filter(
        Reservation.expires_at < now,
        Reservation.status == "active"
    ).all()
    count = 0
    for res in expired:
        res.status = "expired"
        account = db.query(Account).filter(Account.id == res.account_id).first()
        if account:
            account.is_given = False
            account.given_to = None
            logger.info(f"🔄 Freed account {account.id} from expired reservation")
        count += 1
    if count > 0:
        db.commit()
        logger.info(f"🧹 Cleaned {count} expired reservations")
    return count


# ═══════════════════════════════════════════════════════════
#                    TELEGRAM KEYBOARDS
# ═══════════════════════════════════════════════════════════

def get_main_keyboard(is_admin: bool):
    buttons = [
        [
            {"text": "⚡ 🧬 سحب حساب جديد 🧬 ⚡"},
            {"text": "📡 🌐 إحصائيات المخزن 🌐 📡"},
        ],
        [
            {"text": "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖"},
            {"text": "📊 📈 إحصائيات متقدمة 📈 📊"},
        ],
        [
            {"text": "🕐 📜 سجل عملياتي 📜 🕐"},
            {"text": "🏦 ⏳ حجز مؤقت ⏳ 🏦"},
        ],
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ 👾 لوحة تحكم المطور 👾 🛠️"}])
        buttons.append([{"text": "📋 🔐 إدارة المستخدمين 🔐 📋"}])
        buttons.append([{"text": "💾 📤 تصدير/نسخ احتياطي 📤 💾"}])
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def get_admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "reset_delivered"},
                {"text": "🚨 تصفير المخزن", "callback_data": "clear_accounts"},
            ],
            [
                {"text": "🔄 تحديث البيانات", "callback_data": "refresh_admin_stats"},
                {"text": "📊 تقرير مفصل", "callback_data": "detailed_report"},
            ],
            [
                {"text": "🔍 فحص المخزن", "callback_data": "scan_inventory"},
                {"text": "⚠️ المخالفين", "callback_data": "banned_users"},
            ],
            [
                {"text": "📢 إشعار عام", "callback_data": "broadcast"},
                {"text": "⚙️ الإعدادات", "callback_data": "settings"},
            ],
        ]
    }


def get_user_management_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🚫 حظر مستخدم", "callback_data": "ban_user"},
                {"text": "✅ فك حظر", "callback_data": "unban_user"},
            ],
            [
                {"text": "📋 قائمة المحظورين", "callback_data": "list_banned"},
                {"text": "👥 قائمة المستخدمين", "callback_data": "list_users"},
            ],
            [
                {"text": "🔍 بحث عن مستخدم", "callback_data": "search_user"},
                {"text": "📊 إحصائيات المستخدمين", "callback_data": "user_stats"},
            ],
            [
                {"text": "🔙 رجوع", "callback_data": "back_to_admin"},
            ],
        ]
    }


def get_backup_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "💾 تصدير الحسابات (JSON)", "callback_data": "export_json"},
                {"text": "📄 تصدير CSV", "callback_data": "export_csv"},
            ],
            [
                {"text": "📋 تصدير السجل", "callback_data": "export_audit"},
                {"text": "🔄 نسخ احتياطي كامل", "callback_data": "full_backup"},
            ],
            [
                {"text": "🔙 رجوع", "callback_data": "back_to_admin"},
            ],
        ]
    }


def get_cancel_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "❌ إلغاء", "callback_data": "cancel_action"}]
        ]
    }


def get_reservation_keyboard(config_hash_val: str):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ تأكيد السحب", "callback_data": f"confirm_reserve:{config_hash_val}"},
                {"text": "❌ إلغاء الحجز", "callback_data": f"cancel_reserve:{config_hash_val}"},
            ]
        ]
    }


# ═══════════════════════════════════════════════════════════
#                    TELEGRAM API WRAPPERS
# ═══════════════════════════════════════════════════════════

async def tg_send(chat_id: str, text: str, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    payload.update(kwargs)
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_send failed: {e}")


async def tg_send_html(chat_id: str, text: str, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    payload.update(kwargs)
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_send_html failed: {e}")


async def tg_edit(chat_id: str, message_id: int, text: str, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    payload.update(kwargs)
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_edit failed: {e}")


async def tg_answer(callback_id: str, text: str, show_alert: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(
                url,
                json={
                    "callback_query_id": callback_id,
                    "text": text,
                    "show_alert": show_alert,
                },
            )
        except Exception as e:
            logger.error(f"tg_answer failed: {e}")


async def tg_delete_message(chat_id: str, message_id: int):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(url, json={"chat_id": chat_id, "message_id": message_id})
        except Exception as e:
            logger.error(f"tg_delete failed: {e}")


async def tg_send_document(chat_id: str, file_path: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"

    def _read_and_send():
        try:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
                import requests
                resp = requests.post(url, data=data, files=files, timeout=30)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"tg_send_document failed: {e}")
            return False

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _read_and_send)


# ═══════════════════════════════════════════════════════════
#                    OPENBULLET API
# ═══════════════════════════════════════════════════════════

async def get_auth_headers() -> tuple:
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return None, None

    base = OPENBULLET_URL.strip().rstrip("/")
    raw_key = OPENBULLET_API_KEY.strip()
    test_url = f"{base}/api/v1/job/all"

    methods = [
        ({"Authorization": raw_key, "Accept": "application/json"}, "Direct"),
        ({"Authorization": f"Bearer {raw_key}", "Accept": "application/json"}, "Bearer"),
        ({"X-API-Key": raw_key, "Accept": "application/json"}, "X-API-Key"),
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for hdrs, label in methods:
                try:
                    resp = await client.get(test_url, headers=hdrs)
                    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                        logger.info(f"🔐 Auth '{label}' succeeded")
                        return hdrs, label
                    else:
                        logger.warning(f"🔐 Auth '{label}' -> HTTP {resp.status_code}")
                except Exception as e:
                    logger.error(f"🔐 Auth '{label}' connection error: {e}")
    except Exception as e:
        logger.error(f"🔐 Auth fatal error: {e}")

    return None, None


def _unwrap(obj) -> dict:
    if not isinstance(obj, dict):
        return obj
    if "value" in obj and isinstance(obj["value"], dict):
        merged = {k: v for k, v in obj.items() if k != "value"}
        merged.update(obj["value"])
        return merged
    return obj


def _g(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _num(val, fallback=0) -> int:
    try:
        return int(float(val)) if val is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _is_active(status_val) -> bool:
    if status_val is None:
        return False
    return str(status_val).strip().lower() in ("running", "active", "started", "executing")


def _extract_detail(raw: dict) -> dict:
    uw = _unwrap(raw) if isinstance(raw, dict) else {}
    return {
        "progress": _g(uw, "progress", "completionRatio", "completionRate", "percent", "completion"),
        "cpm":      _g(uw, "cpm", "speed", "checkSpeed", "checksPerMinute"),
        "hits":     _g(uw, "hits", "hitsCount", "good", "goodCount", "success"),
        "custom":   _g(uw, "custom", "customCount", "captured"),
        "total":    _g(uw, "total", "totalChecks", "checked", "dataTested"),
        "bad":      _g(uw, "bad", "badCount", "fail", "failed", "toCheck"),
        "name":     _g(uw, "name", "jobName", "configName"),
        "status":   _g(uw, "status", "state"),
        "_keys":    list(uw.keys()) if isinstance(uw, dict) else [],
    }


async def fetch_ob_status() -> dict:
    headers, auth_label = await get_auth_headers()

    if not headers:
        return {
            "error": (
                "❌ *فشلت المصادقة مع OpenBullet*\n\n"
                f"🔗 `{OPENBULLET_URL}`\n"
                f"🔑 `{(OPENBULLET_API_KEY or '')[:8]}...`\n\n"
                "⚠️ تأكد من:\n"
                "1️⃣ تشغيل OpenBullet\n"
                "2️⃣ تفعيل Admin API\n"
                "3️⃣ صحة مفتاح API"
            ),
            "jobs": [],
            "monitors": [],
            "total_all": 0,
            "auth_method": None,
        }

    base = OPENBULLET_URL.strip().rstrip("/")
    result = {
        "auth_method": auth_label,
        "jobs": [],
        "monitors": [],
        "total_all": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
            if resp.status_code != 200:
                return {**result, "error": f"قائمة العمليات: HTTP {resp.status_code}"}

            list_data = resp.json()
            all_jobs = (
                list_data.get("items", [])
                if isinstance(list_data, dict)
                else (list_data if isinstance(list_data, list) else [])
            )
            result["total_all"] = len(all_jobs)

            active_items = [
                {"id": j["id"], "name": j.get("name", "بدون اسم"), "status": j.get("status")}
                for j in all_jobs
                if isinstance(j, dict) and j.get("id") is not None and _is_active(j.get("status"))
            ]

            for item in active_items:
                detail = {
                    "id": item["id"],
                    "name": item["name"],
                    "status": item["status"],
                    "progress": None, "cpm": None, "hits": None,
                    "custom": None, "total": None, "bad": None,
                }
                try:
                    resp2 = await client.get(f"{base}/api/v1/job/{item['id']}", headers=headers)
                    if resp2.status_code == 200:
                        raw = resp2.json()
                        ext = _extract_detail(raw)
                        detail.update({k: ext[k] for k in ["progress", "cpm", "hits", "custom", "total", "bad"]})
                        if ext["name"]:
                            detail["name"] = ext["name"]
                except Exception as e:
                    logger.error(f"❌ Job {item['id']} detail error: {e}")

                result["jobs"].append(detail)

            try:
                resp3 = await client.get(f"{base}/api/v1/jobmonitor/all", headers=headers)
                if resp3.status_code == 200:
                    mon_data = resp3.json()
                    monitors = (
                        mon_data.get("items", [])
                        if isinstance(mon_data, dict)
                        else (mon_data if isinstance(mon_data, list) else [])
                    )
                    for m in monitors:
                        if isinstance(m, dict) and _is_active(m.get("status")):
                            ext = _extract_detail(m)
                            result["monitors"].append({
                                "name": ext["name"] or "مراقب",
                                "status": ext["status"],
                                "progress": ext["progress"],
                                "cpm": ext["cpm"],
                                "hits": ext["hits"],
                                "custom": ext["custom"],
                                "total": ext["total"],
                                "bad": ext["bad"],
                            })
            except Exception as e:
                logger.error(f"❌ Monitors error: {e}")

    except Exception as e:
        logger.error(f"❌ fetch_ob_status error: {e}", exc_info=True)
        return {**result, "error": f"خطأ عام: {e}"}

    return result


def format_ob_message(ob_data: dict) -> str:
    if "error" in ob_data and not ob_data.get("jobs"):
        return f"❌ *خطأ:*\n{ob_data['error']}"

    auth_line = f"🔐 *المصادقة:* `{ob_data['auth_method']}`\n" if ob_data.get("auth_method") else ""

    jobs = ob_data.get("jobs", [])
    monitors = ob_data.get("monitors", [])
    total_all = ob_data.get("total_all", 0)
    total_active = len(jobs) + len(monitors)

    if total_active == 0:
        return (
            "💤 *حالة الـ Mainframe:* `خامل (IDLE)`\n\n"
            f"{auth_line}"
            f"📊 *إجمالي العمليات:* `{total_all}`\n"
            f"🟢 *النشطة:* `0`\n\n"
            "_لا توجد عمليات نشطة حالياً._"
        )

    lines = [
        "⚙️ *「 شاشة مراقبة OPENBULLET 」* ⚙️\n",
        auth_line,
        f"⚡ *النشطة:* `{total_active}` من `{total_all}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    name_count = {}
    for j in jobs:
        name_count[j["name"]] = name_count.get(j["name"], 0) + 1
    name_seen = {}

    for job in jobs:
        name = safe_md(job["name"])
        if name_count.get(job["name"], 0) > 1:
            name_seen[job["name"]] = name_seen.get(job["name"], 0) + 1
            name = f"{name} #{name_seen[job['name']]}"

        hits = _num(job["hits"])
        custom = _num(job["custom"])
        total = _num(job["total"])
        bad = _num(job["bad"])
        cpm = _num(job["cpm"])
        progress = resolve_progress(job["progress"])

        lines.append(f"📦 *عملية:* `{name}`")
        lines.append(f"   🆔 `{job['id']}`")
        lines.append(f"   📊 التقدم: `{progress}%`")
        if total > 0:
            lines.append(f"   📋 تم فحص: `{total}`")
            lines.append(f"   🎯 Hits: `{hits}`")
            if custom > 0:
                lines.append(f"   ⭐ Custom: `{custom}`")
            lines.append(f"   ❌ Fail: `{bad}`")
        else:
            lines.append(f"   🎯 Hits: `{hits}`")
            if custom > 0:
                lines.append(f"   ⭐ Custom: `{custom}`")
        lines.append(f"   ⚡ السرعة: `{cpm}` CPM")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    for mon in monitors:
        name = safe_md(mon.get("name", "مراقب"))
        hits = _num(mon.get("hits"))
        custom = _num(mon.get("custom"))
        total = _num(mon.get("total"))
        cpm = _num(mon.get("cpm"))
        progress = resolve_progress(mon.get("progress"))

        lines.append(f"🔄 *مراقب:* `{name}`")
        lines.append(f"   📊 التقدم: `{progress}%`")
        if total > 0:
            lines.append(f"   📋 تم فحص: `{total}`")
        lines.append(f"   🎯 Hits: `{hits}`")
        if custom > 0:
            lines.append(f"   ⭐ Custom: `{custom}`")
        lines.append(f"   ⚡ السرعة: `{cpm}` CPM")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#                    MESSAGE FORMATTERS
# ═══════════════════════════════════════════════════════════

def format_stats_message(db) -> str:
    total = db.query(Account).count()
    avail = db.query(Account).filter(Account.is_given == False).count()
    given = db.query(Account).filter(Account.is_given == True).count()
    total_users = db.query(User).count()
    banned = db.query(BannedUser).filter(BannedUser.is_active == True).count()
    active_res = db.query(Reservation).filter(Reservation.status == "active").count()

    today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    claims_today = db.query(DeliveredAccount).filter(
        DeliveredAccount.delivered_at >= today_start,
        DeliveredAccount.delivery_type == "claim"
    ).count()

    top_configs = db.query(
        Account.config_name, func.count(Account.id)
    ).filter(Account.is_given == False).group_by(Account.config_name).order_by(
        desc(func.count(Account.id))
    ).limit(5).all()

    lines = [
        "╔═══════════════════════════════════════╗",
        "║     📊  NEXUS ANALYTICS DASHBOARD   ║",
        "╠═══════════════════════════════════════╣",
        f"║  📦 إجمالي الحسابات:    `{total:>6}` ║",
        f"║  🟢 الجاهزة:            `{avail:>6}` ║",
        f"║  🔴 الموزعة:            `{given:>6}` ║",
        f"║  👥 إجمالي المستخدمين:  `{total_users:>6}` ║",
        f"║  🚫 المحظورون:          `{banned:>6}` ║",
        f"║  ⏳ الحجوزات النشطة:    `{active_res:>6}` ║",
        f"║  📈 سحوبات اليوم:       `{claims_today:>6}` ║",
        "╠═══════════════════════════════════════╣",
        "║  🏆 أكثر الأنواع المتوفرة:          ║",
    ]

    for cfg, count in top_configs:
        display = cfg[:20] + "..." if len(cfg) > 20 else cfg
        lines.append(f"║     • `{display:<22}` {count:>3} ║")

    lines.append("╚═══════════════════════════════════════╝")
    return "\n".join(lines)


def format_user_history(db, user_id: str) -> str:
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        return "❌ *لم يتم العثور على سجلات.*"

    deliveries = db.query(DeliveredAccount).filter(
        DeliveredAccount.user_id == user_id,
        DeliveredAccount.delivery_type == "claim"
    ).order_by(desc(DeliveredAccount.delivered_at)).limit(10).all()

    lines = [
        f"🕐 *「 سجل عملياتي 」* 🕐\n",
        f"👤 المستخدم: `{user.user_id}`",
        f"📅 تاريخ التسجيل: `{format_datetime(user.join_date)}`",
        f"🏆 السمعة: `{user.reputation_score}/100`",
        f"📊 إجمالي السحوبات: `{user.total_claims}`\n",
        "*آخر السحوبات:*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not deliveries:
        lines.append("_لا توجد سحوبات مسجلة._")
    else:
        for d in deliveries:
            lines.append(f"📦 `{safe_md(d.config_name or 'غير معروف')}`")
            lines.append(f"   📅 {format_datetime(d.delivered_at)}")
            lines.append("   ━━━")

    return "\n".join(lines)


def format_reservation_message(db, user_id: str) -> str:
    clean_expired_reservations(db)

    reservations = db.query(Reservation).filter(
        Reservation.user_id == user_id,
        Reservation.status == "active"
    ).all()

    if not reservations:
        return (
            "🏦 *「 نظام الحجز المؤقت 」* 🏦\n\n"
            "لا توجد حجوزات نشطة.\n"
            "يمكنك حجز حساب مؤقتاً قبل السحب."
        )

    lines = ["🏦 *「 حجوزاتك النشطة 」* 🏦\n"]

    for res in reservations:
        remaining = res.expires_at - datetime.datetime.utcnow()
        mins = int(remaining.total_seconds() / 60)
        lines.append(f"📦 `{safe_md(res.config_name)}`")
        lines.append(f"   ⏳ متبقي: `{mins} دقيقة`")
        lines.append(f"   📅 ينتهي: `{format_datetime(res.expires_at)}`")
        lines.append("   ━━━")

    return "\n".join(lines)


def format_detailed_report(db) -> str:
    now = datetime.datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - datetime.timedelta(days=7)

    total = db.query(Account).count()
    avail = db.query(Account).filter(Account.is_given == False).count()
    given = db.query(Account).filter(Account.is_given == True).count()

    today_claims = db.query(DeliveredAccount).filter(
        DeliveredAccount.delivered_at >= today_start,
        DeliveredAccount.delivery_type == "claim"
    ).count()
    week_claims = db.query(DeliveredAccount).filter(
        DeliveredAccount.delivered_at >= week_ago,
        DeliveredAccount.delivery_type == "claim"
    ).count()

    total_users = db.query(User).count()
    new_today = db.query(User).filter(User.join_date >= today_start).count()
    banned_count = db.query(BannedUser).filter(BannedUser.is_active == True).count()

    config_stats = db.query(
        Account.config_name,
        func.count(Account.id),
        func.sum(case([(Account.is_given == False, 1)], else_=0))
    ).group_by(Account.config_name).all()

    ratio = (avail / max(total, 1)) * 100

    lines = [
        "╔════════════════════════════════════════════════════╗",
        "║           📋 NEXUS DETAILED REPORT                 ║",
        f"║  {now.strftime('%Y-%m-%d %H:%M UTC'):>42} ║",
        "╠════════════════════════════════════════════════════╣",
        "║  📊 ACCOUNTS METRICS                               ║",
        f"║     Total:     {total:>6}  Available: {avail:>6}       ║",
        f"║     Given:     {given:>6}  Ratio:     {ratio:>5.1f}%      ║",
        "╠════════════════════════════════════════════════════╣",
        "║  📈 ACTIVITY                                       ║",
        f"║     Claims Today:    {today_claims:>6}                       ║",
        f"║     Claims Week:     {week_claims:>6}                       ║",
        f"║     New Users Today: {new_today:>6}                       ║",
        "╠════════════════════════════════════════════════════╣",
        "║  👥 USER BASE                                      ║",
        f"║     Total Users:     {total_users:>6}                       ║",
        f"║     Banned:          {banned_count:>6}                       ║",
        "╠════════════════════════════════════════════════════╣",
        "║  📦 CONFIG INVENTORY                               ║",
    ]

    for cfg, total_cfg, avail_cfg in config_stats[:8]:
        display = cfg[:18] + ".." if len(cfg) > 18 else cfg
        avail_int = int(avail_cfg) if avail_cfg else 0
        lines.append(f"║     {display:>20}  {avail_int:>4}/{total_cfg:<4}              ║")

    lines.append("╚════════════════════════════════════════════════════╝")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#                    WEBHOOK: HIT RECEIVER
# ═══════════════════════════════════════════════════════════

@app.post("/webhook/hit")
async def receive_hit(request: Request):
    db = SessionLocal()
    try:
        data = await request.json()

        config_name = "UNKNOWN"
        for key in ("config", "configName", "ConfigName"):
            if data.get(key):
                config_name = data.get(key)
                break
        if config_name == "UNKNOWN" and data.get("variables"):
            for var in data.get("variables", []):
                if isinstance(var, dict) and var.get("name") in ("Config.Name", "config", "Config"):
                    config_name = var.get("value", "UNKNOWN")
                    break

        config_name = os.path.basename(str(config_name)).replace(".anom", "").replace(".opk", "").strip()
        account_data = str(data.get("data") or data.get("account") or "NO_DATA").strip()
        captured_data = str(
            data.get("captured") or data.get("capturedData") or data.get("variables") or "NO_CAPTURED_DATA"
        ).strip()[:5000]

        quality = 100
        source_job = data.get("jobName") or data.get("job") or None

        try:
            if db.query(Account).filter(Account.account_data == account_data).first():
                return {"status": "ignored", "reason": "duplicate"}
        except Exception as e:
            logger.warning(f"Duplicate check error: {e}")

        try:
            db.add(Account(
                config_name=config_name or "UNKNOWN",
                account_data=account_data,
                captured_data=captured_data,
                is_given=False,
                source_job=source_job,
                quality_score=quality,
            ))
            db.commit()
        except sqlalchemy_exc.IntegrityError:
            db.rollback()
            return {"status": "ignored", "reason": "duplicate (race condition)"}

        log_audit(db, None, "hit_received", f"New account received: {config_name}", request)

        avail_count = db.query(Account).filter(Account.is_given == False).count()
        if NOTIFY_ADMINS_ON_LOW_STOCK and avail_count <= LOW_STOCK_THRESHOLD:
            for admin_id in ADMIN_IDS:
                await tg_send(admin_id,
                    f"⚠️ *تنبيه المخزن المنخفض* ⚠️\n\n"
                    f"📦 النوع: `{safe_md(config_name)}`\n"
                    f"🟢 المتاح: `{avail_count}`\n"
                    f"⚠️ الحد الأدنى: `{LOW_STOCK_THRESHOLD}`\n\n"
                    f"_الحسابات تقترب من النفاد!_"
                )

        return {"status": "success", "config": config_name}
    except Exception as e:
        logger.error(f"/webhook/hit error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#          ★★★ WEBHOOK: TELEGRAM (الكود المُصلح كاملاً) ★★★
# ═══════════════════════════════════════════════════════════

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()

        # ========== CALLBACK QUERIES ==========
        if "callback_query" in payload:
            cb = payload["callback_query"]
            callback_id = cb["id"]
            chat_id = str(cb["message"]["chat"]["id"])
            message_id = cb["message"]["message_id"]
            data = cb["data"]
            is_admin = chat_id in ADMIN_IDS
            user_info = cb.get("from", {})

            # تسجيل/تحديث المستخدم
            get_or_create_user(db, chat_id, user_info)

            # ────────────── claim_cfg: ──────────────
            if data.startswith("claim_cfg:"):
                try:
                    cfg_h = data.split("claim_cfg:", 1)[1]
                    all_cfgs = db.query(Account.config_name).filter(
                        Account.is_given == False
                    ).distinct().all()
                    selected = None
                    for (name,) in all_cfgs:
                        if config_hash(name) == cfg_h:
                            selected = name
                            break

                    if not selected:
                        await tg_answer(callback_id, "❌ لم يتم العثور على هذا النوع!", show_alert=True)
                        return {"status": "ok"}

                    banned, reason = is_user_banned(db, chat_id)
                    if banned:
                        await tg_answer(callback_id, f"🚫 أنت محظور!\nالسبب: {reason}", show_alert=True)
                        return {"status": "ok"}

                    if not rate_limiter.is_claim_allowed(chat_id):
                        await tg_answer(callback_id, "⏳ تجاوزت الحد المسموح (5/ساعة)", show_alert=True)
                        return {"status": "ok"}

                    last_delivery = db.query(DeliveredAccount).filter(
                        DeliveredAccount.user_id == chat_id,
                        DeliveredAccount.delivery_type == "claim"
                    ).order_by(desc(DeliveredAccount.delivered_at)).first()

                    if last_delivery and last_delivery.delivered_at:
                        cooldown_end = last_delivery.delivered_at + datetime.timedelta(
                            seconds=CLAIM_COOLDOWN_SECONDS
                        )
                        if datetime.datetime.utcnow() < cooldown_end:
                            remaining = cooldown_end - datetime.datetime.utcnow()
                            hours = int(remaining.total_seconds() / 3600)
                            mins = int((remaining.total_seconds() % 3600) / 60)
                            await tg_answer(
                                callback_id,
                                f"⏳ انتظر: {hours}س {mins}د قبل السحب التالي",
                                show_alert=True
                            )
                            return {"status": "ok"}

                    account = (
                        db.query(Account)
                        .filter(Account.config_name == selected, Account.is_given == False)
                        .with_for_update()
                        .first()
                    )

                    if not account:
                        await tg_answer(callback_id, "😔 نفدت الحسابات من هذا النوع!", show_alert=True)
                        return {"status": "ok"}

                    account.is_given = True
                    account.given_at = datetime.datetime.utcnow()
                    account.given_to = chat_id

                    db.add(DeliveredAccount(
                        user_id=chat_id,
                        account_id=account.id,
                        config_name=account.config_name,
                        delivery_type="claim"
                    ))

                    user = get_or_create_user(db, chat_id, user_info)
                    user.total_claims += 1
                    user.last_activity = datetime.datetime.utcnow()

                    db.commit()
                    rate_limiter.record_claim(chat_id)
                    log_audit(db, chat_id, "account_claimed",
                              f"Claimed {account.config_name}", request)

                    await tg_edit(chat_id, message_id,
                        f"🌌 *⚡ 「 تم السحب بنجاح 」 ⚡* 🌌\n\n"
                        f"📦 *النوع:* `{safe_md(account.config_name)}`\n\n"
                        f"👤 *الحساب:*\n`{safe_md(account.account_data)}`\n\n"
                        f"⚙️ *المستخرج:*\n`{safe_md(account.captured_data)}`\n\n"
                        f"🔒 _STATUS: DELIVERED_\n"
                        f"⏳ السحب التالي بعد: {CLAIM_COOLDOWN_SECONDS // 3600} ساعة",
                        reply_markup=None
                    )
                except Exception as e:
                    logger.error(f"claim_cfg error: {e}", exc_info=True)
                    db.rollback()
                    await tg_answer(callback_id, f"❌ خطأ: {str(e)[:100]}", show_alert=True)
                return {"status": "ok"}

            # ────────────── confirm_reserve: ──────────────
            if data.startswith("confirm_reserve:"):
                try:
                    cfg_h = data.split("confirm_reserve:", 1)[1]
                    all_cfgs = db.query(Account.config_name).filter(
                        Account.is_given == False
                    ).distinct().all()
                    selected = None
                    for (name,) in all_cfgs:
                        if config_hash(name) == cfg_h:
                            selected = name
                            break

                    if not selected:
                        await tg_answer(callback_id, "❌ لم يتم العثور!", show_alert=True)
                        return {"status": "ok"}

                    existing = db.query(Reservation).filter(
                        Reservation.user_id == chat_id,
                        Reservation.status == "active"
                    ).first()
                    if existing:
                        await tg_answer(callback_id, "⏳ لديك حجز نشط!", show_alert=True)
                        return {"status": "ok"}

                    account = db.query(Account).filter(
                        Account.config_name == selected,
                        Account.is_given == False
                    ).with_for_update().first()

                    if not account:
                        await tg_answer(callback_id, "😔 لا توجد حسابات!", show_alert=True)
                        return {"status": "ok"}

                    account.is_given = True
                    account.given_to = chat_id

                    expires = datetime.datetime.utcnow() + datetime.timedelta(
                        minutes=RESERVATION_MINUTES
                    )

                    reservation = Reservation(
                        user_id=chat_id,
                        config_name=selected,
                        account_id=account.id,
                        expires_at=expires,
                        status="active"
                    )
                    db.add(reservation)
                    db.commit()

                    await tg_edit(chat_id, message_id,
                        f"✅ *تم الحجز بنجاح!*\n\n"
                        f"📦 النوع: `{safe_md(selected)}`\n"
                        f"⏳ مدة الحجز: `{RESERVATION_MINUTES}` دقيقة\n"
                        f"📅 ينتهي: `{format_datetime(expires)}`\n\n"
                        f"_اضغط \"تأكيد السحب\" لاستلام الحساب._",
                        reply_markup=get_reservation_keyboard(cfg_h)
                    )
                    log_audit(db, chat_id, "reservation_created",
                              f"Reserved {selected}", request)
                except Exception as e:
                    logger.error(f"confirm_reserve error: {e}", exc_info=True)
                    db.rollback()
                    await tg_answer(callback_id, f"❌ خطأ: {str(e)[:100]}", show_alert=True)
                return {"status": "ok"}

            # ────────────── cancel_reserve: ──────────────
            if data.startswith("cancel_reserve:"):
                try:
                    res = db.query(Reservation).filter(
                        Reservation.user_id == chat_id,
                        Reservation.status == "active"
                    ).first()
                    if res:
                        res.status = "cancelled"
                        account = db.query(Account).filter(
                            Account.id == res.account_id
                        ).first()
                        if account:
                            account.is_given = False
                            account.given_to = None
                        db.commit()
                        await tg_edit(chat_id, message_id,
                            "❌ *تم إلغاء الحجز.*\nالحساب تم إعادته للمخزن.",
                            reply_markup=None
                        )
                    else:
                        await tg_edit(chat_id, message_id,
                            "❌ لا يوجد حجز نشط.", reply_markup=None)
                except Exception as e:
                    logger.error(f"cancel_reserve error: {e}")
                    db.rollback()
                await tg_answer(callback_id, "تم الإلغاء")
                return {"status": "ok"}

            # ────────────── ADMIN: reset_delivered ──────────────
            if data == "reset_delivered" and is_admin:
                try:
                    count = db.query(DeliveredAccount).count()
                    db.query(DeliveredAccount).delete()
                    db.commit()
                    log_audit(db, chat_id, "reset_delivered",
                              f"Deleted {count} records", request)
                    await tg_edit(chat_id, message_id,
                        f"🧹 *تم تصفير سجل التوزيع*\n\n"
                        f"📊 تم حذف `{count}` سجل",
                        reply_markup=get_admin_keyboard()
                    )
                except Exception as e:
                    db.rollback()
                    await tg_answer(callback_id, f"❌ خطأ: {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── ADMIN: clear_accounts ──────────────
            if data == "clear_accounts" and is_admin:
                try:
                    count = db.query(Account).count()
                    db.query(Account).delete()
                    db.query(DeliveredAccount).delete()
                    db.query(Reservation).delete()
                    db.commit()
                    log_audit(db, chat_id, "clear_accounts",
                              f"Cleared {count} accounts", request)
                    await tg_edit(chat_id, message_id,
                        f"🚨 *تم تصفير المخزن بالكامل*\n\n"
                        f"📦 تم حذف `{count}` حساب",
                        reply_markup=get_admin_keyboard()
                    )
                except Exception as e:
                    db.rollback()
                    await tg_answer(callback_id, f"❌ خطأ: {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── ADMIN: refresh_admin_stats ──────────────
            if data == "refresh_admin_stats" and is_admin:
                try:
                    stats_msg = format_stats_message(db)
                    await tg_edit(chat_id, message_id, stats_msg,
                                  reply_markup=get_admin_keyboard())
                except Exception as e:
                    logger.error(f"refresh stats error: {e}")
                await tg_answer(callback_id, "🔄 تم التحديث")
                return {"status": "ok"}

            # ────────────── ADMIN: detailed_report ──────────────
            if data == "detailed_report" and is_admin:
                try:
                    report = format_detailed_report(db)
                    await tg_edit(chat_id, message_id, report,
                                  reply_markup=get_admin_keyboard())
                except Exception as e:
                    logger.error(f"detailed_report error: {e}")
                await tg_answer(callback_id, "📊 تم التحميل")
                return {"status": "ok"}

            # ────────────── ADMIN: scan_inventory ──────────────
            if data == "scan_inventory" and is_admin:
                try:
                    clean_expired_reservations(db)
                    total = db.query(Account).count()
                    avail = db.query(Account).filter(Account.is_given == False).count()
                    given = db.query(Account).filter(Account.is_given == True).count()
                    reserved = db.query(Reservation).filter(
                        Reservation.status == "active"
                    ).count()
                    orphaned = db.query(Account).filter(
                        Account.is_given == True,
                        Account.given_to == None
                    ).count()

                    # Fix orphaned accounts
                    orphaned_fixed = 0
                    orphans = db.query(Account).filter(
                        Account.is_given == True,
                        Account.given_to == None
                    ).all()
                    for a in orphans:
                        a.is_given = False
                        orphaned_fixed += 1
                    if orphaned_fixed > 0:
                        db.commit()

                    msg = (
                        f"🔍 *نتيجة فحص المخزن*\n\n"
                        f"📦 الإجمالي: `{total}`\n"
                        f"🟢 متاح: `{avail}`\n"
                        f"🔴 موزع: `{given}`\n"
                        f"⏳ محجوز: `{reserved}`\n"
                        f"⚠️ يتيم (تم إصلاحه): `{orphaned_fixed}`\n\n"
                        f"_حالة المخزن: {'✅ سليم' if orphaned_fixed == 0 else '🔧 تم الإصلاح'}_"
                    )
                    await tg_edit(chat_id, message_id, msg,
                                  reply_markup=get_admin_keyboard())
                    log_audit(db, chat_id, "scan_inventory",
                              f"Found {orphaned} orphaned, fixed {orphaned_fixed}", request)
                except Exception as e:
                    logger.error(f"scan_inventory error: {e}")
                    db.rollback()
                await tg_answer(callback_id, "🔍 تم الفحص")
                return {"status": "ok"}

            # ────────────── ADMIN: banned_users ──────────────
            if data == "banned_users" and is_admin:
                try:
                    bans = db.query(BannedUser).filter(
                        BannedUser.is_active == True
                    ).order_by(desc(BannedUser.banned_at)).limit(20).all()

                    if not bans:
                        msg = "✅ *لا يوجد مستخدمون محظورون حالياً.*"
                    else:
                        lines = ["⚠️ *قائمة المحظورين النشطين:*\n"]
                        for b in bans:
                            exp = f" (ينتهي: {format_datetime(b.expires_at)})" if b.expires_at else " (دائم)"
                            lines.append(
                                f"🚫 `{b.user_id}`\n"
                                f"   السبب: `{safe_md(b.reason or 'غير محدد')}`\n"
                                f"   بتاريخ: `{format_datetime(b.banned_at)}`{exp}\n"
                                f"   ━━━"
                            )
                        msg = "\n".join(lines)

                    await tg_edit(chat_id, message_id, msg,
                                  reply_markup=get_admin_keyboard())
                except Exception as e:
                    logger.error(f"banned_users error: {e}")
                await tg_answer(callback_id, "⚠️ تم التحميل")
                return {"status": "ok"}

            # ────────────── ADMIN: broadcast ──────────────
            if data == "broadcast" and is_admin:
                pending_broadcasts[chat_id] = True
                await tg_edit(chat_id, message_id,
                    "📢 *إرسال إشعار عام*\n\n"
                    "أرسل نص الرسالة الآن:\n"
                    "_سيتم إرسالها لجميع المستخدمين._",
                    reply_markup=get_cancel_keyboard()
                )
                await tg_answer(callback_id, "أرسل نص الرسالة")
                return {"status": "ok"}

            # ────────────── ADMIN: settings ──────────────
            if data == "settings" and is_admin:
                msg = (
                    "⚙️ *الإعدادات الحالية*\n\n"
                    f"⏳ مدة الانتظار بين السحوبات: `{CLAIM_COOLDOWN_SECONDS // 3600}` ساعة\n"
                    f"🏦 مدة الحجز: `{RESERVATION_MINUTES}` دقيقة\n"
                    f"📊 حد السحوبات: `{RATE_LIMIT_CLAIMS}` / ساعة\n"
                    f"⚠️ حد التنبيه المنخفض: `{LOW_STOCK_THRESHOLD}` حساب\n"
                    f"🔔 تنبيه المخزن: `{'مفعّل' if NOTIFY_ADMINS_ON_LOW_STOCK else 'معطّل'}`\n\n"
                    "_لتعديل: غيّر متغيرات البيئة في Render._"
                )
                await tg_edit(chat_id, message_id, msg,
                              reply_markup=get_admin_keyboard())
                await tg_answer(callback_id, "⚙️")
                return {"status": "ok"}

            # ────────────── USER MGMT: ban_user ──────────────
            if data == "ban_user" and is_admin:
                pending_bans[chat_id] = True
                await tg_edit(chat_id, message_id,
                    "🚫 *حظر مستخدم*\n\n"
                    "أرسل معرف المستخدم (user\\_id):\n"
                    "_مثال: 123456789_",
                    reply_markup=get_cancel_keyboard()
                )
                await tg_answer(callback_id, "أرسل المعرف")
                return {"status": "ok"}

            # ────────────── USER MGMT: unban_user ──────────────
            if data == "unban_user" and is_admin:
                pending_unbans[chat_id] = True
                await tg_edit(chat_id, message_id,
                    "✅ *فك حظر مستخدم*\n\n"
                    "أرسل معرف المستخدم (user\\_id):",
                    reply_markup=get_cancel_keyboard()
                )
                await tg_answer(callback_id, "أرسل المعرف")
                return {"status": "ok"}

            # ────────────── USER MGMT: list_banned ──────────────
            if data == "list_banned" and is_admin:
                try:
                    bans = db.query(BannedUser).filter(
                        BannedUser.is_active == True
                    ).order_by(desc(BannedUser.banned_at)).limit(15).all()
                    if not bans:
                        msg = "✅ *لا يوجد محظورون.*"
                    else:
                        lines = ["🚫 *المحظورون:*\n"]
                        for b in bans:
                            lines.append(f"• `{b.user_id}` — {safe_md(b.reason or '?')}")
                        msg = "\n".join(lines)
                    await tg_edit(chat_id, message_id, msg,
                                  reply_markup=get_user_management_keyboard())
                except Exception as e:
                    logger.error(f"list_banned error: {e}")
                await tg_answer(callback_id, "📋")
                return {"status": "ok"}

            # ────────────── USER MGMT: list_users ──────────────
            if data == "list_users" and is_admin:
                try:
                    users = db.query(User).order_by(
                        desc(User.last_activity)
                    ).limit(15).all()
                    if not users:
                        msg = "👥 *لا يوجد مستخدمون مسجلون.*"
                    else:
                        lines = [f"👥 *المستخدمون (آخر 15):*\n"]
                        for u in users:
                            name = safe_md(u.first_name or u.username or "بدون اسم")
                            lines.append(
                                f"• `{u.user_id}` {name}\n"
                                f"  سحوبات: {u.total_claims} | {time_ago(u.last_activity)}"
                            )
                        msg = "\n".join(lines)
                    await tg_edit(chat_id, message_id, msg,
                                  reply_markup=get_user_management_keyboard())
                except Exception as e:
                    logger.error(f"list_users error: {e}")
                await tg_answer(callback_id, "👥")
                return {"status": "ok"}

            # ────────────── USER MGMT: search_user ──────────────
            if data == "search_user" and is_admin:
                pending_user_search[chat_id] = True
                await tg_edit(chat_id, message_id,
                    "🔍 *بحث عن مستخدم*\n\n"
                    "أرسل معرف المستخدم (user\\_id):",
                    reply_markup=get_cancel_keyboard()
                )
                await tg_answer(callback_id, "أرسل المعرف")
                return {"status": "ok"}

            # ────────────── USER MGMT: user_stats ──────────────
            if data == "user_stats" and is_admin:
                try:
                    total_users = db.query(User).count()
                    total_claims = db.query(DeliveredAccount).filter(
                        DeliveredAccount.delivery_type == "claim"
                    ).count()
                    avg_claims = total_claims / max(total_users, 1)
                    top_users = db.query(User).order_by(
                        desc(User.total_claims)
                    ).limit(5).all()

                    lines = [
                        "📊 *إحصائيات المستخدمين*\n\n",
                        f"👥 الإجمالي: `{total_users}`",
                        f"📦 إجمالي السحوبات: `{total_claims}`",
                        f"📈 متوسط السحوبات/مستخدم: `{avg_claims:.1f}`\n",
                        "*🏆 أكثر 5 مستخدمين نشاطاً:*",
                    ]
                    for u in top_users:
                        name = safe_md(u.first_name or u.username or u.user_id)
                        lines.append(f"  • {name}: `{u.total_claims}` سحب")

                    await tg_edit(chat_id, message_id, "\n".join(lines),
                                  reply_markup=get_user_management_keyboard())
                except Exception as e:
                    logger.error(f"user_stats error: {e}")
                await tg_answer(callback_id, "📊")
                return {"status": "ok"}

            # ────────────── NAVIGATION: back_to_admin ──────────────
            if data == "back_to_admin" and is_admin:
                try:
                    stats_msg = format_stats_message(db)
                    await tg_edit(chat_id, message_id, stats_msg,
                                  reply_markup=get_admin_keyboard())
                except Exception as e:
                    logger.error(f"back_to_admin error: {e}")
                await tg_answer(callback_id, "🔙")
                return {"status": "ok"}

            # ────────────── EXPORT: export_json ──────────────
            if data == "export_json" and is_admin:
                try:
                    accounts = db.query(Account).all()
                    export_data = []
                    for a in accounts:
                        export_data.append({
                            "id": a.id,
                            "config_name": a.config_name,
                            "account_data": a.account_data,
                            "captured_data": a.captured_data,
                            "is_given": a.is_given,
                            "given_to": a.given_to,
                            "quality_score": a.quality_score,
                        })

                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.json', delete=False, prefix='export_'
                    ) as f:
                        json.dump(export_data, f, ensure_ascii=False, indent=2)
                        tmp_path = f.name

                    await tg_send_document(chat_id, tmp_path,
                        f"💾 تصدير JSON — `{len(export_data)}` حساب")
                    os.unlink(tmp_path)
                    log_audit(db, chat_id, "export_json",
                              f"Exported {len(export_data)} accounts", request)
                except Exception as e:
                    logger.error(f"export_json error: {e}")
                    await tg_answer(callback_id, f"❌ {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── EXPORT: export_csv ──────────────
            if data == "export_csv" and is_admin:
                try:
                    accounts = db.query(Account).all()
                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.csv', delete=False, prefix='export_',
                        newline='', encoding='utf-8'
                    ) as f:
                        writer = csv.writer(f)
                        writer.writerow(['id', 'config_name', 'account_data',
                                         'captured_data', 'is_given', 'given_to'])
                        for a in accounts:
                            writer.writerow([
                                a.id, a.config_name, a.account_data,
                                a.captured_data, a.is_given, a.given_to
                            ])
                        tmp_path = f.name

                    await tg_send_document(chat_id, tmp_path,
                        f"📄 تصدير CSV — `{len(accounts)}` حساب")
                    os.unlink(tmp_path)
                    log_audit(db, chat_id, "export_csv",
                              f"Exported {len(accounts)} accounts", request)
                except Exception as e:
                    logger.error(f"export_csv error: {e}")
                    await tg_answer(callback_id, f"❌ {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── EXPORT: export_audit ──────────────
            if data == "export_audit" and is_admin:
                try:
                    logs = db.query(AuditLog).order_by(
                        desc(AuditLog.timestamp)
                    ).limit(500).all()
                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.json', delete=False, prefix='audit_'
                    ) as f:
                        audit_data = [{
                            "time": format_datetime(l.timestamp),
                            "user_id": l.user_id,
                            "action": l.action,
                            "details": l.details,
                            "ip": l.ip_address,
                        } for l in logs]
                        json.dump(audit_data, f, ensure_ascii=False, indent=2)
                        tmp_path = f.name

                    await tg_send_document(chat_id, tmp_path,
                        f"📋 سجل العمليات — `{len(audit_data)}` سجل")
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.error(f"export_audit error: {e}")
                    await tg_answer(callback_id, f"❌ {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── EXPORT: full_backup ──────────────
            if data == "full_backup" and is_admin:
                try:
                    backup = {
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "accounts": [{
                            "id": a.id, "config_name": a.config_name,
                            "account_data": a.account_data,
                            "captured_data": a.captured_data,
                            "is_given": a.is_given, "given_to": a.given_to,
                            "quality_score": a.quality_score, "tags": a.tags,
                        } for a in db.query(Account).all()],
                        "users": [{
                            "user_id": u.user_id, "username": u.username,
                            "first_name": u.first_name, "total_claims": u.total_claims,
                        } for u in db.query(User).all()],
                        "banned": [{
                            "user_id": b.user_id, "reason": b.reason,
                            "expires_at": format_datetime(b.expires_at),
                        } for b in db.query(BannedUser).filter(
                            BannedUser.is_active == True
                        ).all()],
                    }

                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.json', delete=False, prefix='backup_'
                    ) as f:
                        json.dump(backup, f, ensure_ascii=False, indent=2)
                        tmp_path = f.name

                    await tg_send_document(chat_id, tmp_path,
                        f"🔄 نسخ احتياطي كامل — `{len(backup['accounts'])}` حساب")
                    os.unlink(tmp_path)
                    log_audit(db, chat_id, "full_backup", "Full backup created", request)
                except Exception as e:
                    logger.error(f"full_backup error: {e}")
                    await tg_answer(callback_id, f"❌ {e}", show_alert=True)
                return {"status": "ok"}

            # ────────────── cancel_action ──────────────
            if data == "cancel_action":
                pending_broadcasts.pop(chat_id, None)
                pending_bans.pop(chat_id, None)
                pending_unbans.pop(chat_id, None)
                pending_user_search.pop(chat_id, None)

                if is_admin:
                    stats_msg = format_stats_message(db)
                    await tg_edit(chat_id, message_id, stats_msg,
                                  reply_markup=get_admin_keyboard())
                else:
                    await tg_delete_message(chat_id, message_id)
                await tg_answer(callback_id, "تم الإلغاء")
                return {"status": "ok"}

            # ── Unknown callback ──
            await tg_answer(callback_id, "⚠️ إجراء غير معروف")
            return {"status": "ok"}

        # ========== TEXT MESSAGES ==========
        elif "message" in payload:
            msg = payload["message"]
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", "").strip()
            is_admin = chat_id in ADMIN_IDS
            user_info = msg.get("from", {})

            # تسجيل/تحديث المستخدم
            get_or_create_user(db, chat_id, user_info)

            # ── /start ──
            if text == "/start":
                welcome = (
                    "🌌 *⚡ CYBERPUNK DISTRIBUTOR ⚡* 🌌\n\n"
                    "مرحباً بك في نظام التوزيع المتقدم.\n\n"
                    "⚡ *سحب حساب جديد* — احصل على حساب فوراً\n"
                    "📡 *إحصائيات المخزن* — عرض حالة المخزون\n"
                    "🤖 *عمليات أوبن بلوت* — مراقبة العمليات الحية\n"
                    "📊 *إحصائيات متقدمة* — تحليلات مفصلة\n"
                    "🕐 *سجل عملياتي* — سجل سحوباتك\n"
                    "🏦 *حجز مؤقت* — احجز حساباً مؤقتاً\n"
                )
                await tg_send(chat_id, welcome,
                              reply_markup=get_main_keyboard(is_admin))
                log_audit(db, chat_id, "start", "User started the bot", request)
                return {"status": "ok"}

            # ── سحب حساب جديد ──
            if text == "⚡ 🧬 سحب حساب جديد 🧬 ⚡":
                try:
                    banned, reason = is_user_banned(db, chat_id)
                    if banned:
                        await tg_send(chat_id,
                            f"🚫 *أنت محظور!*\nالسبب: `{safe_md(reason)}`")
                        return {"status": "ok"}

                    clean_expired_reservations(db)

                    configs = db.query(
                        Account.config_name, func.count(Account.id)
                    ).filter(
                        Account.is_given == False
                    ).group_by(Account.config_name).all()

                    if not configs:
                        await tg_send(chat_id,
                            "😔 *المخزن فارغ حالياً*\n\n"
                            "_لا توجد حسابات متاحة. جرب لاحقاً._")
                        return {"status": "ok"}

                    buttons = []
                    for cfg_name, count in configs:
                        display = cfg_name[:30] + "..." if len(cfg_name) > 30 else cfg_name
                        buttons.append([{
                            "text": f"📦 {display} ({count})",
                            "callback_data": f"claim_cfg:{config_hash(cfg_name)}"
                        }])

                    await tg_send(chat_id,
                        "⚡ *「 اختر نوع الحساب 」* ⚡\n\n"
                        f"🟢 `{sum(c for _, c in configs)}` حساب متاح\n"
                        f"📦 `{len(configs)}` أنواع مختلفة",
                        reply_markup={"inline_keyboard": buttons}
                    )
                except Exception as e:
                    logger.error(f"Claim menu error: {e}", exc_info=True)
                    await tg_send(chat_id, f"❌ خطأ: {str(e)[:200]}")
                return {"status": "ok"}

            # ── إحصائيات المخزن ──
            if text == "📡 🌐 إحصائيات المخزن 🌐 📡":
                try:
                    stats_msg = format_stats_message(db)
                    await tg_send(chat_id, stats_msg)
                except Exception as e:
                    logger.error(f"Stats error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في تحميل الإحصائيات")
                return {"status": "ok"}

            # ── عمليات أوبن بلوت ──
            if text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
                try:
                    ob_data = await fetch_ob_status()
                    ob_msg = format_ob_message(ob_data)
                    await tg_send(chat_id, ob_msg)
                except Exception as e:
                    logger.error(f"OB status error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في الاتصال بـ OpenBullet")
                return {"status": "ok"}

            # ── إحصائيات متقدمة ──
            if text == "📊 📈 إحصائيات متقدمة 📈 📊":
                try:
                    report = format_detailed_report(db)
                    await tg_send(chat_id, report)
                except Exception as e:
                    logger.error(f"Advanced stats error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في تحميل التقرير")
                return {"status": "ok"}

            # ── سجل عملياتي ──
            if text == "🕐 📜 سجل عملياتي 📜 🕐":
                try:
                    history = format_user_history(db, chat_id)
                    await tg_send(chat_id, history)
                except Exception as e:
                    logger.error(f"History error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في تحميل السجل")
                return {"status": "ok"}

            # ── حجز مؤقت ──
            if text == "🏦 ⏳ حجز مؤقت ⏳ 🏦":
                try:
                    banned, reason = is_user_banned(db, chat_id)
                    if banned:
                        await tg_send(chat_id,
                            f"🚫 *أنت محظور!*\nالسبب: `{safe_md(reason)}`")
                        return {"status": "ok"}

                    res_msg = format_reservation_message(db, chat_id)

                    configs = db.query(
                        Account.config_name, func.count(Account.id)
                    ).filter(Account.is_given == False).group_by(
                        Account.config_name
                    ).all()

                    if configs:
                        buttons = []
                        for cfg_name, count in configs:
                            display = cfg_name[:30] + "..." if len(cfg_name) > 30 else cfg_name
                            buttons.append([{
                                "text": f"🏦 حجز {display} ({count})",
                                "callback_data": f"confirm_reserve:{config_hash(cfg_name)}"
                            }])

                        await tg_send(chat_id, res_msg,
                                      reply_markup={"inline_keyboard": buttons})
                    else:
                        await tg_send(chat_id,
                            res_msg + "\n\n_لا توجد حسابات متاحة للحجز._")
                except Exception as e:
                    logger.error(f"Reservation error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في نظام الحجز")
                return {"status": "ok"}

            # ── لوحة تحكم المطور ──
            if text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
                try:
                    stats_msg = format_stats_message(db)
                    await tg_send(chat_id, stats_msg,
                                  reply_markup=get_admin_keyboard())
                    log_audit(db, chat_id, "admin_panel", "Opened admin panel", request)
                except Exception as e:
                    logger.error(f"Admin panel error: {e}", exc_info=True)
                    await tg_send(chat_id, "❌ خطأ في فتح لوحة التحكم")
                return {"status": "ok"}

            # ── إدارة المستخدمين ──
            if text == "📋 🔐 إدارة المستخدمين 🔐 📋" and is_admin:
                try:
                    await tg_send(chat_id,
                        "🔐 *「 إدارة المستخدمين 」* 🔐\n\n"
                        "اختر العملية:",
                        reply_markup=get_user_management_keyboard()
                    )
                except Exception as e:
                    logger.error(f"User mgmt error: {e}")
                return {"status": "ok"}

            # ── تصدير/نسخ احتياطي ──
            if text == "💾 📤 تصدير/نسخ احتياطي 📤 💾" and is_admin:
                try:
                    await tg_send(chat_id,
                        "💾 *「 التصدير والنسخ الاحتياطي 」* 💾\n\n"
                        "اختر نوع التصدير:",
                        reply_markup=get_backup_keyboard()
                    )
                except Exception as e:
                    logger.error(f"Backup error: {e}")
                return {"status": "ok"}

            # ── Pending: Broadcast message ──
            if chat_id in pending_broadcasts:
                pending_broadcasts.pop(chat_id, None)
                try:
                    users = db.query(User).all()
                    sent = 0
                    failed = 0
                    for u in users:
                        try:
                            await tg_send(u.user_id, text)
                            sent += 1
                        except Exception:
                            failed += 1

                    await tg_send(chat_id,
                        f"📢 *نتيجة الإشعار العام*\n\n"
                        f"✅ تم الإرسال: `{sent}`\n"
                        f"❌ فشل: `{failed}`\n"
                        f"📊 الإجمالي: `{len(users)}`")
                    log_audit(db, chat_id, "broadcast",
                              f"Sent to {sent} users, {failed} failed", request)
                except Exception as e:
                    logger.error(f"Broadcast error: {e}", exc_info=True)
                    await tg_send(chat_id, f"❌ خطأ: {str(e)[:200]}")
                return {"status": "ok"}

            # ── Pending: Ban user ──
            if chat_id in pending_bans:
                pending_bans.pop(chat_id, None)
                target_id = text.strip()
                try:
                    # الرد المتوقع: user_id | reason
                    parts = target_id.split("|", 1)
                    uid = parts[0].strip()
                    reason = parts[1].strip() if len(parts) > 1 else "حظر يدوي"

                    existing = db.query(BannedUser).filter(
                        BannedUser.user_id == uid
                    ).first()

                    if existing:
                        existing.is_active = True
                        existing.reason = reason
                        existing.banned_at = datetime.datetime.utcnow()
                        existing.banned_by = chat_id
                    else:
                        db.add(BannedUser(
                            user_id=uid,
                            reason=reason,
                            banned_by=chat_id,
                        ))

                    # تحديث جدول users
                    user_obj = db.query(User).filter(User.user_id == uid).first()
                    if user_obj:
                        user_obj.is_banned = True
                        user_obj.ban_reason = reason

                    db.commit()
                    await tg_send(chat_id,
                        f"🚫 *تم حظر المستخدم*\n\n"
                        f"👤 المعرف: `{uid}`\n"
                        f"📝 السبب: `{safe_md(reason)}`")
                    log_audit(db, chat_id, "ban_user",
                              f"Banned {uid}: {reason}", request)
                except Exception as e:
                    db.rollback()
                    logger.error(f"Ban error: {e}", exc_info=True)
                    await tg_send(chat_id, f"❌ خطأ: {str(e)[:200]}")
                return {"status": "ok"}

            # ── Pending: Unban user ──
            if chat_id in pending_unbans:
                pending_unbans.pop(chat_id, None)
                target_id = text.strip()
                try:
                    ban = db.query(BannedUser).filter(
                        BannedUser.user_id == target_id,
                        BannedUser.is_active == True
                    ).first()

                    if ban:
                        ban.is_active = False
                        user_obj = db.query(User).filter(
                            User.user_id == target_id
                        ).first()
                        if user_obj:
                            user_obj.is_banned = False
                            user_obj.ban_reason = None
                        db.commit()
                        await tg_send(chat_id,
                            f"✅ *تم فك حظر*\n\n"
                            f"👤 المعرف: `{target_id}`")
                        log_audit(db, chat_id, "unban_user",
                                  f"Unbanned {target_id}", request)
                    else:
                        await tg_send(chat_id,
                            f"❌ *المستخدم غير محظور*\n\n"
                            f"👤 `{target_id}`")
                except Exception as e:
                    db.rollback()
                    logger.error(f"Unban error: {e}", exc_info=True)
                    await tg_send(chat_id, f"❌ خطأ: {str(e)[:200]}")
                return {"status": "ok"}

            # ── Pending: Search user ──
            if chat_id in pending_user_search:
                pending_user_search.pop(chat_id, None)
                target_id = text.strip()
                try:
                    user_obj = db.query(User).filter(
                        User.user_id == target_id
                    ).first()

                    if not user_obj:
                        await tg_send(chat_id,
                            f"❌ *المستخدم غير موجود*\n\n"
                            f"👤 `{target_id}`")
                    else:
                        claims = db.query(DeliveredAccount).filter(
                            DeliveredAccount.user_id == target_id,
                            DeliveredAccount.delivery_type == "claim"
                        ).count()

                        ban_status = "🚫 محظور" if user_obj.is_banned else "✅ سليم"
                        name = safe_md(
                            user_obj.first_name or user_obj.username or "بدون اسم"
                        )

                        await tg_send(chat_id,
                            f"🔍 *نتيجة البحث*\n\n"
                            f"👤 الاسم: `{name}`\n"
                            f"🆔 المعرف: `{user_obj.user_id}`\n"
                            f"📛 username: `{safe_md(user_obj.username or 'N/A')}`\n"
                            f"📊 السحوبات: `{user_obj.total_claims}`\n"
                            f"🏆 السمعة: `{user_obj.reputation_score}/100`\n"
                            f"🔒 الحالة: `{ban_status}`\n"
                            f"📅 التسجيل: `{format_datetime(user_obj.join_date)}`\n"
                            f"🕐 آخر نشاط: `{time_ago(user_obj.last_activity)}`")
                except Exception as e:
                    logger.error(f"Search error: {e}", exc_info=True)
                    await tg_send(chat_id, f"❌ خطأ: {str(e)[:200]}")
                return {"status": "ok"}

            # ── Unknown text (ignore) ──
            return {"status": "ok"}

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"❌ Telegram webhook error: {e}", exc_info=True)
        return {"status": "ok"}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#                    HEALTH & MISC ROUTES
# ═══════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "status": "online",
        "version": "5.2.0",
        "service": "Cyberpunk Distributor Core",
    }


@app.get("/health")
async def health():
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": str(e)}
        )


@app.get("/api/stats")
async def api_stats():
    db = SessionLocal()
    try:
        total = db.query(Account).count()
        avail = db.query(Account).filter(Account.is_given == False).count()
        given = db.query(Account).filter(Account.is_given == True).count()
        users = db.query(User).count()
        return {
            "total_accounts": total,
            "available": avail,
            "given": given,
            "total_users": users,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 CYBERPUNK DISTRIBUTOR CORE v5.2 — READY")
    logger.info(f"🔗 Webhook URL: /webhook/telegram")
    logger.info(f"🔗 Hit Receiver: /webhook/hit")

    # Clean expired reservations on startup
    try:
        db = SessionLocal()
        clean_expired_reservations(db)
        db.close()
    except Exception as e:
        logger.error(f"Startup cleanup error: {e}")