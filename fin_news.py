"""
Finans Haber Botu - GERT (Hugging Face Spaces Uyumlu)
- Hisse ve sektör bazlı analiz
- 2 cümle kuralı
- Health check endpoint'i
- Telegram bildirimleri
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

# Flask için (health check)
try:
    from flask import Flask, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

load_dotenv()

# ------------------- KONFIGURASYON -------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8829871684:AAFXcvMFaef3bbZ88Cu4VlwvVfZ7z6WgRts")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003928776122")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-da9c72499f264a8d803984e6a75de5bc")

# RSS kaynaklari
RSS_URLS = [
    "https://news.google.com/rss/search?q=ekonomi+finans+borsa&hl=tr&gl=TR&ceid=TR:tr",
    "https://news.google.com/rss/search?q=BIST+100&hl=tr&gl=TR&ceid=TR:tr",
    "https://tr.investing.com/rss/news_1063.rss",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.dunya.com/rss/finans.xml",
    "https://www.ekonomim.com/rss",
    "https://ir.thomsonreuters.com/rss/news-releases.xml",
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

islenen_hashler = deque(maxlen=500)
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Health check için global değişkenler
bot_running = True
last_activity = time.time()

# BIST Hisse ve Sektör Listesi (DeepSeek'e örnek olması için)
BIST_HISSELER = {
    "Banka": "GARAN, YKBNK, ISCTR, AKBNK, VAKBNK",
    "Holding": "KCHOL, SAHOL",
    "Enerji": "TUPRS, AKSEN, SASA",
    "Demir-Çelik": "EREGL, ISDMR",
    "Havacılık": "THYAO, PGSUS",
    "Telekom": "TCELL, TTKOM",
    "Gıda": "ULKER, AEFES",
    "Perakende": "BIMAS, MGROS",
    "İnşaat": "ENKAI, EMKEL"
}

def haber_kaynagi(rss_url):
    """RSS URL'sinden kaynak adini alir."""
    if "dunya.com" in rss_url:
        return "Dünya Gazetesi"
    elif "ekonomim.com" in rss_url:
        return "Ekonomim"
    elif "news.google.com" in rss_url:
        return "Google Haberler"
    elif "investing.com" in rss_url:
        return "Investing.com"
    else:
        return "Finans Haber"

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

def deepseek_hisse_analizi(baslik, ozet, kaynak):
    """
    DeepSeek'ten hisse ve sektör bazlı detaylı analiz alir.
    Format:
    📌 Konu: [Kaynak] - [Başlık]
    🤖 Özet: [2 cümle]
    📊 Etki: [🔴 NEGATİF / 🟢 POZİTİF / ⚪ NÖTR]
    🎯 Hisse: [THYAO, EREGL, ...]
    🏭 Sektör: [Banka, Enerji, Holding, ...]
    💡 Dip Not: [2 cümle yatırımcı uyarısı]
    """
    ozet_kisa = ozet[:500]
    
    prompt = f"""
Sen bir finans uzmanısın. Aşağıdaki haberin BIST 100 piyasasına etkisini analiz et.

Haber Kaynağı: {kaynak}
Haber Başlığı: {baslik}
Haber Özeti: {ozet_kisa}

Görev: Bu haberin Türkiye piyasalarına etkisini hisse ve sektör bazında değerlendir.

ASAGIDAKI FORMATTA YANIT VER (başlıkları ve emojileri aynen kullan):

📌 Konu: {kaynak} - {baslik}

🤖 Özet: [EN FAZLA 2 CUMLE ile haberin piyasa etkisini açıkla. Hangi mekanizmayla etkileyeceğini belirt.]

📊 Etki: [sadece BİRİNİ seç: 🔴 NEGATİF veya 🟢 POZİTİF veya ⚪ NÖTR]

🎯 Hisse: [Haberde doğrudan veya dolaylı olarak etkilenmesi beklenen BIST hisse sembollerini virgülle ayırarak yaz. Örnek: THYAO, EREGL, TUPRS, GARAN, KCHOL]

🏭 Sektör: [Haberde etkilenmesi beklenen sektörleri virgülle ayırarak yaz. Örnek: Banka, Enerji, Havacılık, Demir-Çelik, Holding]

💡 Dip Not: [EN FAZLA 2 CUMLE ile yatırımcılara öneri. Nelere dikkat etmeleri gerektiğini belirt.]

KURALLAR:
- Özet ve Dip Not kesinlikle 2 cümleyi geçmesin.
- Hisse ve Sektör bilgilerini mümkün olduğunca gerçekçi ve spesifik yaz.
- Eğer haberin önemli bir etkisi yoksa veya BIST ile ilgisi yoksa sadece "Etkisiz" yaz.
"""
    try:
        completion = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=450
        )
        yanit = completion.choices[0].message.content.strip()
        if yanit.lower() == "etkisiz":
            return "Etkisiz"
        return yanit
    except Exception as e:
        logging.error(f"DeepSeek hatasi: {e}")
        return "Analiz yapilamadi."

