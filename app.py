from fastapi import FastAPI, Request
import httpx
import os
import logging
import hashlib
from datetime import datetime
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


def make_bar(percent: float, length: int = 10) -> str:
    try:
        p = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        p = 0.0
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)


def format_uptime(start_str: str) -> str:
    try:
        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        delta = datetime.utcnow() - start.replace(tzinfo=None)
        total_sec = int(delta.total_seconds())
        d, rem = divmod(total_sec, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        parts = []
        if d > 0: parts.append(f"{d}d")
        if h > 0: parts.append(f"{h}h")
        if m > 0: parts.append(f"{m}m")
        if not parts: parts.append(f"{s}s")
        return " ".join(parts)
    except Exception:
        return "N/A"


# ==================== KEYBOARDS ====================


def get_main_keyboard(is_admin: bool):
    buttons = [
        [{"text": "⚡ ⚡ سحب حساب جديد"}, {"text": "📡 إحصائيات المخزون"}],
        [{"text": "🤖 شاشة مراقبة أوبن بلوت"}],
        [{"text": "🖥️ معلومات الخادم"}],
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ لوحة تحكم المطور"}])
    return {"keyboard": buttons, "resize_keyboard": True, "one_time_keyboard": False}


def get_admin_inline():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "act:reset_delivered"},
                {"text": "🗑️ مسح المخزن", "callback_data": "act:clear_accounts"},
            ],
            [{"text": "💥 مسح Hits من OB", "callback_data": "act:clear_ob_hits"}],
            [{"text": "🔄 تحديث", "callback_data": "act:refresh_admin"}],
        ]
    }


def get_job_inline(jobs: list) -> dict:
    buttons = []
    row = []
    for j in jobs:
        jid = j.get("id")
        name = (j.get("name") or "Job")[:12]
        status = str(j.get("status", "")).lower()
        if status == "running":
            row.append({"text": f"⏹ {name}#{jid}", "callback_data": f"ctrl:stop:{jid}"})
        elif status in ("idle", "completed", "stopped"):
            row.append({"text": f"▶ {name}#{jid}", "callback_data": f"ctrl:start:{jid}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "🔄 تحديث الشاشة", "callback_data": "ctrl:refresh"}])
    return {"inline_keyboard": buttons}


# ==================== TELEGRAM SENDERS ====================


async def tg_send(chat_id: str, text: str, **kw):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    p.update(kw)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=p)
        except Exception as e:
            logger.error(f"tg_send: {e}")


async def tg_edit(chat_id: str, mid: int, text: str, **kw):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    p = {"chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": "Markdown"}
    p.update(kw)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json=p)
        except Exception as e:
            logger.error(f"tg_edit: {e}")


async def tg_answer(cid: str, text: str, alert: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json={"callback_query_id": cid, "text": text, "show_alert": alert})
        except Exception as e:
            logger.error(f"tg_answer: {e}")


# ==================== OPENBULLET API (سريع وآمن) ====================


async def _auth() -> tuple:
    if not OPENBULLET_URL or not OPENBULLET_API_KEY:
        return None, None
    base = OPENBULLET_URL.strip().rstrip("/")
    key = OPENBULLET_API_KEY.strip()
    methods = [
        ({"Authorization": key, "Accept": "application/json"}, "Direct"),
        ({"Authorization": f"Bearer {key}", "Accept": "application/json"}, "Bearer"),
        ({"X-API-Key": key, "Accept": "application/json"}, "X-API-Key"),
    ]
    try:
        async with httpx.AsyncClient(verify=False, timeout=8.0) as c:
            for h, l in methods:
                try:
                    r = await c.get(f"{base}/api/v1/job/all", headers=h)
                    if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                        return h, l
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Auth: {e}")
    return None, None


async def _ob(path: str, headers: dict, method: str = "GET", body=None, timeout: float = 8.0) -> dict:
    base = OPENBULLET_URL.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout) as c:
            if method == "GET":
                r = await c.get(f"{base}{path}", headers=headers)
            elif method == "POST":
                r = await c.post(f"{base}{path}", headers=headers, json=body) if body else await c.post(f"{base}{path}", headers=headers)
            elif method == "DELETE":
                r = await c.delete(f"{base}{path}", headers=headers)
            else:
                return {"ok": False, "error": "Unknown method"}

            if r.status_code in (200, 204):
                try:
                    return {"ok": True, "data": r.json()}
                except Exception:
                    return {"ok": True, "data": None}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _is_active(s) -> bool:
    return str(s).strip().lower() in ("running", "active", "started")


