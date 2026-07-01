"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           🔥 CYBERPUNK DISTRIBUTOR CORE v5.0 PRO EDITION 🔥                 ║
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
from functools import wraps
from typing import Optional, List, Dict, Any
from collections import defaultdict, deque

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, func,
    DateTime, Text, ForeignKey, Index, desc, asc
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════
#                    LOGGING SYSTEM
# ═══════════════════════════════════════════════════════════

class ColoredFormatter(logging.Formatter):
    """Formatter with cyberpunk colors for terminal output"""
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{self.BOLD}{color}{record.levelname}{self.RESET}"
        return super().format(record)


# Create logs directory if needed
os.makedirs("logs", exist_ok=True)

# File handler with rotation-like naming
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

# Apply colors to console only
console_handler = logging.getLogger().handlers[-1]
console_handler.setFormatter(ColoredFormatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S"
))

logger = logging.getLogger("CYBER-CORE")
logger.info("╔═══════════════════════════════════════════════════════════════╗")
logger.info("║  🔥 CYBERPUNK DISTRIBUTOR CORE v5.0 PRO - INITIALIZING... 🔥  ║")
logger.info("╚═══════════════════════════════════════════════════════════════╝")

# ═══════════════════════════════════════════════════════════
#                    APP & CONFIG
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="Cyberpunk Distributor Core",
    description="Professional Account Distribution System with Advanced Management",
    version="5.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_IDS", "6624995237").split(",") if uid.strip()]
OPENBULLET_URL = os.getenv("OPENBULLET_URL", "")
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Settings
CLAIM_COOLDOWN_SECONDS = int(os.getenv("CLAIM_COOLDOWN_SECONDS", "86400"))  # 24h default
RESERVATION_MINUTES = int(os.getenv("RESERVATION_MINUTES", "10"))
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))
RATE_LIMIT_CLAIMS = int(os.getenv("RATE_LIMIT_CLAIMS", "5"))  # per hour
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
    """Account storage with enhanced metadata"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String(255), index=True)
    account_data = Column(String(1000), unique=True, index=True)
    captured_data = Column(Text)
    is_given = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    given_at = Column(DateTime, nullable=True)
    given_to = Column(String(50), nullable=True, index=True)  # user_id
    source_job = Column(String(255), nullable=True)  # which OB job
    quality_score = Column(Integer, default=100)  # 0-100 quality rating
    tags = Column(String(500), default="")  # comma-separated tags


class User(Base):
    """Extended user tracking"""
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
    reputation_score = Column(Integer, default=100)  # 0-100
    notes = Column(Text, nullable=True)  # admin notes


class DeliveredAccount(Base):
    """Track delivered accounts"""
    __tablename__ = "delivered_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    config_name = Column(String(255), nullable=True)
    delivered_at = Column(DateTime, default=datetime.datetime.utcnow)


class Reservation(Base):
    """Temporary account reservations"""
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    config_name = Column(String(255))
    account_id = Column(Integer, ForeignKey("accounts.id"))
    reserved_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime)
    status = Column(String(20), default="active")  # active, claimed, expired, cancelled


class AuditLog(Base):
    """Complete audit trail"""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    user_id = Column(String(50), nullable=True, index=True)
    action = Column(String(50), index=True)  # claim, reserve, cancel, ban, unban, etc.
    details = Column(Text)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)


class BannedUser(Base):
    """Banned users with reasons"""
    __tablename__ = "banned_users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), unique=True, index=True)
    banned_at = Column(DateTime, default=datetime.datetime.utcnow)
    banned_by = Column(String(50))
    reason = Column(String(500))
    expires_at = Column(DateTime, nullable=True)  # null = permanent
    is_active = Column(Boolean, default=True)


class Notification(Base):
    """Pending notifications for users"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    notification_type = Column(String(30), default="general")  # low_stock, new_accounts, system


class SystemStats(Base):
    """Aggregated statistics snapshots"""
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


# Create all tables
Base.metadata.create_all(bind=engine)
logger.info("✅ Database tables initialized")

# ═══════════════════════════════════════════════════════════
#                    IN-MEMORY TRACKING
# ═══════════════════════════════════════════════════════════

class RateLimiter:
    """Advanced rate limiter using sliding window"""
    def __init__(self):
        self._windows: Dict[str, deque] = defaultdict(deque)
        self._claim_windows: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        window = self._windows[key]
        # Remove old entries
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
        while window and window[0] < now - 3600:  # 1 hour window
            window.popleft()
        return len(window) < RATE_LIMIT_CLAIMS

    def record_claim(self, user_id: str):
        self._claim_windows[user_id].append(time.time())


rate_limiter = RateLimiter()


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


def get_db():
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def log_audit(db, user_id: Optional[str], action: str, details: str, request: Request = None):
    """Log action to audit trail"""
    try:
        ip = request.client.host if request else None
        ua = request.headers.get("user-agent") if request else None
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


def get_or_create_user(db, user_id: str, user_info: dict = None):
    """Get existing user or create new one"""
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
        # Update activity
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
    """Check if user is banned. Returns (is_banned, reason)"""
    ban = db.query(BannedUser).filter(
        BannedUser.user_id == user_id,
        BannedUser.is_active == True
    ).first()
    if not ban:
        return False, None
    # Check if ban expired
    if ban.expires_at and ban.expires_at < datetime.datetime.utcnow():
        ban.is_active = False
        db.commit()
        return False, None
    return True, ban.reason


def clean_expired_reservations(db):
    """Remove expired reservations and free accounts"""
    now = datetime.datetime.utcnow()
    expired = db.query(Reservation).filter(
        Reservation.expires_at < now,
        Reservation.status == "active"
    ).all()
    count = 0
    for res in expired:
        res.status = "expired"
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
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_send failed: {e}")


