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
# Haiku 4.5 = schnellstes & günstigstes Modell
# Sonnet 4.6 = starker Backup
PRIMARY_MODEL = "claude-haiku-4-5-20251001"
BACKUP_MODEL  = "claude-sonnet-4-6"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def call_ai_model(title, snippet):
    prompt = f"Fasse diese ALS-Forschung kurz in 2 Sätzen zusammen (Patientenfokus):\n{title}\n{snippet}"
   
    # Kette: Erst Haiku (günstig), bei Fehler Sonnet (Backup)
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
   
    # Robuster Datenbank-Import
    if db_file.exists():
        try:
            content = db_file.read_text().strip()
            if content:
                seen_urls = json.loads(content).get("hashes", [])
        except:
            seen_urls = []
   
    # Suche nach Durchbrüchen (Phase 3 und FDA)
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
                # Score-Logik: Nur wichtige News
                if any(x in text for x in ["phase 3", "phase iii", "pivotal", "fda", "approval"]):
                    logging.info(f"High-Score News gefunden: {entry.title[:60]}...")
                    summary = call_ai_model(entry.title, getattr(entry, 'summary', ''))
                    found_items.append({
                        'title': entry.title,
                        'link': link,
                        'ai_summary': summary
                    })
                seen_urls.append(link)
   
    # Datenbank aktualisieren (letzte 500 Links)
    db_file.write_text(json.dumps({"hashes": seen_urls[-500:]}))
    return found_items[:10]


def send_email(items):
    if not items:
        logging.info("Keine neuen relevanten News gefunden.")
        return
       
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Update - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT or GMAIL_USER
   
    html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #0071e3; border-bottom: 2px solid #0071e3; padding-bottom: 10px;">
            Wichtige ALS-Forschungsergebnisse (Phase 3 / FDA)
        </h2>
    """
    for item in items:
        html += f"""
        <div style="margin-bottom: 25px; padding: 15px; background-color: #f5f5f7; border-radius: 10px;">
            <a href="{item['link']}" style="font-size: 18px; font-weight: bold; color: #1d1d1f; text-decoration: none;">
                {item['title']}
            </a>
            <p style="margin-top: 10px; line-height: 1.5; color: #424245;">{item['ai_summary']}</p>
        </div>
        """
    html += "</body></html>"
   
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
