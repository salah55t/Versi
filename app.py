from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# ==================== إعدادات البيئة والتكوين ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# معرفات المسؤولين: ضع الـ Chat ID الخاص بك هنا لتظهر لك لوحة التحكم السرية للأدمن
ADMIN_IDS = ["123456789"] 

# جلب رابط قاعدة بيانات ريندر وتعديله ليتوافق مع SQLAlchemy الجديد
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==================== إعدادات قاعدة البيانات (SQLAlchemy) ====================
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 1. جدول الحسابات المخزنة القادمة من الـ Hit
class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String, index=True)
    account_data = Column(String, unique=True, index=True) # unique تمنع تكرار نفس الحساب في القاعدة
    captured_data = Column(String)
    is_given = Column(Boolean, default=False) # True إذا تم تسليمه لمستخدم

# 2. جدول المستخدمين الذين استلموا حصتهم (لمنع التكرار)
class DeliveredAccount(Base):
    __tablename__ = "delivered_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)

# إنشاء الجداول تلقائياً في قاعدة البيانات عند إقلاع التطبيق على ريندر
Base.metadata.create_all(bind=engine)


# ==================== الدالات المساعدة لواجهات البوت ====================

# 1. أزرار القائمة الرئيسية الثابتة (أسفل الشاشة)
def get_main_keyboard(is_admin: bool):
    buttons = [
        [{"text": "🎁 طلب حساب جديد"}, {"text": "📊 فحص المتوفر"}]
    ]
    if is_admin:
        # إضافة زر لوحة التحكم فقط إذا كان المتصل أدمن
        buttons.append([{"text": "⚙️ لوحة تحكم المطور"}])
        
    return {
        "keyboard": buttons,
        "resize_keyboard": True, # لجعل الأزرار متناسقة ومريحة للموبايل
        "one_time_keyboard": False
    }

# 2. الأزرار الشفافة التفاعلية (أسفل رسالة الإحصائيات للأدمن)
def get_inline_control_buttons():
    return {
        "inline_keyboard": [
            [
                {"text": "🧹 تصفير الموزع", "callback_data": "reset_delivered"},
                {"text": "🗑️ حذف كل الحسابات", "callback_data": "clear_accounts"}
            ],
            [
                {"text": "🔄 تحديث الإحصائيات", "callback_data": "refresh_admin_stats"}
            ]
        ]
    }


# ==================== المسارات (Endpoints) ====================

@app.get("/")
def read_root():
    return {"status": "Database & Luxury Bot Bridge is running successfully!"}