async def tg_send_html(chat_id: str, text: str, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    payload.update(kwargs)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
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
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_edit failed: {e}")


async def tg_answer(callback_id: str, text: str, show_alert: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
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
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json={"chat_id": chat_id, "message_id": message_id})
        except Exception as e:
            logger.error(f"tg_delete failed: {e}")


async def tg_send_document(chat_id: str, file_path: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    async with httpx.AsyncClient(verify=False, timeout=30.0) as c:
        try:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
                await c.post(url, data=data, files=files)
        except Exception as e:
            logger.error(f"tg_send_document failed: {e}")


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
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
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
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
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
                        logger.info(f"📊 Job {item['id']} keys: {ext['_keys']}")
                    else:
                        logger.warning(f"⚠️ Job {item['id']} detail HTTP {resp2.status_code}")
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
    """Format comprehensive statistics message"""
    total = db.query(Account).count()
    avail = db.query(Account).filter(Account.is_given == False).count()
    given = db.query(Account).filter(Account.is_given == True).count()
    total_users = db.query(User).count()
    banned = db.query(User).filter(User.is_banned == True).count()
    active_res = db.query(Reservation).filter(Reservation.status == "active").count()

    # Today's claims
    today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    claims_today = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= today_start).count()

    # Top configs
    top_configs = db.query(
        Account.config_name, func.count(Account.id)
    ).filter(Account.is_given == False).group_by(Account.config_name).order_by(desc(func.count(Account.id))).limit(5).all()

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
    """Format user's claim history"""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        return "❌ *لم يتم العثور على سجلات.*"

    deliveries = db.query(DeliveredAccount).filter(
        DeliveredAccount.user_id == user_id
    ).order_by(desc(DeliveredAccount.delivered_at)).limit(10).all()

    lines = [
        f"🕐 *「 سجل العمليات 」* 🕐\n",
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
    """Format active reservations for user"""
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

    lines = [
        "🏦 *「 حجوزاتك النشطة 」* 🏦\n",
    ]

    for res in reservations:
        remaining = res.expires_at - datetime.datetime.utcnow()
        mins = int(remaining.total_seconds() / 60)
        lines.append(f"📦 `{safe_md(res.config_name)}`")
        lines.append(f"   ⏳ متبقي: `{mins} دقيقة`")
        lines.append(f"   📅 ينتهي: `{format_datetime(res.expires_at)}`")
        lines.append("   ━━━")

    return "\n".join(lines)


def format_detailed_report(db) -> str:
    """Format detailed admin report"""
    now = datetime.datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - datetime.timedelta(days=7)

    total = db.query(Account).count()
    avail = db.query(Account).filter(Account.is_given == False).count()
    given = db.query(Account).filter(Account.is_given == True).count()

    today_claims = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= today_start).count()
    week_claims = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= week_ago).count()

    total_users = db.query(User).count()
    new_today = db.query(User).filter(User.join_date >= today_start).count()

    banned_count = db.query(BannedUser).filter(BannedUser.is_active == True).count()

    # Config distribution
    config_stats = db.query(
        Account.config_name,
        func.count(Account.id),
        func.sum(func.case([(Account.is_given == False, 1)], else_=0))
    ).group_by(Account.config_name).all()

    lines = [
        "╔════════════════════════════════════════════════════╗",
        "║           📋 NEXUS DETAILED REPORT                 ║",
        f"║  Generated: {now.strftime('%Y-%m-%d %H:%M UTC'):>35} ║",
        "╠════════════════════════════════════════════════════╣",
        "║  📊 ACCOUNTS METRICS                               ║",
        f"║     Total:     {total:>6}  Available: {avail:>6}       ║",
        f"║     Given:     {given:>6}  Ratio:     {(avail/max(total,1)*100):>5.1f}%      ║",
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
        display = cfg[:15] + ".." if len(cfg) > 15 else cfg
        lines.append(f"║  {display:>18}  {avail_cfg:>4}/{total_cfg:<4}                     ║")

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

        # Extract quality hints if available
        quality = 100
        source_job = data.get("jobName") or data.get("job") or None

        if db.query(Account).filter(Account.account_data == account_data).first():
            return {"status": "ignored", "reason": "duplicate"}

        db.add(Account(
            config_name=config_name or "UNKNOWN",
            account_data=account_data,
            captured_data=captured_data,
            is_given=False,
            source_job=source_job,
            quality_score=quality,
        ))
        db.commit()

        # Log audit
        log_audit(db, None, "hit_received", f"New account received: {config_name}", request)

        # Check low stock and notify
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
        db.rollback()
        logger.error(f"/webhook/hit error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
#                    WEBHOOK: TELEGRAM
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

            # ── Claim Account ──
            if data.startswith("claim_cfg:"):
                cfg_h = data.split("claim_cfg:", 1)[1]
                all_cfgs = db.query(Account.config_name).filter(Account.is_given == False).distinct().all()
                selected = None
                for (name,) in all_cfgs:
                    if config_hash(name) == cfg_h:
                        selected = name
                        break

                if not selected:
                    await tg_answer(callback_id, "❌ لم يتم العثور على هذا النوع!", show_alert=True)
                    return {"status": "ok"}

                # Check ban
                banned, reason = is_user_banned(db, chat_id)
                if banned:
                    await tg_answer(callback_id, f"🚫 أنت محظور!\nالسبب: {reason}", show_alert=True)
                    return {"status": "ok"}

                # Check rate limit
                if not rate_limiter.is_claim_allowed(chat_id):
                    await tg_answer(callback_id, "⏳ تجاوزت الحد المسموح من السحوبات (5/ساعة)", show_alert=True)
                    return {"status": "ok"}

                # Check cooldown (by delivered_accounts)
                last_delivery = db.query(DeliveredAccount).filter(
                    DeliveredAccount.user_id == chat_id
                ).order_by(desc(DeliveredAccount.delivered_at)).first()

                if last_delivery and last_delivery.delivered_at:
                    cooldown_end = last_delivery.delivered_at + datetime.timedelta(seconds=CLAIM_COOLDOWN_SECONDS)
                    if datetime.datetime.utcnow() < cooldown_end:
                        remaining = cooldown_end - datetime.datetime.utcnow()
                        hours = int(remaining.total_seconds() / 3600)
                        mins = int((remaining.total_seconds() % 3600) / 60)
                        await tg_answer(callback_id, f"⏳ يجب الانتظار: {hours}س {mins}د قبل السحب التالي", show_alert=True)
                        return {"status": "ok"}

                # Atomic claim
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
                    config_name=account.config_name
                ))

                # Update user stats
                user = get_or_create_user(db, chat_id)
                user.total_claims += 1
                user.last_activity = datetime.datetime.utcnow()

                db.commit()
                rate_limiter.record_claim(chat_id)

                # Log audit
                log_audit(db, chat_id, "account_claimed", f"Claimed {account.config_name}", request)

                await tg_edit(chat_id, message_id,
                    f"🌌 *⚡ 「 تم السحب بنجاح 」 ⚡* 🌌\n\n"
                    f"📦 *النوع:* `{safe_md(account.config_name)}`\n\n"
                    f"👤 *الحساب:*\n`{safe_md(account.account_data)}`\n\n"
                    f"⚙️ *المستخرج:*\n`{safe_md(account.captured_data)}`\n\n"
                    f"🔒 _STATUS: TERMINAL LOCKED_\n"
                    f"⏳ السحب التالي بعد: {CLAIM_COOLDOWN_SECONDS // 3600} ساعة",
                    reply_markup=None
                )
                return {"status": "ok"}

            # ── Reservation Confirmation ──
            if data.startswith("confirm_reserve:"):
                cfg_h = data.split("confirm_reserve:", 1)[1]
                all_cfgs = db.query(Account.config_name).filter(Account.is_given == False).distinct().all()
                selected = None
                for (name,) in all_cfgs:
                    if config_hash(name) == cfg_h:
                        selected = name
                        break

                if not selected:
                    await tg_answer(callback_id, "❌ لم يتم العثور على النوع!", show_alert=True)
                    return {"status": "ok"}

                # Check existing reservation
                existing = db.query(Reservation).filter(
                    Reservation.user_id == chat_id,
                    Reservation.status == "active"
                ).first()
                if existing:
                    await tg_answer(callback_id, "⏳ لديك حجز نشط بالفعل! أنهِه أولاً.", show_alert=True)
                    return {"status": "ok"}

                # Get available account
                account = db.query(Account).filter(
                    Account.config_name == selected,
                    Account.is_given == False
                ).with_for_update().first()

                if not account:
                    await tg_answer(callback_id, "😔 لا توجد حسابات متاحة!", show_alert=True)
                    return {"status": "ok"}

                # Mark as reserved (is_given = True to prevent others from claiming)
                account.is_given = True
                account.given_to = chat_id

                expires = datetime.datetime.utcnow() + datetime.timedelta(minutes=RESERVATION_MINUTES)
                reservation = Reservation(
                    user_id=chat_id,
                    config_name=selected,
                    account_id=account.id,
                    expires_at=expires,
                    status="active"
                )
                db.add(reservation)
                db.commit()

                log_audit(db, chat_id, "account_reserved", f"Reserved {selected} until {expires}", request)

                await tg_edit(chat_id, message_id,
                    f"🏦 *「 تم الحجز بنجاح 」* 🏦\n\n"
                    f"📦 *النوع:* `{safe_md(selected)}`\n"
                    f"⏳ *المدة:* `{RESERVATION_MINUTES} دقيقة`\n"
                    f"📅 *ينتهي:* `{format_datetime(expires)}`\n\n"
                    f"⚡ *الحساب محجوز لك. أكمل السحب قبل انتهاء الوقت!*",
                    reply_markup=None
                )
                return {"status": "ok"}

            # ── Cancel Reservation ──
            if data.startswith("cancel_reserve:"):
                cfg_h = data.split("cancel_reserve:", 1)[1]
                all_cfgs = db.query(Account.config_name).distinct().all()
                selected = None
                for (name,) in all_cfgs:
                    if config_hash(name) == cfg_h:
                        selected = name
                        break

                reservation = db.query(Reservation).filter(
                    Reservation.user_id == chat_id,
                    Reservation.config_name == selected,
                    Reservation.status == "active"
                ).first()

                if reservation:
                    # Free the account
                    account = db.query(Account).filter(Account.id == reservation.account_id).first()
                    if account:
                        account.is_given = False
                        account.given_to = None
                    reservation.status = "cancelled"
                    db.commit()
                    log_audit(db, chat_id, "reservation_cancelled", f"Cancelled reservation for {selected}", request)
                    await tg_answer(callback_id, "✅ تم إلغاء الحجز وإعادة الحساب للمخزن.", show_alert=True)
                else:
                    await tg_answer(callback_id, "❌ لا يوجد حجز نشط بهذا الاسم.", show_alert=True)
                return {"status": "ok"}

            # ── Admin Callbacks ──
            if chat_id not in ADMIN_IDS:
                await tg_answer(callback_id, "🚫 صلاحيات غير كافية!", show_alert=True)
                return {"status": "ok"}

            if data == "reset_delivered":
                count = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.commit()
                log_audit(db, chat_id, "admin_reset_delivered", f"Reset {count} delivered records", request)
                await tg_answer(callback_id, f"🧹 تم تصفير الموزع ({count} سجل)", show_alert=True)

            elif data == "clear_accounts":
                ac = db.query(Account).count()
                dc = db.query(DeliveredAccount).count()
                rc = db.query(Reservation).count()
                db.query(Reservation).delete()
                db.query(DeliveredAccount).delete()
                db.query(Account).delete()
                db.commit()
                log_audit(db, chat_id, "admin_clear_all", f"Cleared {ac} accounts, {dc} delivered, {rc} reservations", request)
                await tg_answer(callback_id, f"🚨 مسح {ac} حساب و {dc} سجل و {rc} حجز.", show_alert=True)

            elif data == "refresh_admin_stats":
                total = db.query(Account).count()
                avail = db.query(Account).filter(Account.is_given == False).count()
                given = db.query(Account).filter(Account.is_given == True).count()
                users = db.query(User).count()
                await tg_edit(chat_id, message_id,
                    f"┌─── 🌌 *「 لوحة تحكم النيون 」* 🌌\n"
                    f"│\n"
                    f"├── 🟣 *الإجمالي:* `{total}`\n"
                    f"├── 🟢 *الجاهزة:* `{avail}`\n"
                    f"├── 🔴 *الموزعة:* `{given}`\n"
                    f"├── 👥 *المستخدمين:* `{users}`\n"
                    f"│\n"
                    f"└────────────── [ تحديث مباشر ] 🖥️",
                    reply_markup=get_admin_keyboard()
                )
                await tg_answer(callback_id, "🔄 تم التحديث", show_alert=False)

            elif data == "detailed_report":
                report = format_detailed_report(db)
                await tg_send(chat_id, report)
                await tg_answer(callback_id, "📊 تم إرسال التقرير", show_alert=False)

            elif data == "scan_inventory":
                avail = db.query(Account).filter(Account.is_given == False).count()
                config_counts = db.query(
                    Account.config_name, func.count(Account.id)
                ).filter(Account.is_given == False).group_by(Account.config_name).all()

                lines = [
                    f"🔍 *فحص المخزن* 🔍\n",
                    f"🟢 إجمالي المتاح: `{avail}`\n",
                    f"*التفاصيل:*",
                    "━━━━━━━━━━━━━━━━━━━━",
                ]
                for cfg, count in config_counts:
                    display = cfg[:30] + "..." if len(cfg) > 30 else cfg
                    lines.append(f"📦 `{safe_md(display)}`: `{count}`")

                await tg_send(chat_id, "\n".join(lines))
                await tg_answer(callback_id, "🔍 تم الفحص", show_alert=False)

            elif data == "banned_users":
                banned = db.query(BannedUser).filter(BannedUser.is_active == True).all()
                if not banned:
                    await tg_answer(callback_id, "✅ لا يوجد مستخدمون محظورون حالياً.", show_alert=True)
                else:
                    lines = ["🚫 *المحظورون:*\n"]
                    for b in banned:
                        lines.append(f"👤 `{b.user_id}`")
                        lines.append(f"   📅 {format_datetime(b.banned_at)}")
                        lines.append(f"   📝 {safe_md(b.reason or 'غير محدد')}")
                        if b.expires_at:
                            lines.append(f"   ⏳ ينتهي: {format_datetime(b.expires_at)}")
                        lines.append("━━━")
                    await tg_send(chat_id, "\n".join(lines))
                    await tg_answer(callback_id, f"🚫 {len(banned)} محظور", show_alert=False)

            elif data == "broadcast":
                await tg_answer(callback_id, "📢 أرسل الرسالة الآن وسيتم بثها لجميع المستخدمين.", show_alert=True)
                # Set broadcast mode - handled in text messages
                pending_broadcasts[chat_id] = True

            elif data == "settings":
                settings_msg = (
                    f"⚙️ *الإعدادات الحالية:*\n\n"
                    f"⏳ فترة الانتظار: `{CLAIM_COOLDOWN_SECONDS // 3600}h`\n"
                    f"🏦 مدة الحجز: `{RESERVATION_MINUTES}min`\n"
                    f"⚠️ حد المخزن المنخفض: `{LOW_STOCK_THRESHOLD}`\n"
                    f"📊 حد السحب/ساعة: `{RATE_LIMIT_CLAIMS}`\n"
                    f"🔔 تنبيهات المخزن: `{'مفعلة' if NOTIFY_ADMINS_ON_LOW_STOCK else 'معطلة'}`"
                )
                await tg_answer(callback_id, settings_msg, show_alert=True)

            elif data == "ban_user":
                pending_bans[chat_id] = True
                await tg_answer(callback_id, "🚫 أرسل ID المستخدم للحظر...", show_alert=True)

            elif data == "unban_user":
                pending_unbans[chat_id] = True
                await tg_answer(callback_id, "✅ أرسل ID المستخدم لفك الحظر...", show_alert=True)

            elif data == "list_banned":
                banned = db.query(BannedUser).filter(BannedUser.is_active == True).order_by(desc(BannedUser.banned_at)).limit(20).all()
                if not banned:
                    await tg_answer(callback_id, "لا يوجد محظورون", show_alert=True)
                else:
                    lines = ["🚫 *المحظورون:*\n"]
                    for b in banned:
                        lines.append(f"👤 `{b.user_id}` - {safe_md(b.reason or 'N/A')[:30]}")
                    await tg_send(chat_id, "\n".join(lines))
                    await tg_answer(callback_id, f"{len(banned)} محظور", show_alert=False)

            elif data == "list_users":
                users = db.query(User).order_by(desc(User.last_activity)).limit(20).all()
                lines = ["👥 *آخر المستخدمين النشطين:*\n"]
                for u in users:
                    name = u.username or u.first_name or "Unknown"
                    status = "🚫" if u.is_banned else "✅"
                    lines.append(f"{status} `{u.user_id}` - {safe_md(name)} ({u.total_claims} سحب)")
                await tg_send(chat_id, "\n".join(lines))
                await tg_answer(callback_id, f"{len(users)} مستخدم", show_alert=False)

            elif data == "search_user":
                pending_user_search[chat_id] = True
                await tg_answer(callback_id, "🔍 أرسل ID أو اسم المستخدم للبحث...", show_alert=True)

            elif data == "user_stats":
                total_users = db.query(User).count()
                active_today = db.query(User).filter(
                    User.last_activity >= datetime.datetime.utcnow() - datetime.timedelta(hours=24)
                ).count()
                banned_count = db.query(User).filter(User.is_banned == True).count()
                top_claimers = db.query(User).order_by(desc(User.total_claims)).limit(5).all()

                lines = [
                    "📊 *إحصائيات المستخدمين* 📊\n",
                    f"👥 إجمالي المستخدمين: `{total_users}`",
                    f"🟢 نشطون 24 ساعة: `{active_today}`",
                    f"🚫 المحظورون: `{banned_count}`",
                    "\n🏆 *أكثر الساحبين:*",
                ]
                for u in top_claimers:
                    name = u.username or u.first_name or "Unknown"
                    lines.append(f"   {safe_md(name)}: `{u.total_claims}`")
                await tg_send(chat_id, "\n".join(lines))
                await tg_answer(callback_id, "📊 تم", show_alert=False)

            elif data == "export_json":
                await export_and_send(db, chat_id, "json")
                await tg_answer(callback_id, "📤 تم تصدير JSON", show_alert=False)

            elif data == "export_csv":
                await export_and_send(db, chat_id, "csv")
                await tg_answer(callback_id, "📤 تم تصدير CSV", show_alert=False)

            elif data == "export_audit":
                await export_audit_log(db, chat_id)
                await tg_answer(callback_id, "📤 تم تصدير السجل", show_alert=False)

            elif data == "full_backup":
                await create_full_backup(db, chat_id)
                await tg_answer(callback_id, "💾 تم إنشاء نسخة احتياطية", show_alert=False)

            elif data in ("back_to_admin",):
                await tg_edit(chat_id, message_id, "🛠️ اختر من القائمة:", reply_markup=get_admin_keyboard())

            elif data == "cancel_action":
                for pending in [pending_broadcasts, pending_bans, pending_unbans, pending_user_search]:
                    pending.pop(chat_id, None)
                await tg_answer(callback_id, "❌ تم الإلغاء", show_alert=False)

            return {"status": "ok"}

        # ========== TEXT MESSAGES ==========
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}

        chat_id = str(payload["message"]["chat"]["id"])
        text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS

        # Extract user info
        user_info = {
            "username": payload["message"]["from"].get("username"),
            "first_name": payload["message"]["from"].get("first_name"),
            "last_name": payload["message"]["from"].get("last_name"),
        }

        # Get or create user
        user = get_or_create_user(db, chat_id, user_info)

        # Handle pending actions
        if chat_id in pending_bans:
            pending_bans.pop(chat_id, None)
            target_id = text.strip()
            reason = "محظور من قبل المشرف"

            existing = db.query(BannedUser).filter(BannedUser.user_id == target_id).first()
            if existing:
                existing.is_active = True
                existing.banned_at = datetime.datetime.utcnow()
                existing.banned_by = chat_id
                existing.reason = reason
            else:
                db.add(BannedUser(
                    user_id=target_id,
                    banned_by=chat_id,
                    reason=reason
                ))

            # Mark user as banned
            target_user = db.query(User).filter(User.user_id == target_id).first()
            if target_user:
                target_user.is_banned = True
                target_user.ban_reason = reason

            db.commit()
            log_audit(db, chat_id, "user_banned", f"Banned user {target_id}", request)
            await tg_send(chat_id, f"🚫 تم حظر المستخدم `{target_id}`")
            return {"status": "ok"}

        if chat_id in pending_unbans:
            pending_unbans.pop(chat_id, None)
            target_id = text.strip()

            ban = db.query(BannedUser).filter(BannedUser.user_id == target_id, BannedUser.is_active == True).first()
            if ban:
                ban.is_active = False

            target_user = db.query(User).filter(User.user_id == target_id).first()
            if target_user:
                target_user.is_banned = False
                target_user.ban_reason = None

            db.commit()
            log_audit(db, chat_id, "user_unbanned", f"Unbanned user {target_id}", request)
            await tg_send(chat_id, f"✅ تم فك حظر المستخدم `{target_id}`")
            return {"status": "ok"}

        if chat_id in pending_user_search:
            pending_user_search.pop(chat_id, None)
            search_term = text.strip()

            # Search by ID or username
            results = db.query(User).filter(
                (User.user_id == search_term) | (User.username.ilike(f"%{search_term}%"))
            ).limit(10).all()

            if not results:
                await tg_send(chat_id, "❌ لم يتم العثور على مستخدمين.")
            else:
                lines = [f"🔍 *نتائج البحث ({len(results)}):*\n"]
                for u in results:
                    status = "🚫 محظور" if u.is_banned else "✅ نشط"
                    name = u.username or u.first_name or "Unknown"
                    lines.append(f"👤 `{u.user_id}` - {safe_md(name)}")
                    lines.append(f"   {status} | سحوبات: {u.total_claims} | سمعة: {u.reputation_score}")
                    lines.append(f"   آخر نشاط: {time_ago(u.last_activity)}")
                    lines.append("━━━")
                await tg_send(chat_id, "\n".join(lines))
            return {"status": "ok"}

        if chat_id in pending_broadcasts:
            pending_broadcasts.pop(chat_id, None)
            message_text = text

            # Send to all users
            users = db.query(User).all()
            sent = 0
            failed = 0
            for u in users:
                try:
                    await tg_send(u.user_id,
                        f"📢 *إشعار عام من الإدارة* 📢\n\n{safe_md(message_text)}"
                    )
                    sent += 1
                except Exception:
                    failed += 1

            log_audit(db, chat_id, "broadcast", f"Sent broadcast to {sent} users, {failed} failed", request)
            await tg_send(chat_id, f"📢 تم الإرسال: `{sent}` نجح, `{failed}` فشل")
            return {"status": "ok"}

        # ── /start ──
        if text == "/start":
            await tg_send(chat_id,
                "🌌 *WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE PRO* 🌌\n\n"
                "⚡ *الإصدار:* `v5.0 PRO`\n"
                "🎛️ *الواجهة:* `Nexus Terminal`\n"
                f"👤 *مستخدمك:* `{chat_id}`\n\n"
                "🔹 *الميزات المتاحة:*\n"
                "   ⚡ سحب حساب\n"
                "   📊 إحصائيات المخزن\n"
                "   🤖 مراقبة OpenBullet\n"
                "   📈 إحصائيات متقدمة\n"
                "   🕐 سجل العمليات\n"
                "   🏦 حجز مؤقت\n\n"
                "🤖 _اضغط على الزر أدناه للبدء..._",
                reply_markup=get_main_keyboard(is_admin),
            )

        # ── Stats ──
        elif text in ("📡 🌐 إحصائيات المخزن 🌐 📡", "/stats"):
            clean_expired_reservations(db)
            stats_msg = format_stats_message(db)
            await tg_send(chat_id, stats_msg)

        # ── Claim Account ──
        elif text in ("⚡ 🧬 سحب حساب جديد 🧬 ⚡", "/get"):
            # Check ban
            banned, reason = is_user_banned(db, chat_id)
            if banned:
                await tg_send(chat_id,
                    f"🚫 *تم رفض الوصول* 🚫\n\n"
                    f"أنت محظور من النظام.\n"
                    f"السبب: `{safe_md(reason or 'غير محدد')}`"
                )
                return {"status": "ok"}

            # Check rate limit
            if not rate_limiter.is_claim_allowed(chat_id):
                remaining_claims = rate_limiter.get_remaining(chat_id, RATE_LIMIT_CLAIMS, 3600)
                await tg_send(chat_id,
                    f"⏳ *تنبيه:* لقد تجاوزت الحد المسموح من السحوبات.\n"
                    f"📊 متبقي: `{remaining_claims}/{RATE_LIMIT_CLAIMS}` في الساعة"
                )
                return {"status": "ok"}

            # Check cooldown
            last_delivery = db.query(DeliveredAccount).filter(
                DeliveredAccount.user_id == chat_id
            ).order_by(desc(DeliveredAccount.delivered_at)).first()

            if last_delivery and last_delivery.delivered_at:
                cooldown_end = last_delivery.delivered_at + datetime.timedelta(seconds=CLAIM_COOLDOWN_SECONDS)
                if datetime.datetime.utcnow() < cooldown_end:
                    remaining = cooldown_end - datetime.datetime.utcnow()
                    hours = int(remaining.total_seconds() / 3600)
                    mins = int((remaining.total_seconds() % 3600) / 60)
                    await tg_send(chat_id,
                        f"⏳ *فترة الانتظار* ⏳\n\n"
                        f"يجب الانتظار: `{hours}ساعة {mins}دقيقة`\n"
                        f"قبل السحب التالي.\n\n"
                        f"_استخدم الحجز المؤقت للحجز المسبق!_"
                    )
                    return {"status": "ok"}

            results = (
                db.query(Account.config_name, func.count(Account.id))
                .filter(Account.is_given == False)
                .group_by(Account.config_name).all()
            )
            results = [(c, n) for c, n in results if c]

            if not results:
                await tg_send(chat_id,
                    "🚨 *MAINFRAME ERROR:* `المستودع فارغ حالياً` 🚨\n\n"
                    "😔 لا توجد حسابات جاهزة.\n"
                    "📢 سيتم إشعارك عند توفر حسابات جديدة."
                )
            else:
                buttons = []
                for cfg_name, count in results:
                    display = cfg_name if len(cfg_name) <= 40 else cfg_name[:37] + "..."
                    buttons.append([{
                        "text": f"🎁 {display} ({count})",
                        "callback_data": f"claim_cfg:{config_hash(cfg_name)}"
                    }])

                await tg_send(chat_id,
                    f"┌─── 🎛️ *「 قائمة التخصيص 」* 🎛️\n"
                    f"│\n"
                    f"├── ⚡ تم فحص قاعدة البيانات.\n"
                    f"├── 📊 السحوبات المتبقية: `{rate_limiter.get_remaining(chat_id, RATE_LIMIT_CLAIMS, 3600)}/{RATE_LIMIT_CLAIMS}`\n"
                    f"└── 👇 *اختر نوع الحساب:*",
                    reply_markup={"inline_keyboard": buttons},
                )

        # ── OB Status ──
        elif text in ("🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖", "/ob"):
            ob_data = await fetch_ob_status()
            await tg_send(chat_id, format_ob_message(ob_data))

        # ── Advanced Stats ──
        elif text in ("📊 📈 إحصائيات متقدمة 📈 📊", "/advanced"):
            stats_msg = format_stats_message(db)

            # Add trends
            today = datetime.datetime.utcnow()
            yesterday = today - datetime.timedelta(days=1)
            week_ago = today - datetime.timedelta(days=7)

            today_claims = db.query(DeliveredAccount).filter(
                DeliveredAccount.delivered_at >= today.replace(hour=0, minute=0, second=0)
            ).count()

            week_claims = db.query(DeliveredAccount).filter(
                DeliveredAccount.delivered_at >= week_ago
            ).count()

            trend_msg = (
                f"\n📈 *الاتجاهات:*\n"
                f"   📅 اليوم: `{today_claims}` سحب\n"
                f"   📊 هذا الأسبوع: `{week_claims}` سحب"
            )

            await tg_send(chat_id, stats_msg + trend_msg)

        # ── User History ──
        elif text in ("🕐 📜 سجل عملياتي 📜 🕐", "/history"):
            history_msg = format_user_history(db, chat_id)
            await tg_send(chat_id, history_msg)

        # ── Reservation System ──
        elif text in ("🏦 ⏳ حجز مؤقت ⏳ 🏦", "/reserve"):
            clean_expired_reservations(db)

            # Check existing reservation
            existing = db.query(Reservation).filter(
                Reservation.user_id == chat_id,
                Reservation.status == "active"
            ).first()

            if existing:
                remaining = existing.expires_at - datetime.datetime.utcnow()
                mins = int(remaining.total_seconds() / 60)
                await tg_send(chat_id,
                    f"⏳ *لديك حجز نشط بالفعل!*\n\n"
                    f"📦 النوع: `{safe_md(existing.config_name)}`\n"
                    f"⏰ متبقي: `{mins} دقيقة`\n\n"
                    f"_أنهِ الحجز الحالي قبل عمل حجز جديد._"
                )
                return {"status": "ok"}

            # Show available configs for reservation
            results = (
                db.query(Account.config_name, func.count(Account.id))
                .filter(Account.is_given == False)
                .group_by(Account.config_name).all()
            )
            results = [(c, n) for c, n in results if c]

            if not results:
                await tg_send(chat_id, "😔 لا توجد حسابات متاحة للحجز حالياً.")
            else:
                buttons = []
                for cfg_name, count in results:
                    display = cfg_name if len(cfg_name) <= 40 else cfg_name[:37] + "..."
                    buttons.append([{
                        "text": f"🏦 حجز: {display} ({count})",
                        "callback_data": f"confirm_reserve:{config_hash(cfg_name)}"
                    }])

                await tg_send(chat_id,
                    f"🏦 *「 نظام الحجز المؤقت 」* 🏦\n\n"
                    f"⚡ المدة: `{RESERVATION_MINUTES} دقيقة`\n"
                    f"📌 سيحجز لك حساب حتى تستكمل السحب\n\n"
                    f"👇 *اختر نوع الحساب للحجز:*",
                    reply_markup={"inline_keyboard": buttons}
                )

        # ── Admin Panel ──
        elif text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
            total = db.query(Account).count()
            avail = db.query(Account).filter(Account.is_given == False).count()
            given = db.query(Account).filter(Account.is_given == True).count()
            users = db.query(User).count()

            await tg_send(chat_id,
                f"┌─── 🌌 *「 لوحة تحكم النيون 」* 🌌\n"
                f"│\n"
                f"├── 🟣 *الإجمالي:* `{total}`\n"
                f"├── 🟢 *الجاهزة:* `{avail}`\n"
                f"├── 🔴 *الموزعة:* `{given}`\n"
                f"├── 👥 *المستخدمين:* `{users}`\n"
                f"│\n"
                f"└────────────── [ أوامر النظام ] 👇",
                reply_markup=get_admin_keyboard(),
            )

        # ── User Management ──
        elif text == "📋 🔐 إدارة المستخدمين 🔐 📋" and is_admin:
            await tg_send(chat_id,
                "📋 *「 إدارة المستخدمين 」* 📋\n\n"
                "اختر الإجراء المطلوب:",
                reply_markup=get_user_management_keyboard()
            )

        # ── Backup & Export ──
        elif text == "💾 📤 تصدير/نسخ احتياطي 📤 💾" and is_admin:
            await tg_send(chat_id,
                "💾 *「 التصدير والنسخ الاحتياطي 」* 💾\n\n"
                "اختر نوع التصدير:",
                reply_markup=get_backup_keyboard()
            )

        # ── /help ──
        elif text == "/help":
            help_text = (
                "📖 *دليل الاستخدام* 📖\n\n"
                "*الأوامر العامة:*\n"
                "   /start - بدء البوت\n"
                "   /get أو ⚡ سحب حساب - سحب حساب جديد\n"
                "   /stats أو 📡 إحصائيات - عرض المخزن\n"
                "   /ob أو 🤖 عمليات - مراقبة OpenBullet\n"
                "   /advanced - إحصائيات متقدمة\n"
                "   /history أو 🕐 سجل - سجل سحوباتك\n"
                "   /reserve أو 🏦 حجز - حجز مؤقت\n"
                "   /help - هذا الدليل\n\n"
                "*للمطورين:*\n"
                "   🛠️ لوحة التحكم\n"
                "   📋 إدارة المستخدمين\n"
                "   💾 تصدير/نسخ احتياطي"
            )
            await tg_send(chat_id, help_text)

        return {"status": "ok"}

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Telegram webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# Pending actions tracking (in-memory)
pending_broadcasts: Dict[str, bool] = {}
pending_bans: Dict[str, bool] = {}
pending_unbans: Dict[str, bool] = {}
pending_user_search: Dict[str, bool] = {}


