import feedparser
import requests
import datetime
import smtplib
import json
import os
import logging
import urllib.parse
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Automatisches Installieren von Anthropic
try:
    import anthropic
except ImportError:
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic"])
    import anthropic

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- KONFIGURATION ---
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY')

# Globale Variable für das funktionierende Modell
WORKING_MODEL = None

# Suchanfragen
SEARCH_QUERIES = [
    'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:ema.europa.eu ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:clinicaltrials.gov ALS "Phase 3"',
    'site:nature.com ALS OR "Amyotrophic Lateral Sclerosis"',
    'ALS "Phase 3" OR "Pivotal" OR "Top-line results"',
    'ALS "FDA approval" OR "Market authorization"',
    'ALS "ALSFRS-R" "significant slowing"'
]

# --- KI ZUSAMMENFASSUNG (OPTIMIERT FÜR 2026) ---
def get_ai_summary(title, snippet):
    global WORKING_MODEL
    if not ANTHROPIC_KEY:
        return "Zusammenfassung nicht verfügbar (API-Key fehlt)."
    
    # Priorisierte Modell-Liste für 2026
    # Wir testen Claude 4 Haiku zuerst, dann Sonnet als bewährtes Backup
    models_to_test = [
        "claude-4-haiku-latest",
        "claude-4-haiku-20260307", 
        "claude-3-5-sonnet-latest",
        "claude-3-5-sonnet-20241022"
    ]
    
    # Falls wir in diesem Durchlauf schon ein Modell gefunden haben, das geht:
    test_list = [WORKING_MODEL] if WORKING_MODEL else models_to_test
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Fasse diese ALS-Forschungsnachricht in 2-3 prägnanten deutschen Sätzen zusammen. 
    Konzentriere dich auf die klinische Bedeutung für Patienten. Antworte nur mit der Zusammenfassung.
    
    Titel: {title}
    Inhalt: {snippet}"""

    for model_name in test_list:
        if not model_name: continue
        try:
            message = client.messages.create(
                model=model_name, 
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            WORKING_MODEL = model_name 
            return message.content[0].text.strip()
        except Exception as e:
            logging.warning(f"Modell {model_name} fehlgeschlagen: {e}")
            # Falls das gecashte WORKING_MODEL plötzlich fehlschlägt, Liste zurücksetzen
            if WORKING_MODEL == model_name:
                WORKING_MODEL = None
            continue
            
    return "KI-Analyse aktuell nicht möglich (Modell-Fehler)."

# --- SCORING ---
def calculate_score(entry):
    score = 0
    text = (getattr(entry, 'title', '') + " " + getattr(entry, 'summary', '')).lower()
    
    if any(x in text for x in ["fda approval", "ema approved", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 60
    if "alsfrs-r" in text: score += 30
    
    # Bonus für Quellen
    link = getattr(entry, 'link', '').lower()
    if any(dom in link for dom in ["fda.gov", "ema.europa.eu", "nature.com", "nejm.org"]):
        score *= 2.0
        
    # Abzug für Tierversuche (weniger relevant für akute Patienten-Info)
    if any(x in text for x in ["mouse", "mice", "animal model", "in vitro"]): score -= 50
    
    return int(score)

# --- DATENBESCHAFFUNG ---
def get_news():
    db_file = Path('sent_articles.json')
    seen_urls = []
    if db_file.exists():
        try:
            seen_urls = json.loads(db_file.read_text()).get("hashes", [])
        except: pass

    all_news = []
    for q in SEARCH_QUERIES:
        rss_url = f"https://news.google.com/rss?q={urllib.parse.quote(q)}"
        logging.info(f"Suche läuft: {q}")
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                if score >= 40: 
                    logging.info(f"Analysiere: {entry.title[:60]}...")
                    ai_summary = get_ai_summary(entry.title, getattr(entry, 'summary', ''))
                    
                    all_news.append({
                        'title': entry.title,
                        'link': link,
                        'score': score,
                        'date': getattr(entry, 'published', 'Heute'),
                        'ai_summary': ai_summary
                    })
                    seen_urls.append(link)
    
    # Sortieren nach Wichtigkeit
    all_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    db_file.write_text(json.dumps({"hashes": seen_urls[-500:]})) # Speicherbegrenzung
    return all_news[:12]

# --- VERSAND ---
def send_email(news_items):
    if not news_items:
        logging.info("Keine neuen relevanten News.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Update - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = f"ALS Intelligence <{GMAIL_USER}>"
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 650px; margin: auto; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
            <h2 style="color: #0071e3; border-bottom: 2px solid #0071e3; padding-bottom: 10px;">Top ALS News (Score 40+)</h2>
    """
    for item in news_items:
        html += f"""
            <div style="margin-bottom: 25px; padding-bottom: 15px; border-bottom: 1px dotted #ccc;">
                <strong style="color: #d70015;">Score: {item['score']}</strong><br>
                <h3 style="margin: 5px 0;"><a href="{item['link']}" style="text-decoration:none; color:#1d1d1f;">{item['title']}</a></h3>
                <p style="background-color: #f2f2f7; padding: 12px; border-radius: 8px; border-left: 4px solid #0071e3;">{item['ai_summary']}</p>
                <small style="color: #888;">{item['date']}</small>
            </div>
        """
    html += """
            <p style="font-size: 11px; color: #aaa; margin-top: 30px;">
                Dieser Report wurde automatisch durch Claude 4/3.5 KI-Analyse erstellt.
            </p>
        </div>
    </body>
    </html>
    """
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        logging.info("Email wurde erfolgreich versendet!")
    except Exception as e:
        logging.error(f"Fehler beim Email-Versand: {e}")

if __name__ == "__main__":
    results = get_news()
    send_email(results)