# 1. استقبال الـ Hits وتخزينها في قاعدة البيانات
@app.post("/webhook/hit")
async def receive_hit(request: Request):
    db = SessionLocal()
    try:
        data = await request.json()
        
        config_name = data.get("config") or data.get("configName") or data.get("ConfigName") or "Unknown Config"
        account_data = data.get("data", "No Data")
        captured_data = data.get("captured") or data.get("capturedData") or data.get("variables") or "No Captured Data"
        
        # التأكد من أن الحساب لم يتم صيده وتخزينه مسبقاً من قبل
        exists = db.query(Account).filter(Account.account_data == account_data).first()
        if exists:
            return {"status": "ignored", "message": "Account already exists in database"}
            
        # حفظ الحساب كـ "غير موزع بعد"
        new_account = Account(
            config_name=config_name,
            account_data=account_data,
            captured_data=captured_data,
            is_given=False
        )
        db.add(new_account)
        db.commit()
        
        return {"status": "success", "message": "Account secured and stored in Render DB"}
        
    except Exception as e:
        db.rollback()
        print(f"Error saving hit: {str(e)}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# 2. معالجة عمليات بوت تليجرام بالكامل (استقبال + أزرار + توزيع)
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        
        # --- [أ] معالجة ضغطات الأزرار الشفافة Inline Buttons (Callback Queries) ---
        if "callback_query" in payload:
            callback_id = payload["callback_query"]["id"]
            chat_id = str(payload["callback_query"]["message"]["chat"]["id"])
            message_id = payload["callback_query"]["message"]["message_id"]
            data = payload["callback_query"]["data"]
            
            # حماية اللوحة: منع غير الأدمن من ضغط الأزرار الخلفية
            if chat_id not in ADMIN_IDS:
                async with httpx.AsyncClient(verify=False) as client:
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "❌ عذراً، هذا الأمر خاص بالمطور فقط!", "show_alert": True})
                return {"status": "success"}

            async with httpx.AsyncClient(verify=False) as client:
                # أمر تصفير الموزع (السماح للمستخدمين بالطلب مجدداً)
                if data == "reset_delivered":
                    db.query(DeliveredAccount).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🧹 تم تصفير سجل التوزيع! يمكن للجميع سحب حساب جديد الآن.", "show_alert": True})
                
                # أمر حذف كل الحسابات من المخزن
                elif data == "clear_accounts":
                    db.query(Account).delete()
                    db.commit()
                    await client.post(f"{telegram_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "🗑️ تم إفراغ قاعدة البيانات وحذف جميع الحسابات.", "show_alert": True})
                
                # أمر تحديث إحصائيات اللوحة بشكل مباشر
                elif data == "refresh_admin_stats":
                    total_accounts = db.query(Account).count()
                    available_count = db.query(Account).filter(Account.is_given == False).count()
                    delivered_count = db.query(Account).filter(Account.is_given == True).count()
                    
                    updated_text = (
                        f"🛡️ **لوحة تحكم المطور الفاخرة** 🛡️\n\n"
                        f"📦 إجمالي الحسابات بالسيستم: `{total_accounts}`\n"
                        f"🟢 الحسابات الجاهزة للتسليم: `{available_count}`\n"
                        f"🔴 الحسابات التي تم توزيعها: `{delivered_count}`\n\n"
                        f"✨ التحديث: مباشر وتلقائي"
                    )
                    await client.post(f"{telegram_url}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": updated_text, "reply_markup": get_inline_control_buttons(), "parse_mode": "Markdown"})
            
            return {"status": "success"}

        # --- [ب] معالجة الرسائل النصية والضغط على الأزرار الثابتة ---
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        is_admin = chat_id in ADMIN_IDS
        
        # عند إرسال /start لتفعيل الأزرار الثابتة لأول مرة
        if user_text == "/start":
            welcome_text = "✨ **مرحباً بك في بوت التوزيع التلقائي الفاخر!**\n\nقم باستخدام الأزرار الظاهرة أسفل الشاشة للتفاعل مع النظام بكل سهولة 👇"
            payload_data = {
                "chat_id": chat_id,
                "text": welcome_text,
                "reply_markup": get_main_keyboard(is_admin),
                "parse_mode": "Markdown"
            }
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json=payload_data)
                
        # زر فحص المتوفر في المخزن
        elif user_text == "📊 فحص المتوفر" or user_text == "/stats":
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = f"📊 **حالة المخزن الحالي:**\n\n🟢 عدد الحسابات المتاحة للتوزيع فوراً: `{available_count}` حساب."
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # زر طلب حساب جديد (نظام التوزيع العادل)
        elif user_text == "🎁 طلب حساب جديد" or user_text == "/get":
            # التحقق أولاً إن كان المستخدم قد أخذ حساباً مسبقاً
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            
            if already_received:
                reply_text = "❌ **عذراً صديقي!**\n\nلقد استلمت حسابك الخاص سابقاً بنجاح. النظام مصمم ليعطي **حساب واحد فقط لكل شخص** لضمان التوزيع العادل."
            else:
                # جلب أول حساب متوفر في الطابور
                available_account = db.query(Account).filter(Account.is_given == False).first()
                
                if not available_account:
                    reply_text = "😔 **المخزن فارغ حالياً!**\n\nنأسف لك، لا توجد حسابات جاهزة للتسليم في هذه اللحظة. انتظر حتى يتم صيد حسابات جديدة وضخها!"
                else:
                    # تحديث حالة الحساب وحظر المستخدم من الأخذ مجدداً
                    available_account.is_given = True
                    user_claim = DeliveredAccount(user_id=chat_id)
                    db.add(user_claim)
                    db.commit()
                    
                    # صياغة رسالة الحساب بالتنسيق الفاخر (مربعات برمجية قابلة للنسخ بالضغط)
                    reply_text = (
                        f"🎉 **تم تجهيز حسابك الخاص بنجاح!**\n\n"
                        f"📂 **النوع / Config:** `{available_account.config_name}`\n"
                        f"👤 **بيانات الحساب:**\n`{available_account.account_data}`\n\n"
                        f"⚙️ **البيانات المستخرجة (Captured):**\n`{available_account.captured_data}`\n\n"
                        f"⚠️ _تنبيه: تم تسجيل الـ ID الخاص بك، لا يمكنك طلب حساب آخر._"
                    )
                    
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"{telegram_url}/sendMessage", json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"})
                
        # زر لوحة تحكم المطور الفاخرة (للأدمن فقط)
        elif user_text == "⚙️ لوحة تحكم المطور" and is_admin:
            total_accounts = db.query(Account).count()
            available_count = db.query(Account).filter(Account.is_given == False).count()
            delivered_count = db.query(Account).filter(Account.is_given == True).count()
            
            admin_text = (
                f"🛡️ **لوحة تحكم المطور الفاخرة** 🛡️\n\n"
                f"📦 إجمالي الحسابات بالسيستم: `{total_accounts}`\n"
                f"🟢 الحسابات الجاهزة للتسليم: `{available_count}`\n"
                f"🔴 الحسابات التي تم توزيعها: `{delivered_count}`\n\n"
                f"إدارة النظام بالكامل بضغطة زر واحدة عبر الخيارات أدناه 👇"
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
