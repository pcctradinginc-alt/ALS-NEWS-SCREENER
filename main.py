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
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')

# OPTIMIERTE SEARCH QUERIES
# Diese Kombination nutzt Google News als Hub für Behörden und Fachpresse
SEARCH_QUERIES = [
    'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:ema.europa.eu ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:clinicaltrials.gov ALS "Phase 3"',
    'site:nature.com ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:reuters.com ALS "clinical trial" OR "breakthrough"',
    'ALS "Phase 3" OR "Pivotal" OR "Top-line results"',
    'ALS "FDA approval" OR "Market authorization"',
    'ALS "ALSFRS-R" "significant slowing"'
]

# --- SCORING LOGIK ---
def calculate_score(entry):
    score = 0
    title = entry.title if hasattr(entry, 'title') else ""
    summary = entry.summary if hasattr(entry, 'summary') else ""
    text = (title + " " + summary).lower()
    
    # 1. Phasen-Scoring (Priorität)
    if any(x in text for x in ["fda approval", "ema approved", "marketing authorization", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal", "top-line"]): score += 50
    if any(x in text for x in ["phase 2", "phase ii", "efficacy"]): score += 20
    
    # 2. Wissenschaftliche Relevanz
    if "alsfrs-r" in text: score += 25
    if "biomarker" in text or "nfl" in text: score += 15
    if "gene therapy" in text or "aso" in text: score += 15
    
    # 3. Quellen-Bonus (Direkte Behörden/Journale)
    link = entry.link.lower() if hasattr(entry, 'link') else ""
    if any(dom in link for dom in ["fda.gov", "ema.europa.eu", "nature.com", "nejm.org", "thelancet.com"]):
        score *= 2.5
    
    # 4. Hype- & Rauschfilter (Abzüge)
    if any(x in text for x in ["mouse", "mice", "animal model", "preclinical"]): score -= 40
    if any(x in text for x in ["icebucket", "walk", "fundraiser", "donation"]): score -= 60
    if "stem cell" in text or "stammzell" in text: score -= 30
    
    return int(score)

# --- NEWS SAMMELN ---
def get_news():
    db_file = Path('sent_articles.json')
    
    # Datenbank sicher laden
    if db_file.exists():
        try:
            content = db_file.read_text()
            data = json.loads(content) if content else {}
            seen_urls = data.get("hashes", [])
        except Exception:
            seen_urls = []
    else:
        seen_urls = []

    all_news = []
    for q in SEARCH_QUERIES:
        encoded_query = urllib.parse.quote(q)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}"
        
        logging.info(f"Suche in: {q}")
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                # Nur Artikel mit echtem Wert
                if score > 10: 
                    all_news.append({
                        'title': getattr(entry, 'title', 'Unbekannter Titel'),
                        'link': link,
                        'score': score,
                        'date': getattr(entry, 'published', 'Heute')
                    })
                    seen_urls.append(link)
    
    # Sortierung nach Wichtigkeit
    all_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    
    # Datenbank für den nächsten Run speichern (maximal 300 Hashes)
    db_file.write_text(json.dumps({"hashes": seen_urls[-300:]}))
    
    return all_news

# --- EMAIL VERSAND (Apple Design) ---
def send_email(news_items):
    if not news_items:
        logging.info("Keine hochrelevanten neuen Artikel gefunden.")
        return

    if not GMAIL_USER or not GMAIL_PASS:
        logging.error("Secrets GMAIL_USER/GMAIL_PASS fehlen.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Intelligence Report - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = f"ALS Screener <{GMAIL_USER}>"
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    # Hochwertiges HTML-Design (Apple Mail inspiriert)
    html = f"""
    <html>
    <body style="font-family: -apple-system, system-ui, sans-serif; background-color: #f5f5f7; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 40px; border-radius: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.06);">
            <header style="border-bottom: 0.5px solid #d2d2d7; padding-bottom: 25px; margin-bottom: 35px;">
                <p style="color: #0071e3; font-weight: 600; font-size: 13px; text-transform: uppercase; margin: 0; letter-spacing: 0.8px;">Premium Digest</p>
                <h1 style="font-size: 28px; font-weight: 700; color: #1d1d1f; margin: 6px 0 0 0; letter-spacing: -0.5px;">ALS Research Update</h1>
            </header>
    """

    for item in news_items:
        score_bg = "#34c759" if item['score'] >= 90 else "#0071e3" if item['score'] >= 40 else "#8e8e93"
        html += f"""
            <div style="margin-bottom: 40px;">
                <span style="font-size: 10px; font-weight: 700; color: white; background: {score_bg}; padding: 4px 12px; border-radius: 12px; display: inline-block; margin-bottom: 12px;">SCORE: {item['score']}</span>
                <h2 style="font-size: 20px; font-weight: 600; margin: 0 0 8px 0; line-height: 1.35;">
                    <a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a>
                </h2>
                <p style="font-size: 14px; color: #86868b; margin: 0;">{item['date']}</p>
                <div style="margin-top: 12px;">
                    <a href="{item['link']}" style="font-size: 15px; color: #0071e3; text-decoration: none; font-weight: 500;">Vollständigen Bericht lesen →</a>
                </div>
            </div>
        """

    html += """
            <footer style="margin-top: 50px; padding-top: 25px; border-top: 0.5px solid #d2d2d7; text-align: center;">
                <p style="font-size: 12px; color: #86868b; line-height: 1.6;">
                    Dieser Bericht wurde durch einen automatisierten Algorithmus generiert, <br>
                    der medizinische Datenbanken und regulatorische Meldungen filtert.
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
        logging.info(f"Report erfolgreich versendet ({len(news_items)} Artikel).")
    except Exception as e:
        logging.error(f"Mail-Fehler: {e}")

# --- START ---
if __name__ == "__main__":
    logging.info("=== ALS Intelligence Screener Start ===")
    news_data = get_news()
    send_email(news_data)
    logging.info("=== Vorgang beendet ===")