# ═══════════════════════════════════════════════════════════
#                    EXPORT & BACKUP FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def export_and_send(db, chat_id: str, fmt: str = "json"):
    """Export accounts and send as document"""
    import tempfile

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "json":
        accounts = db.query(Account).all()
        data = []
        for a in accounts:
            data.append({
                "id": a.id,
                "config_name": a.config_name,
                "account_data": a.account_data,
                "captured_data": a.captured_data,
                "is_given": a.is_given,
                "created_at": format_datetime(a.created_at),
                "given_at": format_datetime(a.given_at),
                "given_to": a.given_to,
                "source_job": a.source_job,
                "quality_score": a.quality_score,
                "tags": a.tags,
            })

        filepath = f"/tmp/accounts_export_{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        await tg_send_document(chat_id, filepath, f"📤 تصدير الحسابات ({len(data)} سجل)")
        os.remove(filepath)

    elif fmt == "csv":
        import csv
        accounts = db.query(Account).all()
        filepath = f"/tmp/accounts_export_{timestamp}.csv"

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Config", "Account", "Captured", "Given", "Created", "Given To", "Quality"])
            for a in accounts:
                writer.writerow([
                    a.id, a.config_name, a.account_data,
                    a.captured_data[:100] if a.captured_data else "",
                    a.is_given, format_datetime(a.created_at),
                    a.given_to, a.quality_score
                ])

        await tg_send_document(chat_id, filepath, f"📤 تصدير CSV ({len(accounts)} سجل)")
        os.remove(filepath)


