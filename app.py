from fastapi import FastAPI, Request
import httpx
import os
import logging
import hashlib
from datetime import datetime, timedelta
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
    """ينشأ شريط تقدم بصري."""
    try:
        p = float(percent)
    except (TypeError, ValueError):
        p = 0.0
    p = max(0.0, min(100.0, p))
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)


def format_uptime(start_str: str) -> str:
    """يحول وقت البدء لصيغة مقروءة (2h 15m)."""
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
        [
            {"text": "⚡ ⚡ سحب حساب جديد"},
            {"text": "📡 إحصائيات المخزون"},
        ],
        [{"text": "🤖 شاشة مراقبة أوبن بلوت"}],
        [{"text": "🖥️ معلومات الخادم"}],
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ لوحة تحكم المطور"}])
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def get_admin_inline():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "act:reset_delivered"},
                {"text": "🗑️ مسح المخزن", "callback_data": "act:clear_accounts"},
            ],
            [
                {"text": "💥 مسح Hits من OB", "callback_data": "act:clear_ob_hits"},
            ],
            [
                {"text": "🔄 تحديث", "callback_data": "act:refresh_admin"},
            ],
        ]
    }


def get_monitor_inline(jobs: list) -> dict:
    """أزرار تحكم بالعمليات (إيقاف/تشغيل) حسب حالتها."""
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
    buttons.append([{"text": "🔄 تحديث الشاشة", "callback_data": "ctrl:refresh_monitor"}])
    return {"inline_keyboard": buttons}


# ==================== TELEGRAM SENDERS ====================


async def tg_send(chat_id: str, text: str, **kw):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    p.update(kw)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try: await c.post(url, json=p)
        except Exception as e: logger.error(f"tg_send: {e}")


async def tg_edit(chat_id: str, mid: int, text: str, **kw):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    p = {"chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": "Markdown"}
    p.update(kw)
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try: await c.post(url, json=p)
        except Exception as e: logger.error(f"tg_edit: {e}")


