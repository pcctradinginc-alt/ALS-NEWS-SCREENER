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

# --- KONFIGURATION ---
GMAIL_USER = os.environ.get('GMAIL_USER')
GMAIL_PASS = os.environ.get('GMAIL_PASS')
RECIPIENT = os.environ.get('RECIPIENT')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY')

if not ANTHROPIC_KEY:
    raise ValueError("❌ ANTHROPIC_API_KEY fehlt!")

# --- MODELL-IDs (Stand April 2026) ---
PRIMARY_MODEL = "claude-haiku-4-5-20251001"
BACKUP_MODEL  = "claude-sonnet-4-6"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def calculate_score(title: str, link: str) -> int:
    text = title.lower()
    domain = urllib.parse.urlparse(link).netloc.lower()
    score = 0

    # Positive Punkte
    if any(k in text for k in ["approval", "approved", "zulassung", "fda approval", "ema"]):
        score += 100
    if any(k in text for k in ["phase 3", "phase iii", "pivotal"]):
        score += 60
    if any(k in text for k in ["phase 2", "phase ii", "phase 2b", "phase 2c", "topline"]):
        score += 35
    if any(k in text for k in ["alsfrs", "nfl", "neurofilament", "biomarker", "survival", "endpoint"]):
        score += 25
    if any(k in text for k in ["gene therapy", "aso", "antisense", "stem cell", "cell therapy"]):
        score += 20

    # Quellen-Bonus
    premium = ["fda.gov", "nature.com", "nejm.org", "thelancet.com", "reuters.com", "statnews.com",
               "neurologylive.com", "cgtlive.com", "alzforum.org", "beingpatient.com"]
    if any(s in domain for s in premium):
        score += 25

    # Abzüge
    if any(k in text for k in ["mouse", "murine", "preclinical", "animal model"]):
        score -= 40
    if any(k in text for k in ["ice bucket", "charity", "fundraiser", "spendenlauf", "donation run"]):
        score -= 50
    if any(s in domain for s in ["marketwatch", "yahoo.com/finance", "seekingalpha", "fool.com"]):
        score -= 30

    return max(0, score)


def call_ai_model(title, snippet):
    prompt = f"Fasse diese ALS-Forschung kurz in 2 Sätzen zusammen (Patientenfokus):\n{title}\n{snippet}"
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
   
    if db_file.exists():
        try:
            content = db_file.read_text().strip()
            if content:
                seen_urls = json.loads(content).get("hashes", [])
        except:
            seen_urls = []

    # === DEINE ERWEITERTE QUERY-LISTE (optimiert) ===
    queries = [
        # 1. Zulassungen & Behörden
        'ALS (FDA OR EMA OR "regulatory approval" OR "marketing authorization" OR "Breakthrough Designation" OR "Priority Review" OR "Fast Track")',
        
        # 2. Bekannte Pipeline 2026
        'ALS (NurOwn OR Pridopidine OR Tofersen OR Qalsody OR AMX0035 OR Relyvrio OR CNM-Au8 OR MN-166 OR ibudilast OR RT1999 OR smilagenin OR VHB937 OR QRL-201 OR ulefnersen)',
        'ALS ("Phoenix Trial" OR "HEALEY ALS Platform" OR "PREVAiLS" OR "EXPERTS-ALS" OR "ASTRALS")',
        
        # 3. Neue / unbekannte Wirkstoffe
        'ALS ("novel therapeutic" OR "first-in-class" OR "investigational drug" OR "new treatment" OR "emerging therapy" OR "lead candidate")',
        'ALS ("Phase 1" OR "Phase I" OR "Phase 2" OR "Phase II" OR "topline results" OR "interim data" OR "data readout")',
        
        # 4. Genetik & molekulare Targets
        'ALS (TDP-43 OR Stathmin-2 OR UNC13A OR FUS OR SOD1 OR C9orf72 OR "gene therapy" OR ASO OR "antisense" OR CRISPR)',
        
        # 5. Biomarker
        'ALS (biomarker OR NfL OR "Neurofilament" OR "ALSFRS-R" OR pNfH)',
        
        # 6. Technologie
        'ALS ("Brain-Computer Interface" OR BCI OR Synchron OR Neuralink OR "eye-tracking")',
        
        # 7. Breite Fallback-Suche
        'ALS ("motor neuron disease" OR "clinical trial" OR "study results" OR "breakthrough")'
    ]
   
    found_items = []
    now = datetime.datetime.now()

    for q in queries:
        logging.info(f"Suche: {q}")
        feed = feedparser.parse(f"https://news.google.com/rss?q={urllib.parse.quote(q)}")
       
        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            if not link or link in seen_urls:
                continue

            # Zeitfilter: letzte 14 Tage
            published = getattr(entry, 'published_parsed', None)
            if published:
                try:
                    pub_dt = datetime.datetime.fromtimestamp(time.mktime(published))
                    if (now - pub_dt).total_seconds() > 14 * 24 * 3600:
                        continue
                except:
                    pass

            score = calculate_score(entry.title, link)
            if score >= 12:
                logging.info(f"✅ News akzeptiert ({score} Pkt.): {entry.title[:80]}...")
                summary = call_ai_model(entry.title, getattr(entry, 'summary', ''))
                found_items.append({
                    'title': entry.title,
                    'link': link,
                    'ai_summary': summary,
                    'score': score
                })
            else:
                logging.info(f"   → Score zu niedrig ({score}): {entry.title[:60]}...")

            seen_urls.append(link)

    found_items.sort(key=lambda x: x['score'], reverse=True)
    found_items = found_items[:8]

    db_file.write_text(json.dumps({"hashes": seen_urls[-500:]}))
    return found_items


