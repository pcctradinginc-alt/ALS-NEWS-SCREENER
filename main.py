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

# DEINE OPTIMIERTEN MODELLE (Stand 2026)
MODEL_CANDIDATES = [
    "claude-4-haiku",
    "claude-3-7-sonnet"
]
MAX_RETRIES = 2

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# --- KI FUNKTION (MIT TIMEOUT & FEHLER-UPGRADE) ---
def call_ai_model(title, snippet):
    prompt = f"""Fasse diese ALS-Forschung in 2 Sätzen zusammen. Fokus: Bedeutung für Patienten.
    Titel: {title}
    Inhalt: {snippet}"""

    for model in MODEL_CANDIDATES:
        for attempt in range(MAX_RETRIES):
            try:
                logging.info(f"→ Call {model} (Attempt {attempt+1})")
                response = client.messages.create(
                    model=model,
                    max_tokens=350,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=30.0  # 🔥 Fix 4: Timeout hinzugefügt
                )
                logging.info(f"✅ Erfolg: {model}")
                return response.content[0].text.strip()

            except Exception as e:
                err = str(e).lower()
                # 🔥 Fix 5: Erweiterte Fehlererkennung
                if any(x in err for x in ["not_found", "404", "model", "invalid_request_error"]):
                    logging.warning(f"❌ Modell-Name ungültig oder veraltet: {model}")
                    break 
                
                if "rate_limit" in err:
                    time.sleep(10)
                    continue
                
                logging.warning(f"⚠️ API Fehler bei {model}: {e}")
                time.sleep(2)

    return "Zusammenfassung konnte nicht erstellt werden."

# --- SCORING & FILTER ---
def calculate_score(entry):
    score = 0
    text = (getattr(entry, 'title', '') + " " + getattr(entry, 'summary', '')).lower()
    if any(x in text for x in ["fda approval", "ema approved", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 70
    if "alsfrs-r" in text: score += 30
    if any(x in text for x in ["mouse", "mice", "animal model"]): score -= 60
    return int(score)

def get_news():
    db_file = Path('sent_articles.json')
    seen_urls = json.loads(db_file.read_text()).get("hashes", []) if db_file.exists() else []
    
    queries = [
        'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
        'ALS "Phase 3" OR "Pivotal" OR "Top-line results"'
    ]
    
    found_items = []
    for q in queries:
        feed = feedparser.parse(f"https://news.google.com/rss?q={urllib.parse.quote(q)}")
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                
                # 🔥 Fix 6: Nur Top-Artikel (Score > 70) an KI senden
                if score >= 70:
                    logging.info(f"High-Score Match ({score}): {entry.title[:50]}...")
                    summary = call_ai_model(entry.title, getattr(entry, 'summary', ''))
                    found_items.append({
                        'title': entry.title,
                        'link': link,
                        'score': score,
                        'ai_summary': summary
                    })
                seen_urls.append(link)
    
    db_file.write_text(json.dumps({"hashes": seen_urls[-500:]}))
    return sorted(found_items, key=lambda x: x['score'], reverse=True)[:10]

# --- EMAIL ---
def send_email(items):
    if not items:
        logging.info("Keine High-Score News gefunden.")
        return
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS High-Impact Report - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    content = f"<h2 style='color:#0071e3;'>Top ALS Research Alert (Score > 70)</h2>"
    for item in items:
        content += f"""
        <div style="margin-bottom:20px; border-left:4px solid #d70015; padding-left:15px;">
            <b>Score: {item['score']}</b><br>
            <a href="{item['link']}" style="font-size:16px;">{item['title']}</a><br>
            <p style="background:#f9f9f9; padding:10px;">{item['ai_summary']}</p>
        </div>"""
    
    msg.attach(MIMEText(content, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
    logging.info("Email erfolgreich versendet!")

if __name__ == "__main__":
    news = get_news()
    send_email(news)
