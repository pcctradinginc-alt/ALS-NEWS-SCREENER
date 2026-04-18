import feedparser
import datetime
import smtplib
import json
import os
import logging
import urllib.parse
import anthropic
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- KONFIGURATION ---
GMAIL_USER = os.environ.get('GMAIL_USER')
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY')

if not ANTHROPIC_KEY:
    raise ValueError("❌ ANTHROPIC_API_KEY fehlt!")

# --- AKTUALISIERTE MODELL-IDs (Stand April 2026) ---
PRIMARY_MODEL = "claude-haiku-4-5-20251001"
BACKUP_MODEL  = "claude-sonnet-4-6"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def call_ai_model(title, snippet):
    prompt = f"Fasse diese ALS-Forschung kurz in 2 Sätzen zusammen (Patientenfokus):\n{title}\n{snippet}"
   
    for model_id in [PRIMARY_MODEL, BACKUP_MODEL]:
        try:
            logging.info(f"→ KI-Analyse mit {model_id}...")
            response = client.messages.create(
                model=model_id,
                max_tokens=300,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
                timeout=25.0
            )
            logging.info(f"✅ Erfolg mit {model_id}")
            return response.content[0].text.strip()
        except Exception as e:
            logging.warning(f"⚠️ Fehler bei {model_id}: {e}")
            continue
           
    return "Zusammenfassung aktuell nicht verfügbar."


def get_news():
    db_file = Path('sent_articles.json')
    seen_urls = []
   
    if db_file.exists():
        try:
            content = db_file.read_text().strip()
            if content:
                seen_urls = json.loads(content).get("hashes", [])
        except:
            seen_urls = []
   
    queries = [
        'site:fda.gov ALS "Phase 3"',
        'ALS "Pivotal" results "Phase 3"'
    ]
   
    found_items = []
    for q in queries:
        logging.info(f"Suche: {q}")
        feed = feedparser.parse(f"https://news.google.com/rss?q={urllib.parse.quote(q)}")
       
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                text = entry.title.lower()
                if any(x in text for x in ["phase 3", "phase iii", "pivotal", "fda", "approval"]):
                    logging.info(f"High-Score News gefunden: {entry.title[:60]}...")
                    summary = call_ai_model(entry.title, getattr(entry, 'summary', ''))
                    found_items.append({
                        'title': entry.title,
                        'link': link,
                        'ai_summary': summary
                    })
                seen_urls.append(link)
   
    db_file.write_text(json.dumps({"hashes": seen_urls[-500:]}))
    return found_items[:10]


def send_email(items):
    if not items:
        logging.info("Keine neuen relevanten News gefunden.")
        return
       
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Update – {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT or GMAIL_USER

    # === MODERNES APPLE + WISSENSCHAFTLICHES DESIGN ===
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin:0; padding:0; background:#f5f5f7; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
        <div style="max-width: 620px; margin: 30px auto; background:#ffffff; border-radius: 20px; overflow:hidden; box-shadow: 0 15px 35px rgba(0,0,0,0.08);">
            
            <!-- HEADER -->
            <div style="background: linear-gradient(90deg, #0071e3, #00a2ff); padding: 35px 30px; text-align:center; color:white;">
                <h1 style="margin:0; font-size:26px; font-weight:600; letter-spacing:-0.5px;">
                    🧬 ALS Research Update
                </h1>
                <p style="margin:8px 0 0; font-size:15px; opacity:0.95;">
                    Wichtige Phase-3 &amp; FDA Entwicklungen • {datetime.date.today().strftime('%d.%m.%Y')}
                </p>
            </div>

            <!-- CONTENT -->
            <div style="padding: 30px 30px 10px;">
    """

    for item in items:
        html += f"""
                <!-- NEWS CARD -->
                <div style="margin-bottom: 28px; padding: 24px; background:#f8f9fa; border-radius: 16px; border-left: 5px solid #0071e3;">
                    <a href="{item['link']}" target="_blank" style="text-decoration:none; color:#1d1d1f;">
                        <h2 style="margin:0 0 14px; font-size:19px; line-height:1.3; font-weight:600;">
                            {item['title']}
                        </h2>
                    </a>
                    <p style="margin:0; line-height:1.65; font-size:15.5px; color:#333;">
                        {item['ai_summary']}
                    </p>
                    <div style="margin-top:20px;">
                        <a href="{item['link']}" target="_blank" 
                           style="display:inline-flex; align-items:center; gap:6px; color:#0071e3; font-weight:500; font-size:14px; text-decoration:none;">
                            Mehr lesen →
                        </a>
                    </div>
                </div>
        """

    html += """
            </div>

            <!-- FOOTER -->
            <div style="background:#f5f5f7; padding:25px 30px; text-align:center; font-size:13px; color:#666;">
                Automatischer ALS Research Screener • Nur relevante Phase-3 / FDA News<br>
                <span style="font-size:12px; opacity:0.7;">Dies ist kein medizinischer Rat. Immer die Originalquellen prüfen.</span>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        logging.info("✅ Email erfolgreich versendet!")
    except Exception as e:
        logging.error(f"❌ Email-Versand fehlgeschlagen: {e}")


if __name__ == "__main__":
    results = get_news()
    send_email(results)