async def export_audit_log(db, chat_id: str):
    """Export audit log"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logs = db.query(AuditLog).order_by(desc(AuditLog.timestamp)).limit(1000).all()

    filepath = f"/tmp/audit_log_{timestamp}.json"
    data = []
    for log in logs:
        data.append({
            "timestamp": format_datetime(log.timestamp),
            "user_id": log.user_id,
            "action": log.action,
            "details": log.details,
            "ip": log.ip_address,
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await tg_send_document(chat_id, filepath, f"📋 سجل العمليات ({len(data)} سجل)")
    os.remove(filepath)


async def create_full_backup(db, chat_id: str):
    """Create full system backup"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    backup = {
        "backup_time": format_datetime(datetime.datetime.utcnow()),
        "version": "5.0.0",
        "accounts": [],
        "users": [],
        "delivered": [],
        "reservations": [],
        "banned": [],
        "audit_log": [],
    }

    # Accounts
    for a in db.query(Account).all():
        backup["accounts"].append({
            "config_name": a.config_name,
            "account_data": a.account_data,
            "captured_data": a.captured_data,
            "is_given": a.is_given,
            "given_to": a.given_to,
            "quality_score": a.quality_score,
            "tags": a.tags,
        })

    # Users
    for u in db.query(User).all():
        backup["users"].append({
            "user_id": u.user_id,
            "username": u.username,
            "total_claims": u.total_claims,
            "is_banned": u.is_banned,
            "reputation_score": u.reputation_score,
        })

    filepath = f"/tmp/full_backup_{timestamp}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)

    total_size = len(backup["accounts"]) + len(backup["users"])
    await tg_send_document(chat_id, filepath, f"💾 نسخة احتياطية كاملة ({total_size} سجل)")
    os.remove(filepath)


