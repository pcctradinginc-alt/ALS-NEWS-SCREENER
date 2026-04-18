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
# NEU: Anthropic Library
try:
    import anthropic
except ImportError:
    os.system('pip install anthropic')
    import anthropic

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# --- KONFIGURATION ---
GMAIL_USER = os.environ.get('GMAIL_USER') 
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY')

SEARCH_QUERIES = [
    'site:fda.gov ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:ema.europa.eu ALS OR "Amyotrophic Lateral Sclerosis"',
    'site:clinicaltrials.gov ALS "Phase 3"',
    'ALS "Phase 3" OR "Pivotal" OR "Top-line results"',
    'ALS "FDA approval" OR "Market authorization"',
    'ALS "ALSFRS-R" "significant slowing"'
]

# --- NEU: CLAUDE HAIKU ZUSAMMENFASSUNG ---
def get_ai_summary(title, snippet):
    if not ANTHROPIC_KEY:
        return "Zusammenfassung nicht verfügbar (Key fehlt)."
    
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = f"Fasse die folgende Nachricht über ALS-Forschung in genau 2-3 prägnanten deutschen Sätzen zusammen. Fokus auf medizinische Relevanz:\n\nTitel: {title}\nInhalt: {snippet}"
        
        message = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        logging.error(f"KI Fehler: {e}")
        return "Zusammenfassung konnte nicht erstellt werden."

# --- SCORING LOGIK (Unverändert) ---
def calculate_score(entry):
    score = 0
    title = entry.title if hasattr(entry, 'title') else ""
    summary = entry.summary if hasattr(entry, 'summary') else ""
    text = (title + " " + summary).lower()
    if any(x in text for x in ["fda approval", "ema approved", "zulassung"]): score += 100
    if any(x in text for x in ["phase 3", "phase iii", "pivotal"]): score += 50
    if "alsfrs-r" in text: score += 25
    link = entry.link.lower() if hasattr(entry, 'link') else ""
    if any(dom in link for dom in ["fda.gov", "ema.europa.eu", "nature.com"]): score *= 2.5
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
    # Um Kosten zu sparen, limitieren wir auf die Top 10 Treffer pro Tag
    for q in SEARCH_QUERIES:
        encoded_query = urllib.parse.quote(q)
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={encoded_query}")
        
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if link and link not in seen_urls:
                score = calculate_score(entry)
                if score > 30: # Nur wirklich wichtige News zusammenfassen
                    summary_text = getattr(entry, 'summary', '')
                    # KI Zusammenfassung generieren
                    logging.info(f"Generiere KI-Zusammenfassung für: {entry.title[:50]}...")
                    ai_summary = get_ai_summary(entry.title, summary_text)
                    
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
    return all_news[:15] # Top 15 Artikel senden

# --- EMAIL VERSAND (Angepasst für AI Summary) ---
def send_email(news_items):
    if not news_items: return
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🧬 ALS AI Intelligence - {datetime.date.today().strftime('%d.%m.%Y')}"
    msg['From'] = f"ALS Screener <{GMAIL_USER}>"
    msg['To'] = RECIPIENT if RECIPIENT else GMAIL_USER

    html = f"""
    <html>
    <body style="font-family: -apple-system, system-ui, sans-serif; background-color: #f5f5f7; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; padding: 40px; border-radius: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.06);">
            <header style="border-bottom: 0.5px solid #d2d2d7; padding-bottom: 25px; margin-bottom: 35px;">
                <p style="color: #0071e3; font-weight: 600; font-size: 13px; text-transform: uppercase; margin: 0;">AI-Summarized Digest</p>
                <h1 style="font-size: 26px; font-weight: 700; color: #1d1d1f; margin: 6px 0 0 0;">ALS Research Update</h1>
            </header>
    """

    for item in news_items:
        score_bg = "#34c759" if item['score'] >= 90 else "#0071e3"
        html += f"""
            <div style="margin-bottom: 40px;">
                <span style="font-size: 10px; font-weight: 700; color: white; background: {score_bg}; padding: 4px 12px; border-radius: 12px; display: inline-block; margin-bottom: 12px;">SCORE: {item['score']}</span>
                <h2 style="font-size: 19px; font-weight: 600; margin: 0 0 10px 0; line-height: 1.3;">
                    <a href="{item['link']}" style="color: #1d1d1f; text-decoration: none;">{item['title']}</a>
                </h2>
                <p style="font-size: 15px; color: #424245; line-height: 1.5; margin-bottom: 10px; padding-left: 10px; border-left: 3px solid #0071e3; font-style: italic;">
                    {item['ai_summary']}
                </p>
                <p style="font-size: 12px; color: #86868b; margin: 0;">{item['date']}</p>
            </div>
        """

    html += """</div></body></html>"""
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        logging.info("Report mit KI-Zusammenfassung versendet.")
    except Exception as e:
        logging.error(f"Mail-Fehler: {e}")

if __name__ == "__main__":
    news_data = get_news()
    send_email(news_data)
