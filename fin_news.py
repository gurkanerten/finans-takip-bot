import asyncio
import aiohttp
import feedparser
import re
import os
import time
import sys
import yfinance as yf
from dotenv import load_dotenv
from openai import AsyncOpenAI
from flask import Flask
from threading import Thread

print("=== DEBUG: kod başladı ===", file=sys.stderr, flush=True)

load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
ALICI_CHAT_ID = os.environ.get("ALICI_CHAT_ID")
HAFIZA_DOSYASI = "gonderilen_haberler.txt"
LOG_DOSYASI = "bot_log.txt"

EKONOMI_KANALLARI = {
    "Bloomberg HT": "https://www.bloomberght.com/rss",
    "Ekonomim": "https://www.ekonomim.com/rss",
    "Finans Gündem": "http://www.finansgundem.com/rss",
    "Dünya": "https://www.dunya.com/rss",
    "Bigpara": "https://bigpara.hurriyet.com.tr/rss/",
    "Investing TR": "https://tr.investing.com/rss/news_25.rss",
    "Investing Kripto": "https://tr.investing.com/rss/news_301.rss",
    "Anadolu Ajansı Ekonomi": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi",
    "Reuters Business": "https://www.reuters.com/arc/outboundfeeds/business/?outputType=xml"
}

app = Flask(__name__)
@app.route('/')
def home(): return "Bot Aktif!"
def run(): app.run(host='0.0.0.0', port=7860)
Thread(target=run, daemon=True).start()

ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

# --- Gerçek BIST hisseleri (BIST100) ---
BIST100 = {
    "THYAO","GARAN","ISCTR","AKBNK","YKBNK","SAHOL","SISE","KCHOL","ASELS","TUPRS",
    "EREGL","BIMAS","PGSUS","TOASO","FROTO","ARCLK","TCELL","TAVHL","EKGYO","KORDS",
    "KOZAL","SASA","HEKTS","PETKM","OYAKC","CCOLA","MGROS","VESTL","TTRAK","DOAS",
    "AEFES","DOHOL","ULKER","IPEKE","BRISA","OTKAR","TKFEN","ALARK","MAVI","BRSAN",
    "KONTR","KARSN","KLMSN","YATAS","CIMSA","GOZDE","BAHON","ATAGS","BUCIM","DAGHL",
    "DMSAS","EGPRO","EUHOL","GOLTS","HALKB","HURGZ","ICBCT","IDAS","ISGYO","IZMDC",
    "KAPLM","KARTN","KERVT","KONYA","KOZAA","KRDMD","KZBGY","LIDER","LOGO","LUKSK",
    "MERIT","METRO","MNDRS","MSFT","NATEN","NTHOL","ODAS","ORGE","PARSN","PEKGY",
    "PRKME","RALYH","RHEAG","RODRG","SANKO","SARKY","SELGD","SKBNK","SNKRN","SNPAM",
    "SUMAS","TBORG","TDGYO","TEBNK","TKNSA","TLMAN","TRCAS","TRGYO","TSPOR","TUCLK",
    "TURGG","USAK","VAKBN","VERUS","YEOTK","YESIL","YGGYO","YONGA","ZOREN"
}
BIST100 = {x.upper() for x in BIST100}

# Cache ve rate limiting
TEMEL_VERI_CACHE = {}
LAST_YF_CALL = 0
YF_MIN_INTERVAL = 2  # saniye

async def get_hisse_temel_veriler(ticker):
    ticker = ticker.upper()
    if ticker not in BIST100:
        print(f"⚠️ {ticker} BIST100'de değil, atlanıyor.", flush=True)
        return None
    if ticker in TEMEL_VERI_CACHE:
        return TEMEL_VERI_CACHE[ticker]
    
    global LAST_YF_CALL
    now = time.time()
    elapsed = now - LAST_YF_CALL
    if elapsed < YF_MIN_INTERVAL:
        await asyncio.sleep(YF_MIN_INTERVAL - elapsed)
    
    def fetch():
        try:
            t = yf.Ticker(f"{ticker}.IS")
            info = t.info
            return {
                "mevcut_fiyat": info.get("currentPrice") or info.get("regularMarketPrice"),
                "fk": info.get("trailingPE") if info.get("trailingPE") and info.get("trailingPE") > 0 else None,
                "pd_dd": info.get("priceToBook") if info.get("priceToBook") and info.get("priceToBook") > 0 else None,
                "piyasa_degeri": info.get("marketCap") if info.get("marketCap") and info.get("marketCap") > 0 else None,
                "hisse_kodu": ticker
            }
        except Exception as e:
            print(f"⚠️ Temel veri hatası ({ticker}): {e}", flush=True)
            return None
    
    veri = await asyncio.to_thread(fetch)
    LAST_YF_CALL = time.time()
    if veri:
        TEMEL_VERI_CACHE[ticker] = veri
    return veri