# ═══════════════════════════════════════════════════════════
#                    REST API ENDPOINTS
# ═══════════════════════════════════════════════════════════

class StatsResponse(BaseModel):
    total_accounts: int
    available: int
    given: int
    total_users: int
    banned_users: int
    active_reservations: int
    claims_today: int
    version: str = "5.0.0"


class AccountItem(BaseModel):
    id: int
    config_name: str
    is_given: bool
    created_at: Optional[str]
    quality_score: int = 100


@app.get("/api/stats", response_model=StatsResponse, tags=["Analytics"])
async def api_stats():
    """Get comprehensive system statistics"""
    db = SessionLocal()
    try:
        clean_expired_reservations(db)
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        return StatsResponse(
            total_accounts=db.query(Account).count(),
            available=db.query(Account).filter(Account.is_given == False).count(),
            given=db.query(Account).filter(Account.is_given == True).count(),
            total_users=db.query(User).count(),
            banned_users=db.query(User).filter(User.is_banned == True).count(),
            active_reservations=db.query(Reservation).filter(Reservation.status == "active").count(),
            claims_today=db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= today_start).count(),
        )
    finally:
        db.close()


@app.get("/api/accounts", tags=["Accounts"])
async def api_accounts(
    config: Optional[str] = Query(None, description="Filter by config name"),
    available_only: bool = Query(False, description="Only available accounts"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List accounts with filtering"""
    db = SessionLocal()
    try:
        query = db.query(Account)
        if config:
            query = query.filter(Account.config_name.ilike(f"%{config}%"))
        if available_only:
            query = query.filter(Account.is_given == False)

        total = query.count()
        accounts = query.offset(offset).limit(limit).all()

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "accounts": [
                {
                    "id": a.id,
                    "config_name": a.config_name,
                    "is_given": a.is_given,
                    "created_at": format_datetime(a.created_at),
                    "quality_score": a.quality_score,
                    "tags": a.tags,
                }
                for a in accounts
            ]
        }
    finally:
        db.close()


@app.get("/api/configs", tags=["Configs"])
async def api_configs():
    """Get config inventory summary"""
    db = SessionLocal()
    try:
        clean_expired_reservations(db)
        results = db.query(
            Account.config_name,
            func.count(Account.id),
            func.sum(func.case([(Account.is_given == False, 1)], else_=0))
        ).group_by(Account.config_name).all()

        return {
            "configs": [
                {
                    "name": r[0],
                    "total": r[1],
                    "available": r[2] or 0,
                }
                for r in results if r[0]
            ]
        }
    finally:
        db.close()


@app.get("/api/users", tags=["Users"])
async def api_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
):
    """List users"""
    db = SessionLocal()
    try:
        query = db.query(User)
        if search:
            query = query.filter(
                (User.user_id == search) | (User.username.ilike(f"%{search}%"))
            )

        total = query.count()
        users = query.order_by(desc(User.last_activity)).offset(offset).limit(limit).all()

        return {
            "total": total,
            "users": [
                {
                    "user_id": u.user_id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "is_banned": u.is_banned,
                    "total_claims": u.total_claims,
                    "reputation_score": u.reputation_score,
                    "last_activity": time_ago(u.last_activity),
                    "join_date": format_datetime(u.join_date),
                }
                for u in users
            ]
        }
    finally:
        db.close()


@app.get("/api/users/{user_id}", tags=["Users"])
async def api_user_detail(user_id: str):
    """Get user details"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        deliveries = db.query(DeliveredAccount).filter(
            DeliveredAccount.user_id == user_id
        ).order_by(desc(DeliveredAccount.delivered_at)).limit(20).all()

        return {
            "user_id": user.user_id,
            "username": user.username,
            "first_name": user.first_name,
            "is_banned": user.is_banned,
            "ban_reason": user.ban_reason,
            "total_claims": user.total_claims,
            "reputation_score": user.reputation_score,
            "join_date": format_datetime(user.join_date),
            "last_activity": time_ago(user.last_activity),
            "recent_claims": [
                {
                    "config_name": d.config_name,
                    "delivered_at": format_datetime(d.delivered_at),
                }
                for d in deliveries
            ]
        }
    finally:
        db.close()


@app.get("/api/audit-log", tags=["Audit"])
async def api_audit_log(
    limit: int = Query(100, ge=1, le=500),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
):
    """Query audit log"""
    db = SessionLocal()
    try:
        query = db.query(AuditLog)
        if user_id:
            query = query.filter(AuditLog.user_id == user_id)
        if action:
            query = query.filter(AuditLog.action == action)

        logs = query.order_by(desc(AuditLog.timestamp)).limit(limit).all()

        return {
            "logs": [
                {
                    "timestamp": format_datetime(l.timestamp),
                    "user_id": l.user_id,
                    "action": l.action,
                    "details": l.details,
                    "ip": l.ip_address,
                }
                for l in logs
            ]
        }
    finally:
        db.close()


@app.get("/api/health", tags=["System"])
async def api_health():
    """System health check"""
    db_status = "healthy"
    ob_status = "unknown"

    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
    except Exception:
        db_status = "unhealthy"

    return {
        "status": "healthy",
        "version": "5.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "services": {
            "database": db_status,
            "openbullet": ob_status,
        },
        "features": {
            "rate_limiting": True,
            "reservations": True,
            "audit_log": True,
            "user_management": True,
            "notifications": True,
        }
    }


