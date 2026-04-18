import feedparser
import datetime
import smtplib
import json
import os
import logging
import urllib.parse
import time
import hashlib
import re
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

# --- MODELL-IDs ---
PRIMARY_MODEL = "claude-haiku-4-5-20251001"
BACKUP_MODEL  = "claude-sonnet-4-6"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def calculate_score(title: str, link: str) -> int:
    text = title.lower()
    domain = urllib.parse.urlparse(link).netloc.lower()
    score = 0

    # Basis-Bonus
    if "als" in text or "motor neuron" in text:
        score += 25

    # Hohe Priorität
    if any(k in text for k in ["approval", "approved", "zulassung", "fda approval", "ema"]):
        score += 100
    if any(k in text for k in ["phase 3", "phase iii", "pivotal"]):
        score += 60
    if any(k in text for k in ["phase 2", "phase ii", "phase 2b", "phase 2c", "topline"]):
        score += 35

    # === STARKER BONUS FÜR KONKRETE THERAPIE-UPDATES ===
    if any(k in text for k in ["radicava", "edaravone", "shionogi"]):
        score += 35
    if any(k in text for k in ["aan", "conference", "presentation", "presenting", "data readout", "analyses"]):
        score += 25

    # Technologie & Breakthroughs
    if any(k in text for k in ["neuralink", "brain-computer", "bci", "brain chip", "thought control", "synchron", "brain interface"]):
        score += 45
    if any(k in text for k in ["breakthrough", "milestone", "game changer", "revolutionary", "life-changing"]):
        score += 30

    # Weitere Bereiche
    if any(k in text for k in ["alsfrs", "nfl", "neurofilament", "biomarker", "survival", "endpoint"]):
        score += 25
    if any(k in text for k in ["gene therapy", "aso", "antisense", "stem cell", "cell therapy"]):
        score += 22

    # Pipeline & Studien
    if "pipeline" in text or ("clinical trial" in text and "als" in text):
        score += 20

    # Quellen-Bonus
    premium = ["fda.gov", "nature.com", "nejm.org", "thelancet.com", "reuters.com", "statnews.com",
               "neurologylive.com", "cgtlive.com", "alzforum.org", "beingpatient.com", "pharmiweb.com"]
    if any(s in domain for s in premium):
        score += 25

    # === STÄRKERE ABZÜGE FÜR NICHT-RELEVANTE ARTIKEL ===
    if any(k in text for k in ["mouse", "murine", "preclinical", "animal model"]):
        score -= 40
    if any(k in text for k in ["ice bucket", "charity", "fundraiser", "spendenlauf", "donation run"]):
        score -= 50
    if any(s in domain for s in ["marketwatch", "yahoo.com/finance", "seekingalpha", "fool.com", "barchart.com"]):
        score -= 40
    if any(k in text for k in ["what is", "died from", "actor", "celebrity", "game of thrones"]):
        score -= 35   # starke Abwertung für Erklärungs- und Celebrity-Artikel

    return max(0, min(100, score))


def call_ai_model(title, snippet):
    prompt = f"Fasse diese ALS-Forschung kurz in 2 Sätzen zusammen (Patientenfokus):\n{title}\n{snippet}"
    for model_id in [PRIMARY_MODEL, BACKUP_MODEL]:
        try:
            logging.info(f"→ KI-Analyse: {title[:60]}...")
            response = client.messages.create(
                model=model_id,
                max_tokens=300,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
                timeout=25.0
            )
            logging.info(f"✅ KI erfolgreich")
            return response.content[0].text.strip()
        except Exception as e:
            logging.warning(f"⚠️ KI-Fehler: {e}")
            continue
    return "Zusammenfassung aktuell nicht verfügbar."


def get_news():
    db_file = Path('sent_articles.json')
    seen_hashes = set()

    if db_file.exists():
        try:
            content = db_file.read_text().strip()
            if content:
                data = json.loads(content)
                seen_hashes = set(data.get("hashes", []))
        except:
            pass

    queries = [
        'ALS (FDA OR EMA OR "regulatory approval" OR "marketing authorization" OR "Breakthrough Designation" OR "Priority Review" OR "Fast Track")',
        'ALS (NurOwn OR Pridopidine OR Tofersen OR Qalsody OR AMX0035 OR Relyvrio OR CNM-Au8 OR MN-166 OR ibudilast OR RT1999 OR smilagenin OR VHB937 OR QRL-201 OR ulefnersen OR Radicava OR Edaravone)',
        'ALS ("Phoenix Trial" OR "HEALEY ALS Platform" OR "PREVAiLS" OR "EXPERTS-ALS" OR "ASTRALS")',
        'ALS ("novel therapeutic" OR "first-in-class" OR "investigational drug" OR "new treatment" OR "emerging therapy")',
        'ALS ("Phase 1" OR "Phase I" OR "Phase 2" OR "Phase II" OR "topline results" OR "interim data" OR "data readout")',
        'ALS (TDP-43 OR Stathmin-2 OR UNC13A OR FUS OR SOD1 OR C9orf72 OR "gene therapy" OR ASO OR "antisense" OR CRISPR)',
        'ALS (biomarker OR NfL OR "Neurofilament" OR "ALSFRS-R" OR pNfH)',
        'ALS ("Brain-Computer Interface" OR BCI OR Synchron OR Neuralink OR "eye-tracking" OR "brain chip")',
        'ALS ("motor neuron disease" OR "clinical trial" OR "study results" OR "breakthrough" OR "AAN")'
    ]

    candidates = []
    now = datetime.datetime.now()

    for q in queries:
        logging.info(f"Suche: {q}")
        try:
            feed = feedparser.parse(f"https://news.google.com/rss?q={urllib.parse.quote(q)}")
        except Exception as e:
            logging.warning(f"Feed-Error: {e}")
            continue

        for entry in feed.entries:
            link = getattr(entry, 'link', '')
            title = getattr(entry, 'title', '')
            if not link or not title:
                continue

            norm_title = normalize_title(title)
            title_hash = hashlib.md5(norm_title.encode('utf-8')).hexdigest()

            if title_hash in seen_hashes or link in seen_hashes:
                continue

            published = getattr(entry, 'published_parsed', None)
            if published:
                try:
                    pub_dt = datetime.datetime.fromtimestamp(time.mktime(published))
                    if (now - pub_dt).total_seconds() > 14 * 24 * 3600:
                        continue
                except:
                    pass

            score = calculate_score(title, link)
            if score >= 22:                     # Mindest-Score jetzt 22
                candidates.append({
                    'title': title,
                    'link': link,
                    'score': score,
                    'title_hash': title_hash
                })
                logging.info(f"✅ Kandidat ({score} Pkt.): {title[:70]}...")

    candidates.sort(key=lambda x: x['score'], reverse=True)
    final_items = candidates[:8]

    results = []
    for item in final_items:
        summary = call_ai_model(item['title'], "")
        results.append({
            'title': item['title'],
            'link': item['link'],
            'ai_summary': summary,
            'score': item['score']
        })
        seen_hashes.add(item['title_hash'])
        seen_hashes.add(item['link'])

    db_file.write_text(json.dumps({"hashes": list(seen_hashes)[-2000:]}))

    return results


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
                    <div style="margin-top:18px; padding-top:12px; border-top:1px solid #ddd; font-size:13px; color:#0071e3; font-weight:600;">
                        Relevanz: <strong>{item['score']} / 100 Punkte</strong>
                    </div>
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
