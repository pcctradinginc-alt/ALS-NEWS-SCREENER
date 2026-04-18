import feedparser
import requests
import datetime
import smtplib
import json
import os
import logging
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- CONFIGURATION ---
# Greift auf die GitHub Secrets zu, die du gesetzt hast
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')

SEARCH_QUERIES = [
    'ALS "clinical trial" OR "breakthrough" OR "Phase" &output=rss',
    'ALS "FDA approval" OR "EMA" OR "marketing authorization" &output=rss',
    'ALS "biomarker" OR "gene therapy" OR "antisense oligonucleotide" &output=rss',
    'ALS "ALSFRS-R" OR "slowing progression" &output=rss'
]

# --- SCORING LOGIC ---
def calculate_score(entry):
    score = 0
    # Titel und Zusammenfassung prüfen
    text = (entry.title + " " + getattr(entry, 'summary', '')).lower()
    
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
    link = entry.link.lower()
    if any(dom in link for dom in ["nature.com", "reuters.com", "statnews.com", "nejm.org", "thelancet.com", "fda.gov"]):
        score *= 2.0
    elif any(dom in link for dom in ["marketwatch.com", "yahoo.com", "seekingalpha"]):
        score *= 0.5
        
    # 4. Abzüge (Hype-Filter)
    if any(x in text for x in ["mouse", "mice", "animal model", "preclinical", "prä-klinisch"]): score -= 30
    if any(x in text for x in ["stem cell", "stammzell"]): score -= 40
    if any(x in text for x in ["icebucket", "walk", "fundraiser", "donation"]): score -= 50
    
    return int(score)

# --- CORE FUNCTIONS ---
def get_news():
    db_file = Path('sent_articles.json')
    # Wir laden die bereits gesendeten Hashes/Links
    try:
        if db_file.exists():
            data = json.loads(db_file.read_text())
            # Falls die Datei leer ist oder das alte Format hat:
            seen_urls = data.get("hashes", []) if isinstance(data, dict) else []
        else:
            seen_urls = []
    except:
        seen_urls = []

    all_news = []
    for q in SEARCH_QUERIES:
        logging.info(f"Scraping Google News für: {q}")
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}")
        
        for entry in feed.entries:
            if entry.link not in seen_urls:
                score = calculate_score(entry)
                # Nur relevante News (Score > 0)
                if score > 5: 
                    all_news.append({
                        'title': entry.title,
                        'link': entry.link,
                        'score': score,
                        'date': getattr(entry, 'published', 'Kürzlich')
                    })
                    seen_urls.append(entry.link)
    
    # Sortieren: Beste News zuerst
    all_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    
    # Update der Datenbank (nur die letzten 200 Links aufheben)
    db_file.write_text(json.dumps({"hashes": seen_urls[-200:]}))
    
    return all_news

def send_email(news_items):
    if not news_items:
        logging.info("Keine neuen relevanten Artikel gefunden.")
        return

    if not GMAIL_USER or not GMAIL_PASS:
        logging.error("Gmail-Zugangsdaten fehlen in den Secrets!")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Research Report: {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    # Apple-Style HTML Design
    html = f"""
    <html>
    <body style="font-family: -apple-system, system-ui, sans-serif; background-color: #f5f5f7; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.05);">
            <header style="border-bottom: 1px solid #d2d2d7; padding-bottom: 20px; margin-bottom: 30px;">
                <p style="color: #0071e3; font-weight: 600; font-size: 12px; text-transform: uppercase; margin: 0;">Daily Intelligence</p>
                <h1 style="font-size: 26px; font-weight: 700; color: #1d1d1f; margin: 5px 0 0 0;">ALS News Update</h1>
            </header>
    """

    for item in news_items:
        score_color = "#0071e3" if item['score'] >= 40 else "#1d1d1f"
        html += f"""
            <div style="margin-bottom: 35px;">
                <span style="font-size: 11px; font-weight: 700; color: white; background: {score_color}; padding: 3px 8px; border-radius: 12px;">SCORE: {item['score']}</span>
                <h2 style="font-size: 19px; margin: 10px 0 5px 0;">
                    <a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a>
                </h2>
                <p style="font-size: 13px; color: #86868b; margin: 0;">{item['date']}</p>
                <a href="{item['link']}" style="font-size: 14px; color: #0071e3; text-decoration: none; display: inline-block; margin-top: 5px;">Mehr lesen →</a>
            </div>
        """

    html += """
            <footer style="margin-top: 50px; padding-top: 20px; border-top: 1px solid #d2d2d7; text-align: center;">
                <p style="font-size: 12px; color: #86868b;">Dieser Report wurde automatisch erstellt und gefiltert.</p>
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
        logging.info("E-Mail wurde erfolgreich versendet!")
    except Exception as e:
        logging.error(f"Fehler beim E-Mail-Versand: {e}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    logging.info("Starte ALS News Screener...")
    articles = get_news()
    send_email(articles)
    logging.info("Fertig.")