# ═══════════════════════════════════════════════════════════
#                    ADMIN API ENDPOINTS
# ═══════════════════════════════════════════════════════════

class BanRequest(BaseModel):
    user_id: str
    reason: str = "No reason provided"
    expires_at: Optional[str] = None  # ISO format or null for permanent


class BroadcastRequest(BaseModel):
    message: str
    filter_type: str = "all"  # all, active, banned


@app.post("/api/admin/ban", tags=["Admin"])
async def admin_ban_user(ban_req: BanRequest, request: Request):
    """Ban a user"""
    db = SessionLocal()
    try:
        existing = db.query(BannedUser).filter(BannedUser.user_id == ban_req.user_id).first()

        expires = None
        if ban_req.expires_at:
            expires = datetime.datetime.fromisoformat(ban_req.expires_at)

        if existing:
            existing.is_active = True
            existing.banned_at = datetime.datetime.utcnow()
            existing.reason = ban_req.reason
            existing.expires_at = expires
        else:
            db.add(BannedUser(
                user_id=ban_req.user_id,
                reason=ban_req.reason,
                expires_at=expires,
            ))

        # Update user
        user = db.query(User).filter(User.user_id == ban_req.user_id).first()
        if user:
            user.is_banned = True
            user.ban_reason = ban_req.reason

        db.commit()
        log_audit(db, None, "admin_ban", f"Banned {ban_req.user_id}: {ban_req.reason}", request)

        return {"status": "success", "user_id": ban_req.user_id, "banned": True}
    finally:
        db.close()