async def get_hisse_data(ticker):
    ticker = ticker.upper()
    if ticker not in BIST100:
        return "Veri yok"
    
    global LAST_YF_CALL
    now = time.time()
    elapsed = now - LAST_YF_CALL
    if elapsed < YF_MIN_INTERVAL:
        await asyncio.sleep(YF_MIN_INTERVAL - elapsed)
    
    def fetch():
        try:
            t = yf.Ticker(f"{ticker}.IS")
            price = t.fast_info.last_price
            return f"{price:.2f} TL"
        except:
            return "Veri yok"
    
    result = await asyncio.to_thread(fetch)
    LAST_YF_CALL = time.time()
    return result

def escape_markdown(text):
    if not text: return ""
    escape_chars = r'_*\[\]\(\)~`>#\+\-=|\{\}\.!'
    return re.sub(f'([{escape_chars}])', r'\\\1', str(text))

async def telegram_send_message(text, parse_mode="MarkdownV2"):
    """Telegram'a mesaj gönder (aiohttp ile)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": ALICI_CHAT_ID, "text": text, "parse_mode": parse_mode}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=15) as resp:
                result = await resp.json()
                if result.get("ok"):
                    print("✅ Telegram mesajı gönderildi", flush=True)
                    return True
                else:
                    print(f"❌ Telegram API hatası: {result}", flush=True)
                    return False
        except asyncio.TimeoutError:
            print("❌ Telegram isteği zaman aşımı (ağ veya DNS sorunu)", flush=True)
        except Exception as e:
            print(f"❌ Telegram isteği hatası: {type(e).__name__} - {e}", flush=True)
        return False

async def analiz_et(metin):
    ticker_pattern = r'\b([A-Z]{3,4})\b'
    bulunan = re.findall(ticker_pattern, metin.upper())
    hisse_kodu = None
    for aday in bulunan:
        if aday in BIST100:
            hisse_kodu = aday
            break
    
    temel_veriler = await get_hisse_temel_veriler(hisse_kodu) if hisse_kodu else None
    
    prompt = (
        "Sen profesyonel bir finans analistisin. Haberin piyasaya etkisini değerlendir (düşük/orta/yüksek).\n"
        "Kesinlikle tahmin yapma, sadece elindeki verileri kullan.\n\n"
    )
    if temel_veriler and temel_veriler.get("mevcut_fiyat"):
        prompt += f"📊 ELDEKİ VERİLER:\n"
        prompt += f"- Hisse: {temel_veriler['hisse_kodu']}\n"
        prompt += f"- Fiyat: {temel_veriler['mevcut_fiyat']} TL\n"
        prompt += f"- F/K: {temel_veriler['fk'] if temel_veriler['fk'] else 'Veri yok'}\n"
        prompt += f"- PD/DD: {temel_veriler['pd_dd'] if temel_veriler['pd_dd'] else 'Veri yok'}\n"
        if temel_veriler['piyasa_degeri']:
            prompt += f"- Piyasa Değeri: {temel_veriler['piyasa_degeri']/1e9:.2f} Milyar TL\n\n"
        else:
            prompt += "- Piyasa Değeri: Veri yok\n\n"
    else:
        prompt += "⚠️ UYARI: Temel veriler çekilemedi. Sadece haber metnini değerlendir.\n\n"
    
    prompt += (
        "KURALLAR:\n"
        "1. Haberin piyasaya etkisi DÜŞÜK veya hiç yoksa SADECE 'İLGİSİZ' yaz.\n"
        "2. Etkisi ORTA veya YÜKSEK ise şu formatta yanıt ver:\n"
        "   YÖN || ÖZET (en fazla 2 cümle) || HEDEF_FİYAT || POTANSİYEL || GEREKÇE || BIST_KODU\n"
        "   Hedef fiyat yoksa 'Veri yok', potansiyel yoksa 'Belirsiz' yaz.\n"
        "FORMAT: YÖN || ÖZET || HEDEF_FİYAT || POTANSİYEL || GEREKÇE || BIST_KODU\n\n"
        f"HABER: '{metin}'"
    )
    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": prompt}],
            temperature=0.3, timeout=30
        )
        cevap = response.choices[0].message.content.strip()
        print(f"🤖 AI: {cevap[:200]}", flush=True)
        if "İLGİSİZ" in cevap.upper() or "||" not in cevap:
            return None
        parts = [p.strip() for p in cevap.split("||")]
        if len(parts) < 6:
            return None
        # Özeti 2 cümle ile sınırla
        ozet = parts[1]
        if len(ozet.split('.')) > 2:
            parts[1] = '.'.join(ozet.split('.')[:2]) + '.'
        return parts
    except Exception as e:
        print(f"❌ AI hatası: {e}", flush=True)
        return None

async def gonder(kaynak, baslik, *args, link=None):
    for i in range(3):
        try:
            if len(args) < 6:
                return
            v = [escape_markdown(x.strip()) for x in args]
            fiyat = await get_hisse_data(args[5].strip())
            mesaj = (
                f"🚨 *{escape_markdown(kaynak)}*\n\n"
                f"📌 {escape_markdown(baslik)}\n\n"
                f"📊 *Yön:* {v[0]}\n🤖 *Özet:* {v[1]}\n"
                f"📈 *Fiyat:* {escape_markdown(fiyat)}\n🎯 *Hedef:* {v[2]}\n"
                f"🚀 *Potansiyel:* {v[3]}\n💡 *Gerekçe:* {v[4]}\n"
                f"🔗 [Detaylar]({escape_markdown(link)})"
            )
            success = await telegram_send_message(mesaj)
            if success:
                print(f"✅ Mesaj gönderildi: {baslik[:50]}", flush=True)
                return
        except Exception as e:
            print(f"❌ Deneme {i+1} hatası: {e}", flush=True)
        await asyncio.sleep(2)
    print("❌ Mesaj gönderilemedi (tüm denemeler)", flush=True)

async def test_telegram_baslangic():
    await telegram_send_message("🤖 *Finans Takip Botu başarıyla başladı!* \n\nDüzenli olarak haberleri tarıyorum. Uygun BIST haberi geldiğinde buradan bildireceğim.")

async def periyodik_canlilik_mesaji():
    while True:
        await asyncio.sleep(3600)
        await telegram_send_message("🟢 *Canlılık Sinyali* - Bot hâlâ çalışıyor ve haberleri tarıyor. Uygun haber bekleniyor.")

async def tarama_motoru():
    print("🚀 Tarama motoru başlatıldı", flush=True)
    gonderilenler = set()
    if os.path.exists(HAFIZA_DOSYASI):
        with open(HAFIZA_DOSYASI, "r") as f:
            gonderilenler = set(line.strip() for line in f)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            print("🟢 Döngü başladı", file=sys.stderr, flush=True)
            with open(LOG_DOSYASI, "a") as log_f:
                log_f.write(f"{time.ctime()} - Döngü aktif\n")
            print(f"🟢 Tarama {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            
            for ad, url in EKONOMI_KANALLARI.items():
                try:
                    async with session.get(url, timeout=15) as resp:
                        feed = feedparser.parse(await resp.text())
                        for entry in feed.entries[:1]:
                            if entry.link not in gonderilenler:
                                print(f"📰 {ad}: {entry.title[:60]}", flush=True)
                                res = await analiz_et(f"{entry.title}: {entry.get('summary', '')}")
                                if res and len(res) >= 6:
                                    await gonder(ad, entry.title, *res, link=entry.link)
                                    gonderilenler.add(entry.link)
                                    with open(HAFIZA_DOSYASI, "a") as f: f.write(f"{entry.link}\n")
                                else:
                                    print(f"   → Atlanan", flush=True)
                            else:
                                print(f"   → Zaten gönderilmiş", flush=True)
                except Exception as e:
                    print(f"❌ RSS hatası {ad}: {e}", flush=True)
                await asyncio.sleep(2)
            await asyncio.sleep(300)

async def main():
    await test_telegram_baslangic()
    asyncio.create_task(periyodik_canlilik_mesaji())
    await tarama_motoru()

if __name__ == "__main__":
    asyncio.run(main())