async def tg_answer(cid: str, text: str, alert: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            await c.post(url, json={"callback_query_id": cid, "text": text, "show_alert": alert})
        except Exception as e: logger.error(f"tg_answer: {e}")


# ==================== OPENBULLET API ====================


async def _auth() -> tuple:
    """يُرجع (headers, label) الناجحة أو (None, None)."""
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
        async with httpx.AsyncClient(verify=False, timeout=15.0) as c:
            for h, l in methods:
                try:
                    r = await c.get(f"{base}/api/v1/job/all", headers=h)
                    if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                        return h, l
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Auth fatal: {e}")
    return None, None


async def _ob_get(path: str, headers: dict) -> dict:
    """طلب GET آمن يُرجع parsed JSON أو dict بالخطأ."""
    base = OPENBULLET_URL.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as c:
            r = await c.get(f"{base}{path}", headers=headers)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return {"ok": True, "data": r.json()}
            return {"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _ob_post(path: str, headers: dict, body=None) -> dict:
    """طلب POST آمن."""
    base = OPENBULLET_URL.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as c:
            if body is not None:
                r = await c.post(f"{base}{path}", headers=headers, json=body)
            else:
                r = await c.post(f"{base}{path}", headers=headers)
            if r.status_code in (200, 204, 409):
                return {"ok": True, "status": r.status_code}
            return {"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _ob_delete(path: str, headers: dict) -> dict:
    """طلب DELETE آمن."""
    base = OPENBULLET_URL.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as c:
            r = await c.delete(f"{base}{path}", headers=headers)
            if r.status_code in (200, 204):
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _is_active(s) -> bool:
    return str(s).strip().lower() in ("running", "active", "started")


def _g(obj, *keys):
    if not isinstance(obj, dict): return None
    for k in keys:
        if k in obj and obj[k] is not None: return obj[k]
    return None


# ==================== DATA FETCHERS ====================


async def fetch_monitor_data() -> dict:
    """يجلب كل بيانات الشاشة: metrics + jobs + hits."""
    headers, label = await _auth()
    if not headers:
        return {"error": "فشلت المصادقة", "auth": None}

    data = {
        "auth": label,
        "metrics": None,
        "jobs": [],
        "hits": [],
        "configs": [],
    }

    # 1) Metrics
    m = await _ob_get("/api/v1/info/metrics", headers)
    if m["ok"]: data["metrics"] = m["data"]

    # 2) Info (للـ uptime)
    i = await _ob_get("/api/v1/info", headers)
    if i["ok"]: data["info"] = i["data"]

    # 3) Jobs
    j = await _ob_get("/api/v1/job/all", headers)
    if j["ok"]:
        jd = j["data"]
        items = jd.get("items", jd) if isinstance(jd, dict) else jd
        if isinstance(items, list):
            for x in items:
                if isinstance(x, dict) and x.get("id") is not None:
                    data["jobs"].append({
                        "id": x["id"],
                        "name": x.get("name", "?"),
                        "status": x.get("status", "?"),
                    })

    # 4) Recent Hits
    h = await _ob_get("/api/v1/hit/recent", headers)
    if h["ok"]:
        hd = h["data"]
        items = hd.get("items", hd) if isinstance(hd, dict) else hd
        if isinstance(items, list):
            data["hits"] = items[:5]

    # 5) Configs count
    cg = await _ob_get("/api/v1/config/all", headers)
    if cg["ok"]:
        cd = cg["data"]
        items = cd.get("items", cd) if isinstance(cd, dict) else cd
        if isinstance(items, list):
            data["configs"] = items

    return data


async def fetch_server_info() -> dict:
    """يجلب معلومات الخادم فقط."""
    headers, label = await _auth()
    if not headers:
        return {"error": "فشلت المصادقة"}
    
    data = {"auth": label, "info": None, "metrics": None, "configs": [], "jobs_count": 0}

    i = await _ob_get("/api/v1/info", headers)
    if i["ok"]: data["info"] = i["data"]

    m = await _ob_get("/api/v1/info/metrics", headers)
    if m["ok"]: data["metrics"] = m["data"]

    j = await _ob_get("/api/v1/job/all", headers)
    if j["ok"]:
        jd = j["data"]
        items = jd.get("items", jd) if isinstance(jd, dict) else jd
        if isinstance(items, list): data["jobs_count"] = len(items)

    c = await _ob_get("/api/v1/config/all", headers)
    if c["ok"]:
        cd = c["data"]
        items = cd.get("items", cd) if isinstance(cd, dict) else cd
        if isinstance(items, list): data["configs"] = items

    return data


# ==================== FORMATTERS ====================


def format_monitor(data: dict) -> str:
    if "error" in data and not data.get("jobs"):
        return f"❌ **خطأ:**\n{data['error']}"

    jobs = data.get("jobs", [])
    hits = data.get("hits", [])
    metrics = data.get("metrics") or {}
    info = data.get("info") or {}
    auth = data.get("auth", "")

    active = [j for j in jobs if _is_active(j.get("status"))]
    idle = [j for j in jobs if str(j.get("status", "")).lower() == "idle"]
    done = [j for j in jobs if str(j.get("status", "")).lower() == "completed"]

    # ---- Metrics Section ----
    lines = ["⚙️ ═══ **OPENBULLET CONTROL CENTER** ═══\n"]
    
    lines.append("🖥️ **[ SYSTEM STATUS ]**")
    cpu = _g(metrics, "cpuUsage", "cpu", "cpu_percent")
    ram = _g(metrics, "ramUsage", "ram", "ram_percent")
    
    if cpu is not None:
        try:
            cpu_f = float(cpu)
            lines.append(f"├ 💻 CPU: `{make_bar(cpu_f)}` `{cpu_f:.1f}%`")
        except: pass
    else:
        lines.append("├ 💻 CPU: `N/A`")

    if ram is not None:
        try:
            ram_f = float(ram)
            lines.append(f"├ 🧠 RAM: `{make_bar(ram_f)}` `{ram_f:.1f}%`")
        except: pass
    else:
        lines.append("├ 🧠 RAM: `N/A`")

    uptime = _g(info, "startTime", "startTimeUtc", "uptime")
    if uptime:
        lines.append(f"└ ⏱️ Uptime: `{format_uptime(str(uptime))}`")
    
    if auth:
        lines.append(f"🔑 Auth: `{auth}`")

    # ---- Jobs Section ----
    lines.append(f"\n📦 **[ JOBS — {len(active)}/{len(jobs)} Active ]**")
    
    if active:
        for j in active:
            n = safe_md(j["name"])[:25]
            lines.append(f"├ ▶️ `{n}` #{j['id']}")
    if idle:
        for j in idle:
            n = safe_md(j["name"])[:25]
            lines.append(f"├ ⏸️ `{n}` #{j['id']}")
    if done:
        for j in done:
            n = safe_md(j["name"])[:25]
            lines.append(f"└ ✅ `{n}` #{j['id']}")

    # ---- Hits Section ----
    lines.append(f"\n🎯 **[ RECENT HITS — {len(hits)} ]**")
    if hits:
        for i, h in enumerate(hits, 1):
            if isinstance(h, dict):
                cfg = safe_md(_g(h, "configName", "config") or "")
                acc = safe_md(str(_g(h, "data", "account") or ""))[:35]
                if cfg:
                    lines.append(f"├ {i}. `{cfg}` {acc}...")
                else:
                    lines.append(f"├ {i}. {acc}...")
    else:
        lines.append("└ _لم يُصطاد شيء بعد..._")

    lines.append("\n══════════════════════════════")
    return "\n".join(lines)


def format_server_info(data: dict) -> str:
    if "error" in data:
        return f"❌ **خطأ:**\n{data['error']}"

    info = data.get("info") or {}
    metrics = data.get("metrics") or {}
    configs = data.get("configs", [])
    jobs_count = data.get("jobs_count", 0)

    lines = ["🖥️ ═══ **SERVER INFORMATION** ═══\n"]
    
    # Version
    ver = _g(info, "version", "obVersion", "appVersion")
    if ver:
        lines.append(f"🏷️ **Version:** `{safe_md(str(ver))}`")

    # Uptime
    uptime = _g(info, "startTime", "startTimeUtc", "uptime")
    if uptime:
        lines.append(f"⏱️ **Uptime:** `{format_uptime(str(uptime))}`")

    # OS
    os_info = _g(info, "os", "operatingSystem", "osName")
    if os_info:
        lines.append(f"💻 **OS:** `{safe_md(str(os_info))}`")

    # .NET
    dotnet = _g(info, "dotnetVersion", "runtimeVersion")
    if dotnet:
        lines.append(f"🌐 **Runtime:** `{safe_md(str(dotnet))}`")

    lines.append(f"\n📊 **[ RESOURCES ]**")
    
    cpu = _g(metrics, "cpuUsage", "cpu", "cpu_percent")
    ram = _g(metrics, "ramUsage", "ram", "ram_percent")
    total_ram = _g(metrics, "totalRam", "totalMemory")
    used_ram = _g(metrics, "usedRam", "usedMemory")

    if cpu is not None:
        try:
            cpu_f = float(cpu)
            lines.append(f"├ 💻 CPU: `{make_bar(cpu_f, 12)}` `{cpu_f:.1f}%`")
        except: pass

    if ram is not None:
        try:
            ram_f = float(ram)
            lines.append(f"├ 🧠 RAM: `{make_bar(ram_f, 12)}` `{ram_f:.1f}%`")
            if total_ram and used_ram:
                try:
                    gb_total = int(total_ram) / (1024**3)
                    gb_used = int(used_ram) / (1024**3)
                    lines.append(f"└ 📦 `{gb_used:.1f}GB` / `{gb_total:.1f}GB`")
                except: pass
        except: pass

    lines.append(f"\n📦 **[ INVENTORY ]**")
    lines.append(f"├ 🔧 Configs: `{len(configs)}`")
    lines.append(f"├ 📋 Jobs: `{jobs_count}`")
    
    # Config names
    if configs:
        lines.append(f"└ **الكونفيجات:**")
        for c in configs[:6]:
            if isinstance(c, dict):
                cn = safe_md(_g(c, "name", "configName") or "بدون اسم")[:30]
                cid = c.get("id", "?")
                lines.append(f"   • `{cn}` (#{cid})")
        if len(configs) > 6:
            lines.append(f"   ... و `{len(configs) - 6}` أخرى")

    lines.append(f"\n══════════════════════════════")
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

            # ---- سحب حساب ----
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

                acc = (
                    db.query(Account)
                    .filter(Account.config_name == selected, Account.is_given == False)
                    .with_for_update().first()
                )
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

            # ---- تحكم بالعمليات (للجميع) ----
            if cdata.startswith("ctrl:"):
                action_part = cdata.split(":", 2)
                if len(action_part) == 3:
                    action = action_part[1]  # start / stop
                    try:
                        job_id = int(action_part[2])
                    except ValueError:
                        await tg_answer(cid, "❌ معرف غير صحيح", alert=True)
                        return {"status": "ok"}

                    headers, _ = await _auth()
                    if not headers:
                        await tg_answer(cid, "❌ فشلت المصادقة مع OB", alert=True)
                        return {"status": "ok"}

                    if action == "stop":
                        res = await _ob_post(f"/api/v1/job/stop", headers, body=job_id)
                        label = "إيقاف"
                    elif action == "start":
                        res = await _ob_post(f"/api/v1/job/start", headers, body=job_id)
                        label = "تشغيل"
                    else:
                        return {"status": "ok"}

                    if res["ok"]:
                        await tg_answer(cid, f"✅ تم {label} العملية #{job_id}", alert=False)
                    else:
                        await tg_answer(cid, f"❌ فشل {label}: {res.get('error', '?')}", alert=True)

                    # تحديث الشاشة
                    if action != "refresh_monitor":
                        mon = await fetch_monitor_data()
                        await tg_edit(chat_id, mid, format_monitor(mon), reply_markup=get_monitor_inline(mon.get("jobs", [])))

                    return {"status": "ok"}

            # ---- أوامر المطور ----
            if chat_id not in ADMIN_IDS:
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
                headers, _ = await _auth()
                if headers:
                    res = await _ob_delete("/api/v1/hit/clear", headers)
                    if res["ok"]:
                        await tg_answer(cid, "💥 تم مسح الـ Hits من OB!", alert=True)
                    else:
                        await tg_answer(cid, f"❌ فشل: {res.get('error')}", alert=True)
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
                "🎛️ `Interface: v5.0 Final`\n\n"
                "_اختر من القائمة أدناه_",
                reply_markup=get_main_keyboard(is_admin),
            )

        elif text in ("📡 إحصائيات المخزون", "/stats"):
            avail = db.query(Account).filter(Account.is_given == False).count()
            total = db.query(Account).count()
            given = total - avail
            await tg_send(chat_id,
                "📡 ═══ **STORAGE STATS** ═══\n\n"
                f"├ 🟢 **المتوفر:** `{avail}`\n"
                f"├ 🔴 **الموزع:** `{given}`\n"
                f"└ 🟣 **الإجمالي:** `{total}`\n\n"
                "══════════════════════════════",
            )

        elif text in ("⚡ ⚡ سحب حساب جديد", "/get"):
            if db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first():
                await tg_send(chat_id,
                    "🚨 **ACCESS DENIED**\n\n"
                    "❌ _حساب واحد فقط لكل مستخدم_"
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
                    "🚨 **EMPTY VAULT**\n\n"
                    "😔 _لا توجد حسابات متوفرة_"
                )
            else:
                btns = []
                for cn, cnt in results:
                    d = cn if len(cn) <= 38 else cn[:35] + "..."
                    btns.append([{"text": f"🎁 {d} ({cnt})", "callback_data": f"claim_cfg:{config_hash(cn)}"}])
                await tg_send(chat_id,
                    "🎛️ ═══ **SELECT TYPE** ═══\n\n"
                    "_اختر نوع الحساب:_",
                    reply_markup={"inline_keyboard": btns},
                )

        elif text == "🤖 شاشة مراقبة أوبن بلوت":
            mon = await fetch_monitor_data()
            await tg_send(chat_id, format_monitor(mon), reply_markup=get_monitor_inline(mon.get("jobs", [])))

        elif text == "🖥️ معلومات الخادم":
            info = await fetch_server_info()
            await tg_send(chat_id, format_server_info(info))

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
