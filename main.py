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

# Versuche das Anthropic-Modul zu laden, sonst installiere es (für GitHub Actions)
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

# Optimierte Suchanfragen für maximale Relevanz
SEARCH_QUERIES = [
    'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:ema.europa.eu ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:clinicaltrials.gov ALS "Phase 3"',
    'site:nature.com ALS OR "Amyotrophic Lateral Sclerosis"',
    'ALS "Phase 3" OR "Pivotal" OR "Top-line results"',
    'ALS "FDA approval" OR "Market authorization"',
    'ALS "ALSFRS-R" "significant slowing"'
]

# --- KI ZUSAMMENFASSUNG (CLAUDE HAIKU) ---
def get_ai_summary(title, snippet):
    if not ANTHROPIC_KEY:
        return "Zusammenfassung nicht verfügbar (API-Key fehlt)."
    
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        # Der Prompt ist auf Deutsch und präzise formuliert
        prompt = f"""Fasse diese ALS-Forschungsnachricht in 2 bis maximal 3 prägnanten deutschen Sätzen zusammen. 
        Konzentriere dich auf die medizinische Bedeutung für Patienten (z.B. Verlangsamung der Progression, Zulassungsstatus). 
        Antworte nur mit der Zusammenfassung.
        
        Titel: {title}
        Inhalt: {snippet}"""
        
        message = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=200,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text.strip()
    except Exception as e:
        logging.error(f"KI-Fehler: {e}")
        return "Zusammenfassung konnte aufgrund eines technischen Fehlers nicht erstellt werden."

# --- SCORING LOGIK ---
def calculate_score(entry):
    score = 0
    title = entry.title if hasattr(entry, 'title') else ""
    summary = entry.summary if hasattr(entry, 'summary') else ""
    text = (title + " " + summary).lower()
    
    # Phasen-Scoring
    if any(x in text for x in ["fda approval", "ema approved", "zulassung", "market authorization"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal", "top-line"]): score += 50
    if any(x in text for x in ["phase 2", "phase ii", "efficacy"]): score += 20
    
    # Wissenschaftliche Relevanz
    if "alsfrs-r" in text: score += 25
    if any(x in text for x in ["biomarker", "nfl", "gene therapy", "aso"]): score += 15
    
    # Quellen-Bonus
    link = entry.link.lower() if hasattr(entry, 'link') else ""
    if any(dom in link for dom in ["fda.gov", "ema.europa.eu", "nature.com", "nejm.org", "thelancet.com"]):
        score *= 2.5
    
    # Hype-Filter
    if any(x in text for x in ["mouse", "mice", "animal model", "preclinical"]): score -= 40
    if any(x in text for x in ["icebucket", "walk", "fundraiser", "donation"]): score -= 60
    if "stem cell" in text or "stammzell" in text: score -= 30
    
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
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}"
        logging.info(f"Suche: {q}")
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                # Wir erhöhen die Hürde für die KI-Zusammenfassung auf Score 40
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
    return all_news[:15] # Die besten 15 Ergebnisse

# --- EMAIL VERSAND (Apple Mail Style) ---
def send_email(news_items):
    if not news_items:
        logging.info("Keine hochrelevanten neuen Artikel gefunden.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS Intelligence Report - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = f"ALS Screener <{GMAIL_USER}>"
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    html = f"""
    <html>
    <body style="font-family: -apple-system, system-ui, sans-serif; background-color: #f5f5f7; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 40px; border-radius: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.06);">
            <header style="border-bottom: 0.5px solid #d2d2d7; padding-bottom: 25px; margin-bottom: 35px;">
                <p style="color: #0071e3; font-weight: 600; font-size: 13px; text-transform: uppercase; margin: 0; letter-spacing: 0.8px;">AI-Powered Research Update</p>
                <h1 style="font-size: 28px; font-weight: 700; color: #1d1d1f; margin: 6px 0 0 0; letter-spacing: -0.5px;">ALS Intelligence</h1>
            </header>
    """

    for item in news_items:
        score_bg = "#34c759" if item['score'] >= 90 else "#0071e3" if item['score'] >= 50 else "#8e8e93"
        html += f"""
            <div style="margin-bottom: 40px;">
                <span style="font-size: 10px; font-weight: 700; color: white; background: {score_bg}; padding: 4px 12px; border-radius: 12px; display: inline-block; margin-bottom: 12px;">SCORE: {item['score']}</span>
                <h2 style="font-size: 20px; font-weight: 600; margin: 0 0 12px 0; line-height: 1.35;">
                    <a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a>
                </h2>
                <div style="background-color: #f9f9fb; border-left: 4px solid #0071e3; padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                    <p style="font-size: 15px; color: #1d1d1f; line-height: 1.5; margin: 0; font-style: italic;">
                        {item['ai_summary']}
                    </p>
                </div>
                <p style="font-size: 12px; color: #86868b; margin: 0;">{item['date']} • <a href="{item['link']}" style="color: #0071e3; text-decoration: none; font-weight: 500;">Originalquelle öffnen →</a></p>
            </div>
        """

    html += """
            <footer style="margin-top: 50px; padding-top: 25px; border-top: 0.5px solid #d2d2d7; text-align: center;">
                <p style="font-size: 12px; color: #86868b; line-height: 1.6;">
                    Diese Zusammenfassung wurde von Claude 3 Haiku erstellt. <br>
                    Automatisierter Filter für ALS-Forschung.
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
        logging.info("Erfolgreich versendet.")
    except Exception as e:
        logging.error(f"Fehler: {e}")

if __name__ == "__main__":
    logging.info("=== Starte Screener ===")
    news = get_news()
    send_email(news)
    logging.info("=== Fertig ===")