@app.post("/api/admin/unban/{user_id}", tags=["Admin"])
async def admin_unban_user(user_id: str, request: Request):
    """Unban a user"""
    db = SessionLocal()
    try:
        ban = db.query(BannedUser).filter(BannedUser.user_id == user_id).first()
        if ban:
            ban.is_active = False

        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.is_banned = False
            user.ban_reason = None

        db.commit()
        log_audit(db, None, "admin_unban", f"Unbanned {user_id}", request)

        return {"status": "success", "user_id": user_id, "banned": False}
    finally:
        db.close()


@app.post("/api/admin/broadcast", tags=["Admin"])
async def admin_broadcast(broadcast: BroadcastRequest, request: Request):
    """Send broadcast message to users"""
    db = SessionLocal()
    try:
        if broadcast.filter_type == "active":
            users = db.query(User).filter(
                User.last_activity >= datetime.datetime.utcnow() - datetime.timedelta(days=7)
            ).all()
        elif broadcast.filter_type == "banned":
            users = db.query(User).filter(User.is_banned == True).all()
        else:
            users = db.query(User).all()

        sent = 0
        for u in users:
            try:
                await tg_send(u.user_id,
                    f"📢 *إشعار من الإدارة* 📢\n\n{safe_md(broadcast.message)}"
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send broadcast to {u.user_id}: {e}")

        log_audit(db, None, "admin_broadcast", f"Broadcast to {sent} users", request)

        return {"status": "success", "sent": sent, "total": len(users)}
    finally:
        db.close()


@app.post("/api/admin/cleanup", tags=["Admin"])
async def admin_cleanup(request: Request):
    """Clean expired reservations and old data"""
    db = SessionLocal()
    try:
        # Clean expired reservations
        expired_count = clean_expired_reservations(db)

        # Clean old delivered records (keep 30 days)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        old_delivered = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at < cutoff).delete()

        # Clean old audit logs (keep 90 days)
        audit_cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=90)
        old_audit = db.query(AuditLog).filter(AuditLog.timestamp < audit_cutoff).delete()

        db.commit()
        log_audit(db, None, "admin_cleanup", 
            f"Cleaned {expired_count} reservations, {old_delivered} old deliveries, {old_audit} old audit logs", 
            request
        )

        return {
            "status": "success",
            "expired_reservations": expired_count,
            "old_deliveries_removed": old_delivered,
            "old_audit_removed": old_audit,
        }
    finally:
        db.close()