def _g(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


# ==================== DATA FETCHERS (خفيفة وسريعة) ====================


async def fetch_jobs(headers: dict) -> list:
    """يجلب قائمة العمليات فقط (الأساسية)."""
    r = await _ob("/api/v1/job/all", headers)
    if not r["ok"] or not r["data"]:
        return []
    d = r["data"]
    items = d.get("items", d) if isinstance(d, dict) else d
    if not isinstance(items, list):
        return []
    return [
        {"id": x["id"], "name": x.get("name", "?"), "status": x.get("status", "?")}
        for x in items if isinstance(x, dict) and x.get("id") is not None
    ]


async def fetch_hits(headers: dict) -> list:
    """يجلب آخر Hits."""
    r = await _ob("/api/v1/hit/recent", headers)
    if not r["ok"] or not r["data"]:
        return []
    d = r["data"]
    items = d.get("items", d) if isinstance(d, dict) else d
    return items[:4] if isinstance(items, list) else []


async def fetch_metrics(headers: dict) -> dict:
    """يجلب Metrics و Info."""
    result = {}
    m = await _ob("/api/v1/info/metrics", headers, timeout=5.0)
    if m["ok"] and m["data"]:
        result["metrics"] = m["data"]
    i = await _ob("/api/v1/info", headers, timeout=5.0)
    if i["ok"] and i["data"]:
        result["info"] = i["data"]
    return result


# ==================== FORMATTERS ====================


def format_monitor(jobs: list, hits: list, metrics: dict, auth: str) -> str:
    active = [j for j in jobs if _is_active(j.get("status"))]
    idle = [j for j in jobs if str(j.get("status", "")).lower() == "idle"]
    done = [j for j in jobs if str(j.get("status", "")).lower() == "completed"]

    lines = ["⚙️ ═══ **CONTROL CENTER** ═══\n"]

    # Metrics
    met = metrics.get("metrics") or {}
    inf = metrics.get("info") or {}
    cpu = _g(met, "cpuUsage", "cpu", "cpu_percent")
    ram = _g(met, "ramUsage", "ram", "ram_percent")

    lines.append("🖥️ **[ SYSTEM ]**")
    if cpu is not None:
        try: lines.append(f"├ 💻 CPU: `{make_bar(float(cpu))}` `{float(cpu):.1f}%`")
        except: lines.append("├ 💻 CPU: `N/A`")
    else:
        lines.append("├ 💻 CPU: `N/A`")
    if ram is not None:
        try: lines.append(f"├ 🧠 RAM: `{make_bar(float(ram))}` `{float(ram):.1f}%`")
        except: lines.append("├ 🧠 RAM: `N/A`")
    else:
        lines.append("├ 🧠 RAM: `N/A`")

    up = _g(inf, "startTime", "startTimeUtc", "uptime")
    if up:
        lines.append(f"└ ⏱️ Up: `{format_uptime(str(up))}`")
    if auth:
        lines.append(f"🔑 `{auth}`")

    # Jobs
    lines.append(f"\n📦 **[ JOBS {len(active)}/{len(jobs)} ]**")
    if active:
        for j in active:
            lines.append(f"├ ▶️ `{safe_md(j['name'])[:25]}` #{j['id']}")
    if idle:
        for j in idle:
            lines.append(f"├ ⏸️ `{safe_md(j['name'])[:25]}` #{j['id']}")
    if done:
        for j in done:
            lines.append(f"└ ✅ `{safe_md(j['name'])[:25]}` #{j['id']}")

    # Hits
    lines.append(f"\n🎯 **[ HITS {len(hits)} ]**")
    if hits:
        for i, h in enumerate(hits, 1):
            if isinstance(h, dict):
                cfg = safe_md(_g(h, "configName", "config") or "")
                acc = safe_md(str(_g(h, "data", "account") or ""))[:30]
                if cfg:
                    lines.append(f"├ {i}. `{cfg}` {acc}...")
                else:
                    lines.append(f"├ {i}. {acc}...")
    else:
        lines.append("└ _لا يوجد بعد..._")

    lines.append("\n══════════════════════════════")
    return "\n".join(lines)


def format_server(metrics: dict, auth: str, jobs_count: int) -> str:
    met = metrics.get("metrics") or {}
    inf = metrics.get("info") or {}

    lines = ["🖥️ ═══ **SERVER INFO** ═══\n"]

    ver = _g(inf, "version", "obVersion")
    if ver:
        lines.append(f"🏷️ **Ver:** `{safe_md(str(ver))}`")

    up = _g(inf, "startTime", "startTimeUtc", "uptime")
    if up:
        lines.append(f"⏱️ **Up:** `{format_uptime(str(up))}`")

    os_i = _g(inf, "os", "operatingSystem")
    if os_i:
        lines.append(f"💻 **OS:** `{safe_md(str(os_i))}`")

    lines.append(f"\n📊 **[ RESOURCES ]**")
    cpu = _g(met, "cpuUsage", "cpu", "cpu_percent")
    ram = _g(met, "ramUsage", "ram", "ram_percent")

    if cpu is not None:
        try: lines.append(f"├ 💻 CPU: `{make_bar(float(cpu), 12)}` `{float(cpu):.1f}%`")
        except: pass
    if ram is not None:
        try: lines.append(f"├ 🧠 RAM: `{make_bar(float(ram), 12)}` `{float(ram):.1f}%`")
        except: pass

    lines.append(f"\n📦 **Jobs:** `{jobs_count}`")
    if auth:
        lines.append(f"🔑 Auth: `{auth}`")

    lines.append("\n══════════════════════════════")
    return "\n".join(lines)


# ==================== WEBHOOK: HIT FROM OB ====================


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

        db.add(Account(config_name=config_name or "UNKNOWN", account_data=account_data, captured_data=captured_data, is_given=False))
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        logger.error(f"/webhook/hit: {e}", exc_info=True)
        return {"status": "error"}
    finally:
        db.close()


# ==================== WEBHOOK: TELEGRAM ====================


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()

        # ==================== CALLBACKS ====================
        if "callback_query" in payload:
            cb = payload["callback_query"]
            cid = cb["id"]
            chat_id = str(cb["message"]["chat"]["id"])
            mid = cb["message"]["message_id"]
            cdata = cb["data"]

            # ---- 1) سحب حساب ----
            if cdata.startswith("claim_cfg:"):
                cfg_h = cdata.split("claim_cfg:", 1)[1]
                all_cfgs = db.query(Account.config_name).filter(Account.is_given == False).distinct().all()
                selected = None
                for (name,) in all_cfgs:
                    if config_hash(name) == cfg_h:
                        selected = name
                        break

                if not selected:
                    await tg_answer(cid, "❌ نوع غير موجود!", alert=True)
                    return {"status": "ok"}

                if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                    await tg_answer(cid, "❌ سحبت حصتك مسبقاً!", alert=True)
                    return {"status": "ok"}

                acc = db.query(Account).filter(Account.config_name == selected, Account.is_given == False).with_for_update().first()
                if not acc:
                    await tg_answer(cid, "😔 نفدت!", alert=True)
                    return {"status": "ok"}

                acc.is_given = True
                db.add(DeliveredAccount(user_id=chat_id))
                db.commit()

                await tg_edit(chat_id, mid,
                    "🌌 **⚡ 「 تم السحب بنجاح 」 ⚡** 🌌\n\n"
                    f"📦 **النوع:** `{safe_md(acc.config_name)}`\n\n"
                    f"👤 **الحساب:**\n`{safe_md(acc.account_data)}`\n\n"
                    f"⚙️ **المستخرج:**\n`{safe_md(acc.captured_data)}`\n\n"
                    "🔒 _STATUS: TERMINAL LOCKED_"
                )
                return {"status": "ok"}

            # ---- 2) تحكم بالعمليات ----
            if cdata.startswith("ctrl:"):
                parts = cdata.split(":")
                action = parts[1] if len(parts) > 1 else ""

                # زر تحديث الشاشة
                if action == "refresh":
                    await tg_answer(cid, "🔄 جاري التحديث...", alert=False)
                    headers, auth_label = await _auth()
                    if not headers:
                        await tg_edit(chat_id, mid, "❌ **فشلت المصادقة**")
                        return {"status": "ok"}
                    jobs = await fetch_jobs(headers)
                    hits = await fetch_hits(headers)
                    metrics = await fetch_metrics(headers)
                    await tg_edit(chat_id, mid, format_monitor(jobs, hits, metrics, auth_label), reply_markup=get_job_inline(jobs))
                    return {"status": "ok"}

                # إيقاف / تشغيل
                if action in ("start", "stop") and len(parts) == 3:
                    try:
                        job_id = int(parts[2])
                    except ValueError:
                        await tg_answer(cid, "❌ معرف خاطئ", alert=True)
                        return {"status": "ok"}

                    label = "تشغيل" if action == "start" else "إيقاف"
                    await tg_answer(cid, f"⏳ جاري {label}...", alert=False)

                    headers, _ = await _auth()
                    if not headers:
                        await tg_answer(cid, "❌ فشلت المصادقة", alert=True)
                        return {"status": "ok"}

                    r = await _ob(f"/api/v1/job/{action}", headers, "POST", body=job_id)
                    if r["ok"]:
                        # نُجح العملية، نحدّث الشاشة
                        jobs = await fetch_jobs(headers)
                        hits = await fetch_hits(headers)
                        metrics = await fetch_metrics(headers)
                        await tg_edit(chat_id, mid, format_monitor(jobs, hits, metrics, _), reply_markup=get_job_inline(jobs))
                    else:
                        await tg_answer(cid, f"❌ فشل: {r.get('error', '?')}", alert=True)
                    return {"status": "ok"}

                # fallback
                await tg_answer(cid, "❌ أمر غير معروف", alert=True)
                return {"status": "ok"}

            # ---- 3) أوامر المطور ----
            if chat_id not in ADMIN_IDS:
                await tg_answer(cid, "❌ للمطور فقط", alert=True)
                return {"status": "ok"}

            if cdata == "act:reset_delivered":
                cnt = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.commit()
                await tg_answer(cid, f"🧹 تم تصفير الموزع ({cnt})", alert=True)

            elif cdata == "act:clear_accounts":
                ac = db.query(Account).count()
                dc = db.query(DeliveredAccount).count()
                db.query(DeliveredAccount).delete()
                db.query(Account).delete()
                db.commit()
                await tg_answer(cid, f"🚨 مسح {ac} حساب + {dc} سجل", alert=True)

            elif cdata == "act:clear_ob_hits":
                await tg_answer(cid, "⏳ جاري المسح...", alert=False)
                headers, _ = await _auth()
                if headers:
                    r = await _ob("/api/v1/hit/clear", headers, "DELETE")
                    if r["ok"]:
                        await tg_answer(cid, "💥 تم مسح الـ Hits!", alert=True)
                    else:
                        await tg_answer(cid, f"❌ فشل: {r.get('error')}", alert=True)
                else:
                    await tg_answer(cid, "❌ فشلت المصادقة", alert=True)

            elif cdata == "act:refresh_admin":
                total = db.query(Account).count()
                avail = db.query(Account).filter(Account.is_given == False).count()
                given = db.query(Account).filter(Account.is_given == True).count()
                await tg_edit(chat_id, mid,
                    "🛠️ ═══ **DEVELOPER PANEL** ═══\n\n"
                    f"├ 🟣 **الإجمالي:** `{total}`\n"
                    f"├ 🟢 **الجاهزة:** `{avail}`\n"
                    f"└ 🔴 **الموزعة:** `{given}`\n\n"
                    "══════════════════════════════",
                    reply_markup=get_admin_inline()
                )

            return {"status": "ok"}

        # ==================== MESSAGES ====================
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}

        chat_id = str(payload["message"]["chat"]["id"])
        text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS

        if text == "/start":
            await tg_send(chat_id,
                "🌌 ═══ **CYBERPUNK DISTRIBUTOR** ═══\n\n"
                "⚡ `Status: Connected`\n"
                "🎛️ `Interface: v5.0`\n\n"
                "_اختر من القائمة أدناه_",
                reply_markup=get_main_keyboard(is_admin),
            )

        elif text in ("📡 إحصائيات المخزون", "/stats"):
            avail = db.query(Account).filter(Account.is_given == False).count()
            total = db.query(Account).count()
            given = total - avail
            await tg_send(chat_id,
                "📡 ═══ **STORAGE** ═══\n\n"
                f"├ 🟢 **متوفر:** `{avail}`\n"
                f"├ 🔴 **موزع:** `{given}`\n"
                f"└ 🟣 **إجمالي:** `{total}`\n\n"
                "══════════════════════════════",
            )

        elif text in ("⚡ ⚡ سحب حساب جديد", "/get"):
            if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                await tg_send(chat_id, "🚨 **ACCESS DENIED**\n\n❌ _حساب واحد فقط لكل مستخدم_")
                return {"status": "ok"}

            results = db.query(Account.config_name, func.count(Account.id)).filter(Account.is_given == False).group_by(Account.config_name).all()
            results = [(c, n) for c, n in results if c]

            if not results:
                await tg_send(chat_id, "🚨 **EMPTY VAULT**\n\n😔 _لا توجد حسابات_")
            else:
                btns = []
                for cn, cnt in results:
                    d = cn if len(cn) <= 38 else cn[:35] + "..."
                    btns.append([{"text": f"🎁 {d} ({cnt})", "callback_data": f"claim_cfg:{config_hash(cn)}"}])
                await tg_send(chat_id,
                    "🎛️ ═══ **SELECT TYPE** ═══\n\n_اختر نوع الحساب:_",
                    reply_markup={"inline_keyboard": btns},
                )

        elif text == "🤖 شاشة مراقبة أوبن بلوت":
            await tg_send(chat_id, "⏳ _جاري الاتصال بأوبن بلوت..._")
            headers, auth_label = await _auth()
            if not headers:
                await tg_send(chat_id, "❌ **فشلت المصادقة مع أوبن بلوت**")
            else:
                jobs = await fetch_jobs(headers)
                hits = await fetch_hits(headers)
                metrics = await fetch_metrics(headers)
                msg = format_monitor(jobs, hits, metrics, auth_label)
                await tg_send(chat_id, msg, reply_markup=get_job_inline(jobs))

        elif text == "🖥️ معلومات الخادم":
            await tg_send(chat_id, "⏳ _جاري الاتصال..._")
            headers, auth_label = await _auth()
            if not headers:
                await tg_send(chat_id, "❌ **فشلت المصادقة**")
            else:
                metrics = await fetch_metrics(headers)
                jobs = await fetch_jobs(headers)
                msg = format_server(metrics, auth_label, len(jobs))
                await tg_send(chat_id, msg)

        elif text == "🛠️ لوحة تحكم المطور" and is_admin:
            total = db.query(Account).count()
            avail = db.query(Account).filter(Account.is_given == False).count()
            given = db.query(Account).filter(Account.is_given == True).count()
            await tg_send(chat_id,
                "🛠️ ═══ **DEVELOPER PANEL** ═══\n\n"
                f"├ 🟣 **الإجمالي:** `{total}`\n"
                f"├ 🟢 **الجاهزة:** `{avail}`\n"
                f"└ 🔴 **الموزعة:** `{given}`\n\n"
                "══════════════════════════════",
                reply_markup=get_admin_inline(),
            )

        return {"status": "ok"}

    except Exception as e:
        db.rollback()
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error"}
    finally:
        db.close()


# ==================== UTILITIES ====================


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/setup/webhook")
async def setup_webhook():
    url = os.getenv("RENDER_EXTERNAL_URL", "")
    if not url:
        return {"error": "RENDER_EXTERNAL_URL not set"}
    wh = f"{url.rstrip('/')}/webhook/telegram"
    async with httpx.AsyncClient() as c:
        r = await c.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": wh})
    return r.json()
