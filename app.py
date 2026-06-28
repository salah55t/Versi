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
    """تجزئة MD5 مختصرة (12 حرف) لتجنب تجاوز حد 64 بايت في callback_data."""
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def safe_md(text: str) -> str:
    """يستبدل العلامات التي تكسر Markdown v1 في تلغرام."""
    if not text:
        return ""
    return str(text).replace("`", "'").replace("\\", "/")


def resolve_progress(val) -> str:
    """
    OpenBullet قد يُرجع التقدم كنسبة (0.0-1.0) أو كنسبة مئوية (0-100).
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


async def _ob_request(client: httpx.AsyncClient, url: str, headers: dict) -> dict:
    """
    طلب موحّد يعيد {
        "ok": bool,
        "data": list|dict|None,
        "status_code": int,
        "content_type": str,
        "raw_preview": str,
        "error": str|None
    }
    """
    result = {
        "ok": False,
        "data": None,
        "status_code": 0,
        "content_type": "",
        "raw_preview": "",
        "error": None,
    }
    try:
        resp = await client.get(url, headers=headers)
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("content-type", "")
        result["raw_preview"] = resp.text[:500]

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        try:
            parsed = resp.json()
            result["ok"] = True
            result["data"] = parsed
        except Exception:
            result["error"] = "الرد ليس JSON"

    except httpx.ConnectError:
        result["error"] = "فشل الاتصال (رابط خاطئ أو الخادم متوقف)"
    except httpx.TimeoutException:
        result["error"] = "انتهت مهلة الاتصال (Timeout)"
    except Exception as e:
        result["error"] = str(e)

    return result


async def fetch_ob_status() -> dict:
    """
    يجلب البيانات من OpenBullet مع تجربة 3 صيغ مصادقة تلقائياً:
      1. Authorization: <key>           (طريقة OB2 الأصلية)
      2. Authorization: Bearer <key>     (الطريقة القياسية)
      3. X-API-Key: <key>               (بديل شائع)
    ثم يجلب:
      - /api/v1/job/all         (العمليات العادية)
      - /api/v1/jobmonitor/all  (مراقبات العمليات المستمرة)
    """
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return {
            "jobs": [],
            "monitors": [],
            "diag_error": "متغيرات OPENBULLET_URL أو OPENBULLET_API_KEY غير معرّفة.",
        }

    base = OPENBULLET_URL.strip().rstrip("/")
    raw_key = OPENBULLET_API_KEY.strip()

    # ===== 3 صيغ مختلفة للمصادقة =====
    auth_methods = [
        {"Authorization": raw_key},
        {"Authorization": f"Bearer {raw_key}"},
        {"X-API-Key": raw_key},
    ]
    auth_labels = [
        "مباشر (بدون Bearer)",
        "Bearer",
        "X-API-Key",
    ]

    # ===== تحديد أي صيغة تعمل =====
    test_url = f"{base}/api/v1/job/all"
    working_headers = None
    working_label = None

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        for i, hdrs in enumerate(auth_methods):
            hdrs["Accept"] = "application/json"
            try:
                resp = await client.get(test_url, headers=hdrs)
                logger.info(
                    f"Auth test #{i+1} ({auth_labels[i]}): "
                    f"HTTP {resp.status_code} | "
                    f"type: {resp.headers.get('content-type', '?')}"
                )
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    working_headers = hdrs
                    working_label = auth_labels[i]
                    logger.info(f"✅ Auth method #{i+1} ({auth_labels[i]}) WORKS!")
                    break
                elif resp.status_code == 401:
                    logger.warning(f"Auth method #{i+1} ({auth_labels[i]}) -> 401 rejected")
                else:
                    logger.warning(f"Auth method #{i+1} ({auth_labels[i]}) -> HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"Auth method #{i+1} ({auth_labels[i]}) -> error: {e}")

    # ===== إذا لم تنجح أي صيغة =====
    if working_headers is None:
        return {
            "jobs": [],
            "monitors": [],
            "diag_error": (
                "فشلت كل صيغ المصادقة (3 طرق).\n\n"
                f"🔗 الرابط: `{base}`\n"
                f"🔑 الـ Key: `{raw_key[:8]}...{raw_key[-4:]}`\n\n"
                "**تأكد من:**\n"
                "1. فتح OB → Settings → General\n"
                "2. في حقل **Admin API Key** اكتب نفس المفتاح بالضبط\n"
                "3. اضغط **Save Settings** (أسفل الصفحة)"
            ),
        }

    # ===== جلب البيانات بالصيغة الناجحة =====
    result = {
        "jobs": [],
        "monitors": [],
        "auth_method": working_label,
        "diag_jobs": None,
        "diag_monitors": None,
    }

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:

        # ---- Jobs ----
        jd = await _ob_request(client, f"{base}/api/v1/job/all", working_headers)
        result["diag_jobs"] = jd
        if jd["ok"] and jd["data"] is not None:
            d = jd["data"]
            if isinstance(d, dict) and "items" in d:
                result["jobs"] = d["items"]
            elif isinstance(d, list):
                result["jobs"] = d
            elif isinstance(d, dict):
                for v in d.values():
                    if isinstance(v, list):
                        result["jobs"] = v
                        break

        # ---- Job Monitors ----
        md = await _ob_request(client, f"{base}/api/v1/jobmonitor/all", working_headers)
        result["diag_monitors"] = md
        if md["ok"] and md["data"] is not None:
            d = md["data"]
            if isinstance(d, dict) and "items" in d:
                result["monitors"] = d["items"]
            elif isinstance(d, list):
                result["monitors"] = d
            elif isinstance(d, dict):
                for v in d.values():
                    if isinstance(v, list):
                        result["monitors"] = v
                        break

    return result


def _is_active(status_val) -> bool:
    """يحقق مما إذا كانت الحالة تعني 'قيد التشغيل'."""
    if status_val is None:
        return False
    s = str(status_val).strip().lower()
    return s in ("running", "active", "started", "executing")


def _extract_job_info(job: dict) -> dict:
    """
    يستخرج المعلومات من كائن Job بمرونة.
    OpenBullet قد يُسمّي الحقول بأسماء مختلفة بين الإصدارات.
    """
    def get(*keys):
        for k in keys:
            if k in job and job[k] is not None:
                return job[k]
        return None

    return {
        "name": get("name", "jobName", "configName", "id"),
        "status": get("status", "state", "jobStatus"),
        "progress": get("progress", "completionRate", "percent"),
        "cpm": get("cpm", "speed", "checkSpeed"),
        "hits": get("hits", "hitsCount", "good", "success"),
    }


def format_ob_message(ob_data: dict, show_diag: bool = False) -> str:
    """يحوّل بيانات OpenBullet إلى رسالة تلغرام مع تشخيص ذكي."""

    # ---- خطأ إعدادات ----
    if "diag_error" in ob_data:
        return f"❌ **خطأ في الإعدادات:**\n{ob_data['diag_error']}"

    # ---- تشخيص الاتصال ----
    jd = ob_data.get("diag_jobs") or {}
    md = ob_data.get("diag_monitors") or {}
    jobs_ok = jd.get("ok", False)
    mon_ok = md.get("ok", False)
    auth_method = ob_data.get("auth_method", "")

    # كلاهما فشل
    if not jobs_ok and not mon_ok:
        err_j = jd.get("error", "غير معروف")
        err_m = md.get("error", "غير معروف")
        code_j = jd.get("status_code", "?")
        code_m = md.get("status_code", "?")
        preview = (jd.get("raw_preview") or md.get("raw_preview") or "فارغ")[:200]

        msg = (
            "🚨 **فشل الاتصال بأوبن بلوت** 🚨\n\n"
            f"🔗 **الرابط المستخدم:**\n`{OPENBULLET_URL}`\n\n"
            f"❌ **خطأ /job/all:** `{err_j}` (HTTP {code_j})\n"
            f"❌ **خطأ /jobmonitor/all:** `{err_m}` (HTTP {code_m})\n\n"
        )
        if "JSON" in err_j or "JSON" in err_m:
            msg += (
                "⚠️ **السبب المحتمل:** الخادم رد بنص بدلاً من JSON.\n"
                "1. Admin API Key غير مُفعّل في إعدادات OB\n"
                "2. أو الرابط لا يشير لواجهة OB الصحيحة\n\n"
                f"📄 **أول 200 حرف من الرد:**\n`{safe_md(preview)}`\n\n"
                "💡 OB → Settings → General → فعّل **Admin API Key** → احفظ."
            )
        elif "الاتصال" in err_j or "Connect" in err_j:
            msg += (
                "⚠️ **السبب المحتمل:** تعذّر الوصول للخادم.\n"
                "1. تأكد أن حاوية HuggingFace تعمل وليست نائمة\n"
                "2. تأكد أن الرابط صحيح\n"
                "3. جرب فتح الرابط في المتصفح للتأكد"
            )
        else:
            msg += f"📄 **تفاصيل:**\n`{safe_md(preview)}`"

        if show_diag:
            msg += "\n\n🔍 **[وضع التشخيص مُفعّل]**"
        return msg

    # ---- تحليل العمليات النشطة ----
    running_jobs = []
    for j in ob_data.get("jobs", []):
        if not isinstance(j, dict):
            continue
        info = _extract_job_info(j)
        if _is_active(info["status"]):
            running_jobs.append(info)

    running_monitors = []
    for m in ob_data.get("monitors", []):
        if not isinstance(m, dict):
            continue
        info = _extract_job_info(m)
        if _is_active(info["status"]):
            running_monitors.append(info)

    total_active = len(running_jobs) + len(running_monitors)
    total_all = len(ob_data.get("jobs", [])) + len(ob_data.get("monitors", []))

    auth_line = ""
    if auth_method:
        auth_line = f"🔑 **المصادقة:** `{auth_method}`\n"

    lines = []

    # ---- لا عمليات نشطة ----
    if total_active == 0:
        if total_all == 0 and (not jobs_ok or not mon_ok):
            # API نجح جزئياً لكن البيانات فارغة - مشبوه
            partial_err = ""
            if not jobs_ok:
                partial_err += f"\n⚠️ /job/all فشل: `{jd.get('error')}`"
            if not mon_ok:
                partial_err += f"\n⚠️ /jobmonitor/all فشل: `{md.get('error')}`"
            preview = (jd.get("raw_preview") or md.get("raw_preview") or "")[:150]

            lines = [
                "⚠️ **تنبيه:** لم يتم العثور على عمليات رغم نجاح جزئي للاتصال.",
                f"📊 إجمالي ما تم جلبه: `{total_all}` عملية",
                partial_err,
            ]
            if preview:
                lines.append(f"\n📄 **عينة الرد:**\n`{safe_md(preview)}`")
            lines.append(
                "\n💡 **احتمالات:**\n"
                "1. مسار API مختلف عن المتوقع\n"
                "2. الإصدار لا يدعم هذا الـ Endpoint\n"
                "3. لا توجد عمليات حالياً فعلاً"
            )
            if show_diag:
                lines.append("\n🔍 **[وضع التشخيص مُفعّل]**")
            return "\n".join(lines)

        lines = [
            "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n",
            auth_line,
            f"📊 **إجمالي العمليات المسجلة:** `{total_all}`",
            f"🟢 **العمليات النشطة:** `0`\n",
            "_لا توجد عمليات فحص نشطة حالياً._",
        ]
    else:
        # ---- عمليات نشطة موجودة ----
        lines = [
            "⚙️ **「 شاشة مراقبة OPENBULLET 」** ⚙️\n",
            auth_line,
            f"⚡ **العمليات النشطة:** `{total_active}` من `{total_all}`",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        for job in running_jobs:
            name = safe_md(job["name"] or "بدون اسم")
            lines += [
                f"📦 **عملية عادية:** `{name}`",
                f"   📊 التقدم: `{resolve_progress(job['progress'])}%`",
                f"   ⚡ السرعة: `{job['cpm'] or 0}` CPM",
                f"   🎯 Hits: `{job['hits'] or 0}`",
                "━━━━━━━━━━━━━━━━━━━━",
            ]

        for mon in running_monitors:
            name = safe_md(mon["name"] or "بدون اسم")
            lines += [
                f"🔄 **مراقب مستمر:** `{name}`",
                f"   📊 التقدم: `{resolve_progress(mon['progress'])}%`",
                f"   ⚡ السرعة: `{mon['cpm'] or 0}` CPM",
                f"   🎯 Hits: `{mon['hits'] or 0}`",
                "━━━━━━━━━━━━━━━━━━━━",
            ]

    if show_diag:
        lines.append("\n🔍 **[وضع التشخيص مُفعّل]**")
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
                    "Config.Name", "config", "Config",
                ):
                    config_name = var.get("value", "UNKNOWN")
                    break

        config_name = (
            os.path.basename(str(config_name))
            .replace(".anom", "").replace(".opk", "").strip()
        )
        account_data = str(data.get("data") or data.get("account") or "NO_DATA").strip()
        captured_data = str(
            data.get("captured") or data.get("capturedData")
            or data.get("variables") or "NO_CAPTURED_DATA"
        ).strip()[:5000]

        # تجنب التكرار
        if db.query(Account).filter(Account.account_data == account_data).first():
            return {"status": "ignored"}

        db.add(Account(
            config_name=config_name or "UNKNOWN",
            account_data=account_data,
            captured_data=captured_data,
            is_given=False,
        ))
        db.commit()
        logger.info(f"Hit stored: {config_name}")
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
                    .distinct().all()
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
                    await tg_answer(callback_id, "❌ لقد سحبت حصتك سابقاً!", show_alert=True)
                    return {"status": "ok"}

                # قفل الصف لمنع السباق (Race Condition)
                account = (
                    db.query(Account)
                    .filter(Account.config_name == selected, Account.is_given == False)
                    .with_for_update().first()
                )

                if not account:
                    await tg_answer(callback_id, "😔 نفدت الحسابات من هذا النوع!", show_alert=True)
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
                ac = db.query(Account).count()
                dc = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.query(Account).delete()
                db.commit()
                await tg_answer(callback_id, f"🚨 مسح {ac} حساب و {dc} سجل موزع.", show_alert=True)

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
                    "❌ يسمح النظام بـ **حساب واحد فقط لكل مستخدم**.",
                )
                return {"status": "ok"}

            results = (
                db.query(Account.config_name, func.count(Account.id))
                .filter(Account.is_given == False)
                .group_by(Account.config_name).all()
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
                    buttons.append(
                        [{"text": f"🎁 {display} ({count})", "callback_data": f"claim_cfg:{config_hash(cfg_name)}"}]
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


# ==================== DEBUG & UTILITY ENDPOINTS ====================


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/debug/ob")
async def debug_ob():
    """
    Endpoint لتشخيص مشاكل الاتصال بأوبن بلوت.
    افتحه في المتصفح لترى الرد الخام من OpenBullet.
    """
    ob_data = await fetch_ob_status()
    return {
        "config": {
            "url": OPENBULLET_URL,
            "has_api_key": bool(OPENBULLET_API_KEY),
            "api_key_preview": OPENBULLET_API_KEY[:6] + "..." if OPENBULLET_API_KEY else None,
        },
        "auth_method_used": ob_data.get("auth_method"),
        "jobs_endpoint": ob_data.get("diag_jobs"),
        "monitors_endpoint": ob_data.get("diag_monitors"),
        "parsed_jobs_count": len(ob_data.get("jobs", [])),
        "parsed_monitors_count": len(ob_data.get("monitors", [])),
        "first_job_raw": ob_data.get("jobs", [None])[0] if ob_data.get("jobs") else None,
        "first_monitor_raw": ob_data.get("monitors", [None])[0] if ob_data.get("monitors") else None,
    }


@app.post("/setup/webhook")
async def setup_webhook():
    """
    استدعِ هذا Endpoint مرة واحدة بعد النشر لربط الويب هوك:
    POST https://your-app.onrender.com/setup/webhook
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
