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

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- KONFIGURATION ---
# Diese Variablen ziehen die Werte aus deinen GitHub Secrets
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')

# Suchbegriffe ohne manuelles &output=rss (wird unten in der Funktion gelöst)
SEARCH_QUERIES = [
    'ALS "clinical trial" OR "breakthrough" OR "Phase"',
    'ALS "FDA approval" OR "EMA" OR "marketing authorization"',
    'ALS "biomarker" OR "gene therapy" OR "antisense oligonucleotide"',
    'ALS "ALSFRS-R" OR "slowing progression"'
]

# --- SCORING LOGIK ---
def calculate_score(entry):
    score = 0
    title = entry.title if hasattr(entry, 'title') else ""
    summary = entry.summary if hasattr(entry, 'summary') else ""
    text = (title + " " + summary).lower()
    
    # 1. Phasen-Scoring
    if any(x in text for x in ["fda", "ema", "approved", "zulassung", "nda"]): score += 100
    elif any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 40
    elif any(x in text for x in ["phase 2", "phase ii", "efficacy"]): score += 15
    elif any(x in text for x in ["phase 1", "phase i", "safety"]): score += 5
    
    # 2. Bonus-Keywords
    if "alsfrs-r" in text: score += 20
    if any(x in text for x in ["biomarker", "nfl"]): score += 10
    if any(x in text for x in ["gene therapy", "aso", "antisense"]): score += 10
    
    # 3. Quellen-Multiplikator
    link = entry.link.lower() if hasattr(entry, 'link') else ""
    if any(dom in link for dom in ["nature.com", "reuters.com", "statnews.com", "nejm.org", "thelancet.com", "fda.gov"]):
        score *= 2.0
    elif any(dom in link for dom in ["marketwatch.com", "yahoo.com", "seekingalpha"]):
        score *= 0.5
        
    # 4. Abzüge (Hype-Filter)
    if any(x in text for x in ["mouse", "mice", "animal model", "preclinical", "prä-klinisch"]): score -= 30
    if any(x in text for x in ["stem cell", "stammzell"]): score -= 40
    if any(x in text for x in ["icebucket", "walk", "fundraiser", "donation"]): score -= 50
    
    return int(score)

# --- NEWS SAMMELN ---
def get_news():
    db_file = Path('sent_articles.json')
    
    # Datenbank laden oder erstellen
    try:
        if db_file.exists():
            data = json.loads(db_file.read_text())
            seen_urls = data.get("hashes", []) if isinstance(data, dict) else []
        else:
            seen_urls = []
    except Exception as e:
        logging.warning(f"Datenbank konnte nicht geladen werden, starte neu: {e}")
        seen_urls = []

    all_news = []
    for q in SEARCH_QUERIES:
        # URL-Encoding für Leerzeichen und Sonderzeichen
        encoded_query = urllib.parse.quote(q)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}"
        
        logging.info(f"Scraping Google News: {q}")
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                # Nur relevante News in den Report aufnehmen
                if score > 5: 
                    all_news.append({
                        'title': getattr(entry, 'title', 'Kein Titel'),
                        'link': link,
                        'score': score,
                        'date': getattr(entry, 'published', 'Heute')
                    })
                    seen_urls.append(link)
    
    # Sortieren: Beste News zuerst
    all_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    
    # Update der Datenbank (Speichert die letzten 200 Links gegen Duplikate)
    try:
        db_file.write_text(json.dumps({"hashes": seen_urls[-200:]}))
    except Exception as e:
        logging.error(f"Datenbank-Update fehlgeschlagen: {e}")
    
    return all_news

# --- EMAIL VERSAND (Apple Design) ---
def send_email(news_items):
    if not news_items:
        logging.info("Keine neuen relevanten Artikel gefunden. Sende keine E-Mail.")
        return

    if not GMAIL_USER or not GMAIL_PASS:
        logging.error("Gmail-Zugangsdaten fehlen! Bitte Secrets GMAIL_ADDRESS und GMAIL_APP_PASSWORD prüfen.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Report: {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    # Apple-Inspired HTML Layout
    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f5f5f7; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.05);">
            <header style="border-bottom: 1px solid #d2d2d7; padding-bottom: 20px; margin-bottom: 30px;">
                <p style="color: #0071e3; font-weight: 600; font-size: 12px; text-transform: uppercase; margin: 0; letter-spacing: 0.5px;">Daily Intelligence</p>
                <h1 style="font-size: 26px; font-weight: 700; color: #1d1d1f; margin: 5px 0 0 0;">ALS Research News</h1>
            </header>
    """

    for item in news_items:
        # Farbe basierend auf Score
        score_bg = "#34c759" if item['score'] >= 80 else "#0071e3" if item['score'] >= 30 else "#8e8e93"
        html += f"""
            <div style="margin-bottom: 35px; padding-bottom: 10px;">
                <span style="font-size: 10px; font-weight: 700; color: white; background: {score_bg}; padding: 3px 10px; border-radius: 10px; display: inline-block;">SCORE: {item['score']}</span>
                <h2 style="font-size: 19px; font-weight: 600; margin: 12px 0 6px 0; line-height: 1.3;">
                    <a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a>
                </h2>
                <p style="font-size: 13px; color: #86868b; margin: 0;">{item['date']}</p>
                <div style="margin-top: 8px;">
                    <a href="{item['link']}" style="font-size: 14px; color: #0071e3; text-decoration: none; font-weight: 500;">Bericht lesen →</a>
                </div>
            </div>
        """

    html += """
            <footer style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #d2d2d7; text-align: center;">
                <p style="font-size: 11px; color: #86868b; line-height: 1.5;">
                    Automatisierter ALS News-Screener. <br>
                    Basierend auf klinischen Phasen-Daten & FDA/EMA Meldungen.
                </p>
            </footer>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        logging.info(f"E-Mail mit {len(news_items)} Artikeln erfolgreich versendet!")
    except Exception as e:
        logging.error(f"Fehler beim E-Mail-Versand: {e}")

# --- START ---
if __name__ == "__main__":
    logging.info("=== ALS News Screener gestartet ===")
    news = get_news()
    send_email(news)
    logging.info("=== Vorgang abgeschlossen ===")