@app.get("/api/admin/dashboard", tags=["Admin"])
async def admin_dashboard():
    """Comprehensive admin dashboard data"""
    db = SessionLocal()
    try:
        clean_expired_reservations(db)
        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - datetime.timedelta(days=7)

        # Core stats
        total_accounts = db.query(Account).count()
        available = db.query(Account).filter(Account.is_given == False).count()
        given = db.query(Account).filter(Account.is_given == True).count()

        # User stats
        total_users = db.query(User).count()
        active_today = db.query(User).filter(User.last_activity >= today_start).count()
        active_week = db.query(User).filter(User.last_activity >= week_ago).count()
        banned = db.query(BannedUser).filter(BannedUser.is_active == True).count()

        # Activity
        claims_today = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= today_start).count()
        claims_week = db.query(DeliveredAccount).filter(DeliveredAccount.delivered_at >= week_ago).count()

        # Reservations
        active_res = db.query(Reservation).filter(Reservation.status == "active").count()

        # Top configs
        top_configs = db.query(
            Account.config_name,
            func.count(Account.id),
            func.sum(func.case([(Account.is_given == False, 1)], else_=0))
        ).group_by(Account.config_name).order_by(desc(func.count(Account.id))).limit(10).all()

        # Recent activity
        recent_audit = db.query(AuditLog).order_by(desc(AuditLog.timestamp)).limit(20).all()

        # Alerts
        alerts = []
        if available <= LOW_STOCK_THRESHOLD:
            alerts.append({"type": "low_stock", "message": f"Low stock: {available} accounts remaining"})
        if banned > 0:
            alerts.append({"type": "banned_users", "message": f"{banned} banned users"})

        return {
            "overview": {
                "total_accounts": total_accounts,
                "available": available,
                "given": given,
                "availability_ratio": round(available / max(total_accounts, 1) * 100, 2),
            },
            "users": {
                "total": total_users,
                "active_today": active_today,
                "active_week": active_week,
                "banned": banned,
            },
            "activity": {
                "claims_today": claims_today,
                "claims_week": claims_week,
                "active_reservations": active_res,
            },
            "top_configs": [
                {"name": c[0], "total": c[1], "available": c[2] or 0}
                for c in top_configs
            ],
            "recent_activity": [
                {
                    "time": format_datetime(a.timestamp),
                    "user": a.user_id,
                    "action": a.action,
                    "details": a.details,
                }
                for a in recent_audit
            ],
            "alerts": alerts,
        }
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
#                    LEGACY & UTILITY ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/ping", tags=["System"])
def keep_alive():
    """Keep-alive endpoint for cron jobs"""
    return {
        "status": "alive",
        "service": "CYBERPUNK_CORE_PRO",
        "version": "5.0.0",
        "timestamp": time.time()
    }


@app.get("/health", tags=["System"])
def health_check():
    """Simple health check"""
    return {"status": "healthy", "version": "5.0.0"}


@app.get("/debug/ob", tags=["Debug"])
async def debug_ob():
    """Comprehensive OpenBullet diagnostics"""
    try:
        headers, auth_label = await get_auth_headers()

        if not headers:
            return {
                "error": "فشلت المصادقة",
                "url": OPENBULLET_URL,
                "key_preview": (OPENBULLET_API_KEY or "")[:10] + "..." if OPENBULLET_API_KEY else "MISSING",
            }

        base = OPENBULLET_URL.strip().rstrip("/")
        result = {
            "auth": auth_label,
            "jobs_list": [],
            "first_active_detail": None,
            "first_active_detail_unwrapped": None,
        }

        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            try:
                resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", data) if isinstance(data, dict) else data
                    result["jobs_list"] = items if isinstance(items, list) else []

                    for j in result["jobs_list"]:
                        if isinstance(j, dict) and _is_active(j.get("status")) and j.get("id") is not None:
                            resp2 = await client.get(f"{base}/api/v1/job/{j['id']}", headers=headers)
                            if resp2.status_code == 200:
                                raw = resp2.json()
                                result["first_active_detail"] = raw
                                result["first_active_detail_unwrapped"] = _unwrap(raw)
                            else:
                                result["first_active_detail"] = {
                                    "http_error": resp2.status_code,
                                    "body": resp2.text[:500]
                                }
                            break
                else:
                    result["list_error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            except Exception as e:
                result["list_error"] = str(e)

        return result

    except Exception as e:
        logger.error(f"/debug/ob error: {e}", exc_info=True)
        return {"fatal_error": str(e)}


@app.get("/debug/job/{job_id}", tags=["Debug"])
async def debug_job(job_id: int):
    """Single job full detail from OB"""
    try:
        if not OPENBULLET_URL or not OPENBULLET_API_KEY:
            return {"error": "متغيرات البيئة غير مكتملة", "url": OPENBULLET_URL, "has_key": bool(OPENBULLET_API_KEY)}

        base = OPENBULLET_URL.strip().rstrip("/")
        headers, auth_label = await get_auth_headers()

        if not headers:
            return {"error": "فشلت المصادقة", "auth_tried": auth_label}

        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            resp = await client.get(f"{base}/api/v1/job/{job_id}", headers=headers)

            result = {
                "job_id": job_id,
                "auth_used": auth_label,
                "http_status": resp.status_code,
                "content_type": resp.headers.get("content-type", "?"),
            }

            if resp.status_code == 200:
                try:
                    raw = resp.json()
                    result["raw_json"] = raw
                    result["unwrapped"] = _unwrap(raw)
                    result["extracted"] = _extract_detail(raw)
                except Exception as e:
                    result["parse_error"] = str(e)
                    result["raw_text"] = resp.text[:2000]
            else:
                result["error_body"] = resp.text[:1000]

            return result

    except httpx.ConnectError:
        return {"error": "فشل الاتصال بالخادم", "url": OPENBULLET_URL}
    except httpx.TimeoutException:
        return {"error": "انتهت مهلة الاتصال (15 ثانية)"}
    except Exception as e:
        logger.error(f"/debug/job/{job_id} error: {e}", exc_info=True)
        return {"error": f"خطأ غير متوقع: {e}"}


@app.post("/setup/webhook", tags=["Setup"])
async def setup_webhook():
    """Configure Telegram webhook"""
    render_url = os.getenv("RENDER_EXTERNAL_URL", "")
    if not render_url:
        return {"error": "RENDER_EXTERNAL_URL not set"}

    webhook_url = f"{render_url.rstrip('/')}/webhook/telegram"
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": webhook_url},
        )
    return resp.json()


# ═══════════════════════════════════════════════════════════
#                    BACKGROUND TASKS
# ═══════════════════════════════════════════════════════════

async def periodic_cleanup():
    """Background task to clean expired reservations"""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            db = SessionLocal()
            try:
                count = clean_expired_reservations(db)
                if count > 0:
                    logger.info(f"🧹 Periodic cleanup: {count} expired reservations removed")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Periodic cleanup error: {e}")


@app.on_event("startup")
async def on_startup():
    """Run on application startup"""
    logger.info("🚀 CYBERPUNK CORE PRO v5.0 started successfully!")
    logger.info(f"🔧 Admin IDs: {ADMIN_IDS}")
    logger.info(f"📊 Features enabled: Rate Limiting, Reservations, Audit Log, User Management")

    # Start background tasks
    asyncio.create_task(periodic_cleanup())


@app.on_event("shutdown")
async def on_shutdown():
    """Run on application shutdown"""
    logger.info("🛑 CYBERPUNK CORE PRO shutting down...")


# ═══════════════════════════════════════════════════════════
#                    MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
