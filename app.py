from fastapi import FastAPI, Request
import httpx
import os
import logging
import hashlib
from sqlalchemy import create_engine, Column, Integer, String, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("OB_Bridge")

app = FastAPI()

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "6624995237").split(",")

OPENBULLET_URL = os.getenv("OPENBULLET_URL")
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY")

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==================== DATABASE ====================
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String, index=True)
    account_data = Column(String, unique=True, index=True)
    captured_data = Column(String)
    is_given = Column(Boolean, default=False)


class DeliveredAccount(Base):
    __tablename__ = "delivered_accounts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)


Base.metadata.create_all(bind=engine)

# ==================== HELPERS ====================


def config_hash(name: str) -> str:
    """
    يُنشئ تجزئة قصيرة (12 حرف) لاسم الكونفق
    لأن تلغرام يحدد callback_data بـ 64 بايت كحد أقصى.
    """
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def safe_md(text: str) -> str:
    """يستبدل العلامات التي تكسر Markdown v1 في تلغرام."""
    if not text:
        return ""
    return str(text).replace("`", "'").replace("\\", "/")


def resolve_progress(val) -> str:
    """
    OpenBullet قد يُرجع التقدم كنسبة (0.0 - 1.0) أو كنسبة مئوية (0 - 100).
    هذه الدالة تتعامل مع الحالتين.
    """
    try:
        p = float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return "0.0"
    if p <= 1.0:
        return f"{p * 100:.1f}"
    return f"{p:.1f}"


# ==================== KEYBOARDS ====================


def get_main_keyboard(is_admin: bool):
    buttons = [
        [
            {"text": "⚡ 🧬 سحب حساب جديد 🧬 ⚡"},
            {"text": "📡 🌐 إحصائيات المخزن 🌐 📡"},
        ],
        [{"text": "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖"}],
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ 👾 لوحة تحكم المطور 👾 🛠️"}])
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def get_inline_control_buttons():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "reset_delivered"},
                {"text": "🚨 تصفير المخزن بالكامل", "callback_data": "clear_accounts"},
            ],
            [
                {"text": "🔄 تحديث بيانات اللوحة", "callback_data": "refresh_admin_stats"}
            ],
        ]
    }


# ==================== TELEGRAM SENDER WRAPPERS ====================


