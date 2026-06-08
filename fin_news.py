"""
Finans Haber Botu - GERT (Railway Uyumlu)
- Railway'de kesintisiz çalışır
- Health check endpoint'i ile Railway'in beklentisini karşılar
"""

import feedparser
import requests
import time
import logging
import hashlib
import re
import os
import random
import threading
from collections import deque
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

# ------------------- KONFIGURASYON -------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8829871684:AAFXcvMFaef3bbZ88Cu4VlwvVfZ7z6WgRts")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003928776122")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-da9c72499f264a8d803984e6a75de5bc")

# RSS kaynaklari
RSS_URLS = [
    "https://news.google.com/rss/search?q=ekonomi+finans+borsa&hl=tr&gl=TR&ceid=TR:tr",
    "https://news.google.com/rss/search?q=BIST+100&hl=tr&gl=TR&ceid=TR:tr",
    "https://www.dunya.com/rss/finans.xml",
    "https://www.ekonomim.com/rss",
    "https://tr.investing.com/rss/news_1063.rss",
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

islenen_hashler = deque(maxlen=500)
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

bot_running = True
last_activity = time.time()

# ------------------- YARDIMCI FONKSIYONLAR -------------------
def haber_ozeti(entry):
    ozet = ""
    if 'summary' in entry:
        ozet = entry.summary
    elif 'description' in entry:
        ozet = entry.description
    elif 'content' in entry and entry.content:
        ozet = entry.content[0].value
    else:
        ozet = entry.title
    ozet = re.sub(r'<[^>]+>', ' ', ozet)
    ozet = re.sub(r'\s+', ' ', ozet).strip()
    return ozet[:500]

def haber_hash(entry):
    benzersiz = entry.title
    if 'published' in entry:
        benzersiz += entry.published
    elif 'updated' in entry:
        benzersiz += entry.updated
    benzersiz += haber_ozeti(entry)[:100]
    return hashlib.md5(benzersiz.encode('utf-8')).hexdigest()

def haber_yeni_mi(entry, max_dakika=60):
    if not hasattr(entry, 'published_parsed') or entry.published_parsed is None:
        return True
    try:
        haber_zamani = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        simdi = datetime.now(timezone.utc)
        fark = (simdi - haber_zamani).total_seconds() / 60
        return fark <= max_dakika
    except:
        return True

def deepseek_analiz(baslik, ozet):
    ozet_kisa = ozet[:500]
    prompt = f"""
Haber Basligi: {baslik}
Haber Ozeti: {ozet_kisa}

Gorev: Bu haberin Turkiye piyasalarina (BIST, doviz, faiz, sektorler) etkisini analiz et.

EN FAZLA 2 CUMLE ile yanit ver:
- "Olumlu, THYAO hissesi yukselebilir."
- "Olumsuz, banka hisseleri baskilanabilir."
- "Etkisiz"

Etkisi yoksa sadece "Etkisiz" yaz.
"""
    try:
        completion = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=150
        )
        yanit = completion.choices[0].message.content.strip()
        if yanit.lower() == "etkisiz":
            return "Etkisiz"
        return yanit
    except Exception as e:
        logging.error(f"DeepSeek hatasi: {e}")
        return "Analiz yapilamadi."

def telegram_bildir(mesaj, max_deneme=3):
    """Telegram'a mesaj gonderir - gelismis yeniden deneme ile"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    
    for deneme in range(max_deneme):
        try:
            timeout = 20 + (deneme * 10)
            response = requests.post(url, json=payload, timeout=timeout)
            if response.status_code == 200:
                logging.info(f"Telegram mesaji gonderildi")
                return True
            else:
                logging.warning(f"Telegram hatasi: {response.status_code}")
        except requests.exceptions.Timeout:
            logging.warning(f"Deneme {deneme+1} zaman asimi ({timeout} sn)")
        except Exception as e:
            logging.warning(f"Deneme {deneme+1} hata: {e}")
        
        if deneme < max_deneme - 1:
            time.sleep(random.uniform(5, 10))
    
    return False

# ------------------- RAILWAY HEALTH CHECK -------------------
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "last_activity": last_activity,
        "uptime_seconds": time.time() - last_activity
    }), 200

def start_web_server():
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ------------------- ANA DONGU -------------------
def ana_dongu():
    global last_activity, bot_running
    logging.info("GERT finans botu basladi (Railway).")
    
    test_mesaj = "🤖 *GERT Bot Basladi*\n\nBot Railway'de calisiyor."
    telegram_bildir(test_mesaj)
    
    while bot_running:
        gonderilen = 0
        for rss_url in RSS_URLS:
            if not bot_running:
                break
            try:
                logging.info(f"Taranıyor: {rss_url}")
                feed = feedparser.parse(rss_url)
                if feed.bozo:
                    continue
                for entry in feed.entries[:3]:
                    if not haber_yeni_mi(entry):
                        continue
                    h = haber_hash(entry)
                    if h in islenen_hashler:
                        continue
                    islenen_hashler.append(h)
                    logging.info(f"Isleniyor: {entry.title}")
                    last_activity = time.time()
                    yorum = deepseek_analiz(entry.title, haber_ozeti(entry))
                    if "etkisiz" in yorum.lower():
                        continue
                    mesaj = f"🚨 *YENİ BİLDİRİM: EKONOMİ HABERİ ANALİZİ*\n\n📌 **{entry.title}**\n\n🤖 {yorum}\n\n🔗 [Habere Git]({entry.link})"
                    if telegram_bildir(mesaj):
                        gonderilen += 1
                    time.sleep(random.uniform(3, 5))
            except Exception as e:
                logging.error(f"RSS hatasi: {e}")
                continue
        logging.info(f"Tur bitti. {gonderilen} haber gonderildi.")
        for _ in range(10):
            if not bot_running:
                break
            time.sleep(30)
            logging.debug("Health check: bot calisiyor")

# ------------------- CALISTIR -------------------
if __name__ == "__main__":
    print("GERT Finans Botu baslatiliyor... (Railway)")
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    time.sleep(2)
    try:
        ana_dongu()
    except KeyboardInterrupt:
        bot_running = False
        print("\nBot durduruldu.")