def send_email(items):
    msg = MIMEMultipart('alternative')
    msg['From'] = GMAIL_USER
    msg['To'] = RECIPIENT or GMAIL_USER

    today = datetime.date.today().strftime('%d.%m.%Y')

    if items:
        msg['Subject'] = f"🧬 ALS Research Update – {today}"
        has_news = True
    else:
        msg['Subject'] = f"🧬 ALS Research Update – Keine neuen News ({today})"
        has_news = False

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin:0; padding:0; background:#f5f5f7; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
        <div style="max-width: 620px; margin: 30px auto; background:#ffffff; border-radius: 20px; overflow:hidden; box-shadow: 0 15px 35px rgba(0,0,0,0.08);">
            
            <div style="background: linear-gradient(90deg, #0071e3, #00a2ff); padding: 35px 30px; text-align:center; color:white;">
                <h1 style="margin:0; font-size:26px; font-weight:600;">🧬 ALS Research Update</h1>
                <p style="margin:8px 0 0; font-size:15px; opacity:0.95;">{today}</p>
            </div>

            <div style="padding: 30px 30px 10px;">
    """

    if has_news:
        for item in items:
            html += f"""
                <div style="margin-bottom: 28px; padding: 24px; background:#f8f9fa; border-radius: 16px; border-left: 5px solid #0071e3;">
                    <a href="{item['link']}" target="_blank" style="text-decoration:none; color:#1d1d1f;">
                        <h2 style="margin:0 0 14px; font-size:19px; line-height:1.3; font-weight:600;">{item['title']}</h2>
                    </a>
                    <p style="margin:0; line-height:1.65; font-size:15.5px; color:#333;">{item['ai_summary']}</p>
                    <div style="margin-top:20px;">
                        <a href="{item['link']}" target="_blank" style="color:#0071e3; font-weight:500; font-size:14px; text-decoration:none;">Mehr lesen →</a>
                    </div>
                </div>
            """
    else:
        html += """
                <div style="text-align:center; padding: 40px 20px; color:#555;">
                    <h2 style="font-size:22px; color:#0071e3;">📭 Keine neuen relevanten Nachrichten</h2>
                    <p style="font-size:16px; line-height:1.6;">In den letzten 14 Tagen wurden keine neuen ALS-Meldungen mit ausreichender Relevanz gefunden.<br><br>Der Screener läuft weiter und meldet sich sofort, sobald etwas Neues erscheint.</p>
                </div>
        """

    html += """
            </div>
            <div style="background:#f5f5f7; padding:25px 30px; text-align:center; font-size:13px; color:#666;">
                Automatischer ALS Research Screener • Täglich um 08:00 Uhr<br>
                <span style="font-size:12px; opacity:0.7;">Dies ist kein medizinischer Rat.</span>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, msg['To'], msg.as_string())
        
        if has_news:
            logging.info(f"✅ Email mit {len(items)} Artikeln versendet!")
        else:
            logging.info("📭 Keine neuen News – Status-Email versendet")
    except Exception as e:
        logging.error(f"❌ Email-Versand fehlgeschlagen: {e}")


if __name__ == "__main__":
    results = get_news()
    send_email(results)