async def tg_send(chat_id: str, text: str, **kwargs):
    """إرسال رسالة جديدة."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    payload.update(kwargs)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_send failed: {e}")


async def tg_edit(chat_id: str, message_id: int, text: str, **kwargs):
    """تعديل رسالة موجودة."""
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
    """الرد على callback query."""
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


# ==================== OPENBULLET API CLIENT ====================


async def fetch_ob_status() -> dict:
    """
    يجلب البيانات من OpenBullet عبر المسارات الصحيحة:
      - /api/v1/job/all        (العمليات العادية)
      - /api/v1/jobmonitor/all  (مراقبات العمليات المستمرة)
    حسب ما تظهره واجهة API في الصورة المرفقة.
    """
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return {"error": "متغيرات OPENBULLET_URL أو OPENBULLET_API_KEY غير معرّفة."}

    base = OPENBULLET_URL.strip().rstrip("/")
    headers = {
        "Authorization": f"Bearer {OPENBULLET_API_KEY.strip()}",
        "Accept": "application/json",
    }

    result = {"jobs": [], "monitors": []}

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        # ---- 1) جلب Jobs ----
        try:
            resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
            logger.info(f"OB /job/all -> {resp.status_code}")
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                data = resp.json()
                # OpenBullet قد يُرجع {"items": [...]} أو مصفوفة مباشرة
                if isinstance(data, dict) and "items" in data:
                    result["jobs"] = data["items"]
                elif isinstance(data, list):
                    result["jobs"] = data
                else:
                    logger.warning(f"Unexpected /job/all format: {type(data)}")
            else:
                logger.warning(f"/job/all bad response: {resp.status_code} | {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Error fetching /job/all: {e}")
            result["jobs_error"] = str(e)

        # ---- 2) جلب Job Monitors ----
        try:
            resp = await client.get(f"{base}/api/v1/jobmonitor/all", headers=headers)
            logger.info(f"OB /jobmonitor/all -> {resp.status_code}")
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                data = resp.json()
                if isinstance(data, dict) and "items" in data:
                    result["monitors"] = data["items"]
                elif isinstance(data, list):
                    result["monitors"] = data
                else:
                    logger.warning(f"Unexpected /jobmonitor/all format: {type(data)}")
            else:
                logger.warning(
                    f"/jobmonitor/all bad response: {resp.status_code} | {resp.text[:200]}"
                )
        except Exception as e:
            logger.error(f"Error fetching /jobmonitor/all: {e}")
            result["monitors_error"] = str(e)

    return result


def format_ob_message(ob_data: dict) -> str:
    """يحوّل بيانات OpenBullet الخام إلى رسالة تلغرام منسّقة."""
    if "error" in ob_data:
        return f"❌ **خطأ في الإعدادات:**\n`{ob_data['error']}`\n\n💡 تحقق من متغيرات البيئة في Render."

    # الحالات التي تعني "قيد التشغيل" (حالة صغيرة للمقارنة)
    active_statuses = {"running", "active", "started"}

    running_jobs = [
        j for j in ob_data.get("jobs", [])
        if isinstance(j, dict) and str(j.get("status", "")).lower() in active_statuses
    ]
    running_monitors = [
        m for m in ob_data.get("monitors", [])
        if isinstance(m, dict) and str(m.get("status", "")).lower() in active_statuses
    ]

    total_active = len(running_jobs) + len(running_monitors)
    total_all = len(ob_data.get("jobs", [])) + len(ob_data.get("monitors", []))

    if total_active == 0:
        return (
            f"💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n"
            f"📊 **إجمالي العمليات المسجلة:** `{total_all}`\n"
            f"🟢 **العمليات النشطة:** `0`\n\n"
            f"_لا توجد عمليات فحص نشطة حالياً على الخادم._"
        )

    lines = [
        "⚙️ **「 شاشة مراقبة OPENBULLET 」** ⚙️\n",
        f"⚡ **العمليات النشطة:** `{total_active}` من `{total_all}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for job in running_jobs:
        name = safe_md(job.get("name", "بدون اسم"))
        lines += [
            f"📦 **عملية عادية:** `{name}`",
            f"   📊 التقدم: `{resolve_progress(job.get('progress'))}%`",
            f"   ⚡ السرعة: `{job.get('cpm', 0) or 0}` CPM",
            f"   🎯 Hits: `{job.get('hits', 0) or 0}`",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

    for mon in running_monitors:
        name = safe_md(mon.get("name", "بدون اسم"))
        lines += [
            f"🔄 **مراقب مستمر:** `{name}`",
            f"   📊 التقدم: `{resolve_progress(mon.get('progress'))}%`",
            f"   ⚡ السرعة: `{mon.get('cpm', 0) or 0}` CPM",
            f"   🎯 Hits: `{mon.get('hits', 0) or 0}`",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

    return "\n".join(lines)


# ==================== WEBHOOK: HIT FROM OPENBULLET ====================


@app.post("/webhook/hit")
async def receive_hit(request: Request):
    db = SessionLocal()
    try:
        data = await request.json()

        # استخراج اسم الكونفق بطرق متعددة
        config_name = "UNKNOWN"
        for key in ("config", "configName", "ConfigName"):
            if data.get(key):
                config_name = data.get(key)
                break
        if config_name == "UNKNOWN" and data.get("variables"):
            for var in data.get("variables", []):
                if isinstance(var, dict) and var.get("name") in (
                    "Config.Name",
                    "config",
                    "Config",
                ):
                    config_name = var.get("value", "UNKNOWN")
                    break

        config_name = (
            os.path.basename(str(config_name))
            .replace(".anom", "")
            .replace(".opk", "")
            .strip()
        )
        account_data = str(data.get("data") or data.get("account") or "NO_DATA").strip()
        captured_data = str(
            data.get("captured") or data.get("capturedData") or data.get("variables") or "NO_CAPTURED_DATA"
        ).strip()[:5000]  # حد أقصى لتجنب تخزين بيانات ضخمة

        # تجنب التكرار
        exists = db.query(Account).filter(Account.account_data == account_data).first()
        if exists:
            return {"status": "ignored"}

        new_acc = Account(
            config_name=config_name or "UNKNOWN",
            account_data=account_data,
            captured_data=captured_data,
            is_given=False,
        )
        db.add(new_acc)
        db.commit()
        logger.info(f"New hit stored: {config_name}")
        return {"status": "success"}

    except Exception as e:
        db.rollback()
        logger.error(f"/webhook/hit error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ==================== WEBHOOK: TELEGRAM ====================


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()

        # ===================== CALLBACK QUERY =====================
        if "callback_query" in payload:
            cb = payload["callback_query"]
            callback_id = cb["id"]
            chat_id = str(cb["message"]["chat"]["id"])
            message_id = cb["message"]["message_id"]
            data = cb["data"]

            # ---------- سحب حساب عبر التجزئة ----------
            if data.startswith("claim_cfg:"):
                cfg_h = data.split("claim_cfg:", 1)[1]

                # البحث عن الكونفق المطابق للتجزئة
                all_cfgs = (
                    db.query(Account.config_name)
                    .filter(Account.is_given == False)
                    .distinct()
                    .all()
                )
                selected = None
                for (name,) in all_cfgs:
                    if config_hash(name) == cfg_h:
                        selected = name
                        break

                if not selected:
                    await tg_answer(callback_id, "❌ لم يتم العثور على هذا النوع!", show_alert=True)
                    return {"status": "ok"}

                # هل سبق له السحب؟
                if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                    await tg_answer(
                        callback_id,
                        "❌ حظر: لقد سحبت حصتك سابقاً!",
                        show_alert=True,
                    )
                    return {"status": "ok"}

                # قفل الصف لمنع السباق (Race Condition)
                account = (
                    db.query(Account)
                    .filter(Account.config_name == selected, Account.is_given == False)
                    .with_for_update()
                    .first()
                )

                if not account:
                    await tg_answer(
                        callback_id,
                        "😔 نفدت الحسابات من هذا النوع حالياً!",
                        show_alert=True,
                    )
                    return {"status": "ok"}

                account.is_given = True
                db.add(DeliveredAccount(user_id=chat_id))
                db.commit()

                reply = (
                    f"🌌 **⚡ 「 تم سحب الحساب بنجاح 」 ⚡** 🌌\n\n"
                    f"📦 **نوع الخدمة:** `{safe_md(account.config_name)}`\n\n"
                    f"👤 **بيانات الحساب:**\n`{safe_md(account.account_data)}`\n\n"
                    f"⚙️ **البيانات المستخرجة:**\n`{safe_md(account.captured_data)}`\n\n"
                    f"🔒 _STATUS: TERMINAL LOCKED_"
                )
                await tg_edit(chat_id, message_id, reply)
                return {"status": "ok"}

            # ---------- أوامر المطور ----------
            if chat_id not in ADMIN_IDS:
                return {"status": "ok"}

            if data == "reset_delivered":
                count = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.commit()
                await tg_answer(callback_id, f"🧹 تم تصفير الموزع ({count} سجل)", show_alert=True)

            elif data == "clear_accounts":
                acc_count = db.query(Account).count()
                del_count = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.query(Account).delete()
                db.commit()
                await tg_answer(
                    callback_id,
                    f"🚨 تم مسح {acc_count} حساب و {del_count} سجل موزع.",
                    show_alert=True,
                )

            elif data == "refresh_admin_stats":
                total = db.query(Account).count()
                avail = db.query(Account).filter(Account.is_given == False).count()
                given = db.query(Account).filter(Account.is_given == True).count()
                txt = (
                    f"┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n"
                    f"│\n"
                    f"├── 🟣 **إجمالي الحسابات:** `{total}`\n"
                    f"├── 🟢 **الحسابات الجاهزة:** `{avail}`\n"
                    f"└── 🔴 **الحسابات الموزعة:** `{given}`\n"
                    f"│\n"
                    f"└────────────── [ تحديث مباشر ] 🖥️"
                )
                await tg_edit(chat_id, message_id, txt, reply_markup=get_inline_control_buttons())

            return {"status": "ok"}

        # ===================== TEXT MESSAGE =====================
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}

        chat_id = str(payload["message"]["chat"]["id"])
        text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS

        # ---- /start ----
        if text == "/start":
            await tg_send(
                chat_id,
                "🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                "⚡ `الحالة: متصل بالشبكة الآمنة`\n"
                "🎛️ `الواجهة: ثيم التوزيع التفاعلي v4.8`\n\n"
                "🤖 _اضغط على سحب حساب بالأسفل لتفقد الخيارات المتاحة لك..._",
                reply_markup=get_main_keyboard(is_admin),
            )

        # ---- إحصائيات ----
        elif text in ("📡 🌐 إحصائيات المخزن 🌐 📡", "/stats"):
            avail = db.query(Account).filter(Account.is_given == False).count()
            await tg_send(
                chat_id,
                "┌─── 📡 **「 مستودع البيانات 」** 📡\n"
                "│\n"
                f"└── 🟢 **المتوفر الإجمالي:** `{avail}` حساب\n"
                "│\n"
                "└───────────── [ مصفوفة حية ] ⚡",
            )

        # ---- سحب حساب ----
        elif text in ("⚡ 🧬 سحب حساب جديد 🧬 ⚡", "/get"):
            if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                await tg_send(
                    chat_id,
                    "🚨 **SYSTEM DENIAL:** `جدار الحماية نشط` 🚨\n\n"
                    "❌ يسمح النظام بـ **حساب واحد فقط لكل مستخدم** لضمان العدالة.",
                )
                return {"status": "ok"}

            results = (
                db.query(Account.config_name, func.count(Account.id))
                .filter(Account.is_given == False)
                .group_by(Account.config_name)
                .all()
            )
            results = [(c, n) for c, n in results if c]

            if not results:
                await tg_send(
                    chat_id,
                    "🚨 **MAINFRAME ERROR:** `المستودع فارغ حالياً` 🚨\n\n"
                    "😔 لا توجد حسابات جاهزة للتسليم.",
                )
            else:
                buttons = []
                for cfg_name, count in results:
                    display = cfg_name if len(cfg_name) <= 40 else cfg_name[:37] + "..."
                    h = config_hash(cfg_name)
                    buttons.append(
                        [{"text": f"🎁 {display} ({count})", "callback_data": f"claim_cfg:{h}"}]
                    )
                await tg_send(
                    chat_id,
                    "┌─── 🎛️ **「 قائمة التخصيص والتعيين 」** 🎛️\n"
                    "│\n"
                    "├── ⚡ تم فحص قاعدة البيانات وتجميع الأنواع المتوفرة.\n"
                    "└── 👇 **اختر نوع الحساب الذي ترغب بسحبه:**",
                    reply_markup={"inline_keyboard": buttons},
                )

        # ---- عمليات أوبن بلوت ----
        elif text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
            ob_data = await fetch_ob_status()
            await tg_send(chat_id, format_ob_message(ob_data))

        # ---- لوحة المطور ----
        elif text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
            total = db.query(Account).count()
            avail = db.query(Account).filter(Account.is_given == False).count()
            given = db.query(Account).filter(Account.is_given == True).count()
            await tg_send(
                chat_id,
                "┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n"
                "│\n"
                f"├── 🟣 **إجمالي الحسابات:** `{total}`\n"
                f"├── 🟢 **الحسابات الجاهزة:** `{avail}`\n"
                f"└── 🔴 **الحسابات الموزعة:** `{given}`\n"
                "│\n"
                "└────────────── [ أوامر النظام التفاعلية ] 👇",
                reply_markup=get_inline_control_buttons(),
            )

        return {"status": "ok"}

    except Exception as e:
        db.rollback()
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ==================== UTILITY ENDPOINTS ====================


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/setup/webhook")
async def setup_webhook(request: Request):
    """
    استدعِ هذا Endpoint مرة واحدة بعد النشر لربط الويب هوك:
    POST https://your-render-app.onrender.com/setup/webhook
    """
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
