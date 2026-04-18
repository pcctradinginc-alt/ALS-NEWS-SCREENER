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

# Automatisches Installieren von Anthropic in der GitHub Action Umgebung
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

# Globale Variable für das funktionierende Modell (Cache)
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

# --- KI ZUSAMMENFASSUNG (MIT ROBUSTER MODELL-SUCHE) ---
def get_ai_summary(title, snippet):
    global WORKING_MODEL
    if not ANTHROPIC_KEY:
        return "Zusammenfassung nicht verfügbar (API-Key fehlt)."
    
    # Mögliche Haiku-Modellnamen für 2026 (günstigste Kategorie)
    haiku_models = [
        "claude-3-5-haiku-latest",
        "claude-3-5-haiku-20241022",
        "claude-3-haiku-20240307"
    ]
    
    # Wenn wir bereits wissen, welches Modell geht, nutzen wir es direkt
    models_to_test = [WORKING_MODEL] if WORKING_MODEL else haiku_models
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Fasse diese ALS-Forschungsnachricht in 2-3 prägnanten deutschen Sätzen zusammen. 
    Konzentriere dich auf die Bedeutung für Patienten. Antworte nur mit der Zusammenfassung.
    
    Titel: {title}
    Inhalt: {snippet}"""

    for model_name in models_to_test:
        if not model_name: continue
        try:
            message = client.messages.create(
                model=model_name, 
                max_tokens=300,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            WORKING_MODEL = model_name # Modell für nächsten Aufruf speichern
            return message.content[0].text.strip()
        except Exception as e:
            logging.warning(f"Modell {model_name} fehlgeschlagen: {e}")
            continue
            
    return "Zusammenfassung konnte aufgrund eines technischen Fehlers nicht erstellt werden."

# --- SCORING LOGIK ---
def calculate_score(entry):
    score = 0
    title = entry.title if hasattr(entry, 'title') else ""
    summary = entry.summary if hasattr(entry, 'summary') else ""
    text = (title + " " + summary).lower()
    
    if any(x in text for x in ["fda approval", "ema approved", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 50
    if "alsfrs-r" in text: score += 25
    
    link = entry.link.lower() if hasattr(entry, 'link') else ""
    if any(dom in link for dom in ["fda.gov", "ema.europa.eu", "nature.com"]):
        score *= 2.5
    
    if any(x in text for x in ["mouse", "mice", "animal model"]): score -= 40
    
    return int(score)

# --- NEWS SAMMELN ---
def get_news():
    db_file = Path('sent_articles.json')
    if db_file.exists():
        try:
            data = json.loads(db_file.read_text())
            seen_urls = data.get("hashes", [])
        except: seen_urls = []
    else: seen_urls = []

    all_news = []
    for q in SEARCH_QUERIES:
        encoded_query = urllib.parse.quote(q)
        rss_url = f"https://news.google.com/rss?q={encoded_query}"
        logging.info(f"Suche: {q}")
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                if score >= 40: 
                    snippet = getattr(entry, 'summary', '')
                    logging.info(f"KI-Analyse für: {entry.title[:50]}...")
                    ai_summary = get_ai_summary(entry.title, snippet)
                    
                    all_news.append({
                        'title': getattr(entry, 'title', 'Unbekannter Titel'),
                        'link': link,
                        'score': score,
                        'date': getattr(entry, 'published', 'Heute'),
                        'ai_summary': ai_summary
                    })
                    seen_urls.append(link)
    
    all_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    db_file.write_text(json.dumps({"hashes": seen_urls[-300:]}))
    return all_news[:15]

# --- EMAIL VERSAND ---
def send_email(news_items):
    if not news_items:
        logging.info("Keine relevanten News gefunden.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Intelligence Report - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = f"ALS Screener <{GMAIL_USER}>"
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    html = f"""
    <html>
    <body style="font-family: sans-serif; background-color: #f5f5f7; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 30px; border-radius: 20px;">
            <h1 style="font-size: 24px; color: #1d1d1f; border-bottom: 1px solid #eee; padding-bottom: 10px;">ALS Research News</h1>
    """
    for item in news_items:
        html += f"""
            <div style="margin-bottom: 30px;">
                <span style="background: #0071e3; color: white; padding: 3px 10px; border-radius: 10px; font-size: 11px;">SCORE: {item['score']}</span>
                <h2 style="font-size: 18px; margin: 10px 0;"><a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a></h2>
                <p style="background: #f9f9fb; padding: 15px; border-radius: 10px; font-style: italic; color: #333;">{item['ai_summary']}</p>
                <p style="font-size: 12px; color: #888;">{item['date']} • <a href="{item['link']}">Originalquelle</a></p>
            </div>
        """
    html += "</div></body></html>"
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        logging.info("Erfolgreich versendet.")
    except Exception as e:
        logging.error(f"Email Fehler: {e}")

if __name__ == "__main__":
    results = get_news()
    send_email(results)