def telegram_bildir(mesaj):
    """Telegram'a mesaj gonderir"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            logging.info("Telegram mesaji gonderildi.")
            return True
        else:
            logging.warning(f"Telegram hatasi: {response.status_code}")
            return False
    except requests.exceptions.Timeout:
        logging.warning("Telegram zaman asimi")
        return False
    except Exception as e:
        logging.warning(f"Telegram baglanti hatasi: {e}")
        return False

def test_telegram():
    """Telegram baglantisini test eder"""
    mesaj = "🤖 *GERT Bot Basladi*\n\nHisse ve sektor bazli analiz aktif."
    return telegram_bildir(mesaj)

# ------------------- HEALTH CHECK (FLASK) -------------------
def start_health_check():
    if not FLASK_AVAILABLE:
        logging.info("Flask yok, health check devre disi.")
        return
    
    app = Flask(__name__)
    
    @app.route('/')
    @app.route('/health')
    def health():
        global last_activity, bot_running
        return jsonify({
            "status": "healthy" if bot_running else "stopped",
            "last_activity": last_activity,
            "uptime_seconds": time.time() - last_activity
        }), 200
    
    thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=7860, debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()
    logging.info("Health check endpoint: http://0.0.0.0:7860/health")

# ------------------- ANA DONGU -------------------
def ana_dongu():
    global last_activity, bot_running
    logging.info("GERT finans botu basladi (Hisse ve Sektor Bazli Analiz ile).")
    
    test_telegram()
    
    while bot_running:
        gonderilen = 0
        for rss_url in RSS_URLS:
            if not bot_running:
                break
            
            try:
                logging.info(f"Taranıyor: {rss_url}")
                feed = feedparser.parse(rss_url)
                
                if feed.bozo:
                    logging.warning(f"Feed sorunu: {rss_url}")
                    continue

                for entry in feed.entries[:3]:
                    if not haber_yeni_mi(entry):
                        continue
                    
                    h = haber_hash(entry)
                    if h in islenen_hashler:
                        continue
                    islenen_hashler.append(h)
                    
                    kaynak = haber_kaynagi(rss_url)
                    logging.info(f"Isleniyor: {kaynak} - {entry.title}")
                    last_activity = time.time()
                    
                    analiz = deepseek_hisse_analizi(entry.title, haber_ozeti(entry), kaynak)

                    if "etkisiz" in analiz.lower():
                        logging.info(f"Etkisiz atlandi: {entry.title}")
                        continue

                    # Telegram mesajini olustur
                    mesaj = f"🚨 *YENİ BİLDİRİM: EKONOMİ HABERİ ANALİZİ*\n\n{analiz}\n\n🔗 [Detayları Okumak İçin Tıklayınız]({entry.link})"
                    
                    if telegram_bildir(mesaj):
                        gonderilen += 1
                    
                    time.sleep(random.uniform(3, 5))

            except Exception as e:
                logging.error(f"RSS hatasi {rss_url}: {e}")
                continue
        
        last_activity = time.time()
        logging.info(f"Tur bitti. {gonderilen} haber gonderildi. 5 dakika bekleniyor...")
        
        for _ in range(10):
            if not bot_running:
                break
            time.sleep(30)
            logging.debug("Health check: bot calisiyor")

# ------------------- CALISTIR -------------------
if __name__ == "__main__":
    print("GERT Finans Botu baslatiliyor... (Hisse ve Sektor Bazli Analiz)")
    
    start_health_check()
    time.sleep(2)
    
    try:
        ana_dongu()
    except KeyboardInterrupt:
        bot_running = False
        print("\nBot durduruldu.")
    except Exception as e:
        bot_running = False
        logging.critical(f"Hata: {e}")
