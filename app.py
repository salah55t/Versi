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
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def safe_md(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("`", "'").replace("\\", "/")


def resolve_progress(val) -> str:
    """يتعامل مع: 0.45 (نسبة عشرية) أو 45.0 (نسبة مئوية) أو None."""
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    payload.update(kwargs)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=payload)
        except Exception as e:
            logger.error(f"tg_send failed: {e}")


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


# ==================== OPENBULLET API CLIENT ====================


async def _ob_request(client: httpx.AsyncClient, url: str, headers: dict) -> dict:
    """طلب موحّد بإرجاع تفاصيل كاملة."""
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
        result["raw_preview"] = resp.text[:1000]

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
    يجلب البيانات من OpenBullet مع تجربة 3 صيغ مصادقة تلقائياً.
    """
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return {
            "jobs": [],
            "monitors": [],
            "diag_error": "متغيرات OPENBULLET_URL أو OPENBULLET_API_KEY غير معرّفة.",
        }

    base = OPENBULLET_URL.strip().rstrip("/")
    raw_key = OPENBULLET_API_KEY.strip()

    auth_methods = [
        {"Authorization": raw_key},
        {"Authorization": f"Bearer {raw_key}"},
        {"X-API-Key": raw_key},
    ]
    auth_labels = ["مباشر", "Bearer", "X-API-Key"]

    test_url = f"{base}/api/v1/job/all"
    working_headers = None
    working_label = None

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        for i, hdrs in enumerate(auth_methods):
            hdrs["Accept"] = "application/json"
            try:
                resp = await client.get(test_url, headers=hdrs)
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    working_headers = hdrs
                    working_label = auth_labels[i]
                    logger.info(f"✅ Auth #{i+1} ({auth_labels[i]}) WORKS!")
                    break
            except Exception as e:
                logger.error(f"Auth #{i+1} error: {e}")

    if working_headers is None:
        return {
            "jobs": [],
            "monitors": [],
            "diag_error": (
                "فشلت كل صيغ المصادقة.\n\n"
                f"🔗 الرابط: `{base}`\n"
                f"🔑 المفتاح: `{raw_key}`\n\n"
                "**تأكد:**\n"
                "1. OB → Settings → General\n"
                "2. حقل Admin API Key → الصق المفتاح بالضبط\n"
                "3. Save Settings"
            ),
        }

    result = {
        "jobs": [],
        "monitors": [],
        "auth_method": working_label,
        "raw_jobs": None,
        "raw_monitors": None,
    }

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        jd = await _ob_request(client, f"{base}/api/v1/job/all", working_headers)
        if jd["ok"] and jd["data"] is not None:
            result["raw_jobs"] = jd["data"]
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

        md = await _ob_request(client, f"{base}/api/v1/jobmonitor/all", working_headers)
        if md["ok"] and md["data"] is not None:
            result["raw_monitors"] = md["data"]
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


# ==================== OB2 DATA EXTRACTOR ====================


def _unwrap_ob2(obj: dict) -> dict:
    """
    OpenBullet 2 يغلّف البيانات الحقيقية داخل حقل 'value'.
    هذه الدالة تفك هذا الغلاف إذا وُجد.
    
    مثال:
      المدخل: {"id": "abc", "value": {"name": "Job1", "status": "Running"}}
      المخرج: {"id": "abc", "name": "Job1", "status": "Running"}
    """
    if not isinstance(obj, dict):
        return obj

    # إذا فيه حقل value وهو dict → ندمج محتواه مع المستوى الأعلى
    if "value" in obj and isinstance(obj["value"], dict):
        unwrapped = {k: v for k, v in obj.items() if k != "value"}
        unwrapped.update(obj["value"])
        logger.info(f"Unwrapped OB2 'value' field. Keys now: {list(unwrapped.keys())}")
        return unwrapped

    return obj


def _get(obj: dict, *keys, default=None):
    """يبحث عن أول مفتاح موجود في القاموس."""
    for k in keys:
        if obj and k in obj and obj[k] is not None:
            return obj[k]
    return default


def _extract_job_info(job_raw, index: int = 0) -> dict:
    """
    يستخرج معلومات العملية بمرونة تامة.
    
    يتعامل مع:
      - البيانات المغلفة في 'value' (OB2)
      - أسماء حقول مختلفة بين إصدارات OB
      - العمليات المتعددة بنفس الاسم (يضيف رقم)
    """
    if not isinstance(job_raw, dict):
        return None

    # فك غلاف OB2
    job = _unwrap_ob2(job_raw)

    # ---- استخراج الاسم ----
    # OB2 يضع اسم الكونفق أحياناً داخل كائن config
    name = _get(job, "name", "jobName", "configName")
    if not name:
        config_obj = _get(job, "config")
        if isinstance(config_obj, dict):
            name = _get(config_obj, "name", "configName")
    if not name:
        name = f"عملية {index + 1}"

    # ---- استخراج الحالة ----
    status = _get(job, "status", "state", "jobStatus")

    # ---- استخراج التقدم ----
    # OB2 يستخدم completionRatio (0.0 - 1.0) أو percent
    progress = _get(
        job,
        "progress", "completionRatio", "completionRate",
        "percent", "completion", "value"
    )

    # ---- استخراج السرعة ----
    # OB2 يستخدم checkSpeed
    cpm = _get(job, "cpm", "speed", "checkSpeed", "checksPerMinute")

    # ---- استخراج Hits ----
    # OB2 يفرق بين goodCount و customCount
    hits = _get(job, "hits", "hitsCount", "good", "goodCount", "success")
    custom = _get(job, "custom", "customCount", "captured")

    # ---- استخراج إحصائيات إضافية ----
    total_checks = _get(job, "total", "totalChecks", "checked", "dataTested")
    bad = _get(job, "bad", "badCount", "fail", "failed", "toCheck")
    proxies_tested = _get(job, "proxiesTested", "proxyTested")

    # ---- معرّف العملية ----
    job_id = _get(job, "id", "itemId", "guid")

    return {
        "name": str(name).strip(),
        "status": status,
        "progress": progress,
        "cpm": cpm,
        "hits": hits,
        "custom": custom,
        "total": total_checks,
        "bad": bad,
        "proxies_tested": proxies_tested,
        "job_id": job_id,
        "index": index,
        # نحتفظ بالقاموس الأصلي بعد فك الغلاف للتصحيح
        "_all_keys": list(job.keys()),
    }


def _is_active(status_val) -> bool:
    if status_val is None:
        return False
    s = str(status_val).strip().lower()
    return s in ("running", "active", "started", "executing")


def _num(val, fallback=0) -> int:
    """يحول القيمة لرقم بأمان."""
    try:
        return int(float(val)) if val is not None else fallback
    except (TypeError, ValueError):
        return fallback


def format_ob_message(ob_data: dict) -> str:
    """يحوّل بيانات OpenBullet إلى رسالة تلغرام منسّقة."""

    if "diag_error" in ob_data:
        return f"❌ **خطأ في الإعدادات:**\n{ob_data['diag_error']}"

    # ---- استخراج العمليات ----
    raw_jobs = ob_data.get("jobs", [])
    raw_monitors = ob_data.get("monitors", [])

    running_jobs = []
    for i, j in enumerate(raw_jobs):
        info = _extract_job_info(j, i)
        if info and _is_active(info["status"]):
            running_jobs.append(info)

    running_monitors = []
    for i, m in enumerate(raw_monitors):
        info = _extract_job_info(m, i)
        if info and _is_active(info["status"]):
            running_monitors.append(info)

    total_active = len(running_jobs) + len(running_monitors)
    total_all = len(raw_jobs) + len(raw_monitors)

    auth_line = ""
    if ob_data.get("auth_method"):
        auth_line = f"🔑 **المصادقة:** `{ob_data['auth_method']}`\n"

    # ---- لا عمليات نشطة ----
    if total_active == 0:
        return (
            "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n"
            f"{auth_line}"
            f"📊 **إجمالي العمليات المسجلة:** `{total_all}`\n"
            f"🟢 **العمليات النشطة:** `0`\n\n"
            "_لا توجد عمليات فحص نشطة حالياً._"
        )

    # ---- عمليات نشطة ----
    lines = [
        "⚙️ **「 شاشة مراقبة OPENBULLET 」** ⚙️\n",
        auth_line,
        f"⚡ **العمليات النشطة:** `{total_active}` من `{total_all}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # تجميع أسماء متكررة لتمييزها
    name_count = {}
    for j in running_jobs:
        n = j["name"]
        name_count[n] = name_count.get(n, 0) + 1

    name_seen = {}
    for job in running_jobs:
        name = safe_md(job["name"])

        # إذا كان الاسم مكرراً نضيف رقم
        if name_count.get(job["name"], 0) > 1:
            name_seen[job["name"]] = name_seen.get(job["name"], 0) + 1
            name = f"{name} #{name_seen[job['name']]}"

        hits = _num(job["hits"])
        custom = _num(job["custom"])
        total = _num(job["total"])
        bad = _num(job["bad"])
        cpm = _num(job["cpm"])
        progress = resolve_progress(job["progress"])

        lines.append(f"📦 **عملية:** `{name}`")

        # إضافة معرّف قصير إن وُجد
        if job.get("job_id"):
            short_id = str(job["job_id"])[:8]
            lines.append(f"   🆔 المعرف: `{short_id}...`")

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

    for mon in running_monitors:
        name = safe_md(mon["name"])
        hits = _num(mon["hits"])
        custom = _num(mon["custom"])
        total = _num(mon["total"])
        cpm = _num(mon["cpm"])
        progress = resolve_progress(mon["progress"])

        lines.append(f"🔄 **مراقب مستمر:** `{name}`")
        lines.append(f"   📊 التقدم: `{progress}%`")

        if total > 0:
            lines.append(f"   📋 تم فحص: `{total}`")
        lines.append(f"   🎯 Hits: `{hits}`")
        if custom > 0:
            lines.append(f"   ⭐ Custom: `{custom}`")
        lines.append(f"   ⚡ السرعة: `{cpm}` CPM")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ==================== WEBHOOK: HIT FROM OPENBULLET ====================


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

            if data.startswith("claim_cfg:"):
                cfg_h = data.split("claim_cfg:", 1)[1]
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

                if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                    await tg_answer(callback_id, "❌ لقد سحبت حصتك سابقاً!", show_alert=True)
                    return {"status": "ok"}

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

        if text == "/start":
            await tg_send(
                chat_id,
                "🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                "⚡ `الحالة: متصل بالشبكة الآمنة`\n"
                "🎛️ `الواجهة: ثيم التوزيع التفاعلي v4.8`\n\n"
                "🤖 _اضغط على سحب حساب بالأسفل لتفقد الخيارات المتاحة لك..._",
                reply_markup=get_main_keyboard(is_admin),
            )

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

        elif text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
            ob_data = await fetch_ob_status()
            await tg_send(chat_id, format_ob_message(ob_data))

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


# ==================== DEBUG ENDPOINTS ====================


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/debug/ob")
async def debug_ob():
    """
    تشخيص شامل: يعرض البنية الخام + البيانات المستخرجة.
    
    افتحه في المتصفح:
    https://your-app.onrender.com/debug/ob
    
    يُظهر:
      - أول عملية خام كما جاءت من API
      - نفس العملية بعد فك غلاف 'value'
      - البيانات المستخرجة النهائية
    """
    ob_data = await fetch_ob_status()
    raw_jobs = ob_data.get("jobs", [])

    # تحليل أول عملية فقط للتصحيح
    analysis = {}
    if raw_jobs and isinstance(raw_jobs[0], dict):
        original = raw_jobs[0]
        unwrapped = _unwrap_ob2(original)
        extracted = _extract_job_info(raw_jobs[0], 0)

        analysis = {
            "original_keys": list(original.keys()) if isinstance(original, dict) else "not a dict",
            "had_value_wrapper": "value" in original and isinstance(original.get("value"), dict),
            "unwrapped_keys": list(unwrapped.keys()) if isinstance(unwrapped, dict) else "not a dict",
            "extracted_info": extracted,
            # نعرض قيم الحقول المهمة كما هي في الأصل (قبل التحويل)
            "raw_field_values": {
                k: unwrapped.get(k) for k in [
                    "name", "status", "progress", "completionRatio",
                    "cpm", "checkSpeed", "hits", "goodCount",
                    "total", "totalChecks", "bad", "badCount",
                    "custom", "customCount", "config"
                ] if k in unwrapped
            }
        }

    return {
        "config": {
            "url": OPENBULLET_URL,
            "auth": ob_data.get("auth_method"),
        },
        "totals": {
            "jobs_raw_count": len(raw_jobs),
            "monitors_raw_count": len(ob_data.get("monitors", [])),
        },
        "first_job_analysis": analysis,
        "all_jobs_extracted": [_extract_job_info(j, i) for i, j in enumerate(raw_jobs)],
    }


@app.post("/setup/webhook")
async def setup_webhook():
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
