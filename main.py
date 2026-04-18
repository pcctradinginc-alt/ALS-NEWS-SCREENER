import feedparser
import datetime
import smtplib
import json
import os
import logging
import urllib.parse
import time
import anthropic
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- KONFIGURATION & VALIDIERUNG ---
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY')

if not ANTHROPIC_KEY:
    raise ValueError("❌ ANTHROPIC_API_KEY fehlt in den GitHub Secrets!")

# Das stabilste Modell (feste Version gegen 404-Fehler)
STABLE_MODEL = "claude-3-haiku-20240307"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# --- KI FUNKTION (STABIL & SCHNELL) ---
def call_ai_model(title, snippet):
    prompt = f"""Fasse diese ALS-Forschung in 2 Sätzen zusammen. Fokus: Bedeutung für Patienten.
    Titel: {title}
    Inhalt: {snippet}"""

    try:
        logging.info(f"→ KI-Analyse mit {STABLE_MODEL}...")
        response = client.messages.create(
            model=STABLE_MODEL,
            max_tokens=300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
            timeout=30.0 
        )
        logging.info(f"✅ KI Erfolg")
        return response.content[0].text.strip()
    except Exception as e:
        logging.error(f"⚠️ KI Fehler bei {STABLE_MODEL}: {e}")
        return "Zusammenfassung aktuell nicht verfügbar."

# --- SCORING ---
def calculate_score(entry):
    score = 0
    text = (getattr(entry, 'title', '') + " " + getattr(entry, 'summary', '')).lower()
    
    # Kritische Begriffe (Gewichtung 2026)
    if any(x in text for x in ["fda approval", "ema approved", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 70
    if "alsfrs-r" in text: score += 30
    
    # Ausschlusskriterien (Tierstudien)
    if any(x in text for x in ["mouse", "mice", "animal model", "in vitro"]): score -= 60
    
    return int(score)

# --- DATENBESCHAFFUNG ---
def get_news():
    db_file = Path('sent_articles.json')
    seen_urls = []
    
    # Fix: Robuster JSON-Import (verhindert JSONDecodeError)
    if db_file.exists():
        try:
            content = db_file.read_text().strip()
            if content:
                seen_urls = json.loads(content).get("hashes", [])
        except Exception as e:
            logging.warning(f"⚠️ DB korrupt, starte neu: {e}")

    queries = [
        'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
        'ALS "Phase 3" OR "Pivotal" OR "Top-line results"'
    ]
    
    found_items = []
    for q in queries:
        logging.info(f"Suche: {q}")
        feed = feedparser.parse(f"https://news.google.com/rss?q={urllib.parse.quote(q)}")
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                
                # Nur Top-News (> 70) werden verarbeitet
                if score >= 70:
                    logging.info(f"High-Score ({score}): {entry.title[:50]}...")
                    summary = call_ai_model(entry.title, getattr(entry, 'summary', ''))
                    found_items.append({
                        'title': entry.title,
                        'link': link,
                        'score': score,
                        'ai_summary': summary
                    })
                # Wir markieren den Link als "gesehen", egal welcher Score
                seen_urls.append(link)
    
    # DB Speichern (begrenzt auf letzte 500 Links)
    try:
        db_file.write_text(json.dumps({"hashes": seen_urls[-500:]}))
    except Exception as e:
        logging.error(f"Fehler beim Speichern der DB: {e}")
        
    return sorted(found_items, key=lambda x: x['score'], reverse=True)[:10]

# --- EMAIL VERSAND ---
def send_email(items):
    if not items:
        logging.info("Keine neuen relevanten High-Score News.")
        return
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Update - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    html = f"""
    <html>
    <body style="font-family: sans-serif; color: #333;">
        <h2 style="color: #0071e3; border-bottom: 2px solid #0071e3;">Top ALS News (Pivotal & FDA)</h2>
    """
    for item in items:
        html += f"""
        <div style="margin-bottom: 20px; padding: 10px; border-left: 5px solid #d70015; background: #f9f9f9;">
            <b style="color: #d70015;">Score: {item['score']}</b><br>
            <a href="{item['link']}" style="font-size: 18px; font-weight: bold; text-decoration: none; color: #1d1d1f;">{item['title']}</a>
            <p style="margin-top: 10px; font-style: italic;">{item['ai_summary']}</p>
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
        logging.error(f"❌ Email-Fehler: {e}")

if __name__ == "__main__":
    results = get_news()
    send_email(results)
