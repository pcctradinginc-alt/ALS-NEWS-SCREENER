#!/usr/bin/env python3
"""
ALS News Screener
=================
Collects ALS-related news from Google News RSS, FDA, ClinicalTrials.gov,
and PubMed. Scores articles by clinical relevance and emails a daily
digest in Apple-style HTML to a Gmail address.

Runs daily via GitHub Actions at 08:00 CET/CEST.
"""

import os
import re
import json
import hashlib
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", GMAIL_ADDRESS)

SENT_ARTICLES_FILE = Path(__file__).parent / "sent_articles.json"
MAX_SENT_HISTORY = 2000  # keep last N article hashes

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("als-screener")

# ---------------------------------------------------------------------------
# 1. NEWS SOURCES
# ---------------------------------------------------------------------------

GOOGLE_NEWS_QUERIES = [
    # Primary broad search with exclusions
    'ALS OR "Amyotrophic Lateral Sclerosis" OR "Amyotrophe Lateralsklerose" -icebucket -challenge -walk -fundraiser -marathon',
    # Clinical trials
    'ALS "clinical trial" OR "Phase III" OR "Phase II" OR "pivotal trial"',
    # Biomarkers & gene therapy
    'ALS "biomarker" OR "gene therapy" OR "antisense oligonucleotide" OR "neurofilament"',
    # Regulatory approvals
    'ALS "FDA approval" OR "EMA" OR "NDA submission" OR "marketing authorization" OR "Zulassung"',
    # ALSFRS-R specific (high-value clinical endpoint)
    '"ALSFRS-R" OR "ALS Functional Rating Scale"',
]

# High-quality sources get a multiplier
PREMIUM_SOURCES = {
    "reuters.com": 2.0,
    "statnews.com": 2.0,
    "nature.com": 2.5,
    "nejm.org": 2.5,
    "thelancet.com": 2.5,
    "sciencedirect.com": 2.0,
    "pubmed.ncbi.nlm.nih.gov": 2.0,
    "nih.gov": 2.0,
    "fda.gov": 2.5,
    "ema.europa.eu": 2.5,
    "clinicaltrials.gov": 2.0,
    "alsnewstoday.com": 1.8,
    "sciencemag.org": 2.0,
    "science.org": 2.0,
    "cell.com": 2.0,
    "biorxiv.org": 1.5,
    "medrxiv.org": 1.5,
}

# Low-quality / financial sources get penalized
LOW_QUALITY_SOURCES = {
    "marketwatch.com": 0.5,
    "yahoo.com": 0.6,
    "finance.yahoo.com": 0.5,
    "seekingalpha.com": 0.4,
    "benzinga.com": 0.5,
    "investorplace.com": 0.4,
    "fool.com": 0.5,
}


def fetch_google_news(query: str, max_age_hours: int = 48) -> list[dict]:
    """Fetch articles from Google News RSS for a given query."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            # Skip old articles
            if pub_date:
                age = datetime.now(timezone.utc) - pub_date
                if age > timedelta(hours=max_age_hours):
                    continue

            source = ""
            if hasattr(entry, "source") and hasattr(entry.source, "title"):
                source = entry.source.title
            elif hasattr(entry, "source"):
                source = getattr(entry.source, "href", "")

            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": pub_date.isoformat() if pub_date else "",
                "published_dt": pub_date,
                "source": source,
                "summary": entry.get("summary", ""),
                "origin": "Google News",
            })
    except Exception as e:
        log.warning(f"Google News fetch failed for query: {e}")
    return articles


def fetch_google_news_de(query: str, max_age_hours: int = 48) -> list[dict]:
    """Fetch articles from Google News RSS – German edition."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=de&gl=DE&ceid=DE:de"
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            if pub_date:
                age = datetime.now(timezone.utc) - pub_date
                if age > timedelta(hours=max_age_hours):
                    continue

            source = ""
            if hasattr(entry, "source") and hasattr(entry.source, "title"):
                source = entry.source.title

            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": pub_date.isoformat() if pub_date else "",
                "published_dt": pub_date,
                "source": source,
                "summary": entry.get("summary", ""),
                "origin": "Google News DE",
            })
    except Exception as e:
        log.warning(f"Google News DE fetch failed: {e}")
    return articles


def fetch_fda_press(max_age_hours: int = 72) -> list[dict]:
    """Fetch recent FDA press releases mentioning ALS via RSS."""
    url = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"
    articles = []
    try:
        feed = feedparser.parse(url)
        als_terms = re.compile(
            r"\bALS\b|amyotrophic lateral sclerosis|motor neuron disease",
            re.IGNORECASE,
        )
        for entry in feed.entries:
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"
            if not als_terms.search(text):
                continue
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": pub_date.isoformat() if pub_date else "",
                "published_dt": pub_date,
                "source": "FDA",
                "summary": entry.get("summary", ""),
                "origin": "FDA Press Releases",
            })
    except Exception as e:
        log.warning(f"FDA fetch failed: {e}")
    return articles


def fetch_pubmed_als(max_results: int = 20) -> list[dict]:
    """Fetch recent ALS publications from PubMed via E-utilities."""
    articles = []
    try:
        search_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax={max_results}"
            "&sort=date&term=amyotrophic+lateral+sclerosis"
            "&datetype=edat&reldate=3"  # last 3 days
        )
        resp = requests.get(search_url, timeout=15)
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return articles

        summary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&retmode=json&id={','.join(ids)}"
        )
        resp2 = requests.get(summary_url, timeout=15)
        resp2.raise_for_status()
        result = resp2.json().get("result", {})

        for pmid in ids:
            info = result.get(pmid, {})
            if not info:
                continue
            pub_date_str = info.get("epubdate", "") or info.get("pubdate", "")
            articles.append({
                "title": info.get("title", ""),
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "published": pub_date_str,
                "published_dt": None,
                "source": info.get("fulljournalname", "PubMed"),
                "summary": info.get("title", ""),
                "origin": "PubMed",
            })
    except Exception as e:
        log.warning(f"PubMed fetch failed: {e}")
    return articles


def fetch_clinicaltrials(max_results: int = 15) -> list[dict]:
    """Fetch recently updated ALS clinical trials from ClinicalTrials.gov v2 API."""
    articles = []
    try:
        url = (
            "https://clinicaltrials.gov/api/v2/studies"
            "?query.cond=amyotrophic+lateral+sclerosis"
            "&sort=LastUpdatePostDate:desc"
            f"&pageSize={max_results}"
            "&fields=NCTId,BriefTitle,OverallStatus,LastUpdatePostDate,Phase,LeadSponsorName"
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})

            nct_id = ident.get("nctId", "")
            title = ident.get("briefTitle", "")
            status = status_mod.get("overallStatus", "")
            last_update = status_mod.get("lastUpdatePostDateStruct", {}).get("date", "")
            phases = design.get("phases", []) if design else []
            phase_str = ", ".join(phases) if phases else "N/A"
            sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "") if sponsor_mod else ""

            articles.append({
                "title": f"[{phase_str}] {title} — Status: {status}",
                "link": f"https://clinicaltrials.gov/study/{nct_id}",
                "published": last_update,
                "published_dt": None,
                "source": sponsor or "ClinicalTrials.gov",
                "summary": f"Phase: {phase_str} | Status: {status} | Sponsor: {sponsor}",
                "origin": "ClinicalTrials.gov",
            })
    except Exception as e:
        log.warning(f"ClinicalTrials.gov fetch failed: {e}")
    return articles


# ---------------------------------------------------------------------------
# 2. SCORING ENGINE
# ---------------------------------------------------------------------------

# Phase scoring
PHASE_RULES = [
    # (regex, points, label)
    (re.compile(r"\bapproved\b|\bapproval\b|\bNDA submission\b|\bZulassung\b|\bmarketing authorization\b", re.I), 100, "🏆 Zulassung/Approval"),
    (re.compile(r"\bPhase\s*(?:III|3)\b|\bpivotal trial\b|\bconfirmatory\b", re.I), 40, "🔬 Phase III"),
    (re.compile(r"\bPhase\s*(?:II|2)\b|\bproof of concept\b|\befficacy\b", re.I), 15, "🧪 Phase II"),
    (re.compile(r"\bPhase\s*(?:I|1)\b|\bfirst.in.human\b|\bsafety profile\b", re.I), 5, "📋 Phase I"),
]

# Bonus keywords
BONUS_RULES = [
    (re.compile(r"\bALSFRS-R\b|\bALS Functional Rating Scale\b", re.I), 20, "ALSFRS-R Endpoint"),
    (re.compile(r"\bneurofilament\b|\bNfL\b", re.I), 10, "Biomarker (NfL)"),
    (re.compile(r"\bgene therapy\b|\bgentherapie\b", re.I), 10, "Gene Therapy"),
    (re.compile(r"\bantisense oligonucleotide\b|\bASO\b", re.I), 10, "ASO"),
    (re.compile(r"\bFDA\b", re.I), 15, "FDA mentioned"),
    (re.compile(r"\bEMA\b", re.I), 15, "EMA mentioned"),
    (re.compile(r"\bbreakthrough\b|\bdurchbruch\b", re.I), 10, "Breakthrough"),
    (re.compile(r"\bsignifican(?:t|ce)\b.*(?:slow|delay|reduction)", re.I), 15, "Significant outcome"),
]

# Penalty keywords (hype / preclinical / dubious)
PENALTY_RULES = [
    (re.compile(r"\bmouse model\b|\bmice\b|\bmurine\b|\bin vivo\b|\banimal model\b|\bMausmodell\b|\bpräklinisch\b|\bpreclinical\b", re.I), -30, "⚠️ Preclinical/Animal"),
    (re.compile(r"\bstem cell\b|\bStammzelltherapie\b", re.I), -10, "⚠️ Stem Cell (unverified?)"),
    (re.compile(r"\bmiracle\b|\bWunderheilung\b|\bcure found\b|\bnow available\b|\bjetzt verfügbar\b", re.I), -40, "🚫 Dubious Claim"),
    (re.compile(r"\bfundrais\b|\bcharity\b|\bwalk for\b|\bice bucket\b|\bSpendenlauf\b", re.I), -50, "Fundraiser/Noise"),
]


def compute_score(article: dict) -> tuple[int, list[str]]:
    """Return (score, [matched_labels]) for an article."""
    text = f"{article['title']} {article.get('summary', '')}"
    score = 0
    labels: list[str] = []

    # Phase scoring (take the highest match)
    phase_scores = []
    for regex, pts, label in PHASE_RULES:
        if regex.search(text):
            phase_scores.append((pts, label))
    if phase_scores:
        best = max(phase_scores, key=lambda x: x[0])
        score += best[0]
        labels.append(best[1])

    # Bonus
    for regex, pts, label in BONUS_RULES:
        if regex.search(text):
            score += pts
            labels.append(label)

    # Penalties
    for regex, pts, label in PENALTY_RULES:
        if regex.search(text):
            score += pts  # pts is negative
            labels.append(label)

    # Recency bonus
    if article.get("published_dt"):
        age_hours = (datetime.now(timezone.utc) - article["published_dt"]).total_seconds() / 3600
        if age_hours < 12:
            score += 10
            labels.append("🕐 <12h alt")

    # Source quality multiplier
    link = article.get("link", "").lower()
    source_name = article.get("source", "").lower()
    multiplier = 1.0

    for domain, mult in PREMIUM_SOURCES.items():
        if domain in link or domain in source_name:
            multiplier = max(multiplier, mult)
            break

    for domain, mult in LOW_QUALITY_SOURCES.items():
        if domain in link or domain in source_name:
            multiplier = min(multiplier, mult)
            break

    score = int(score * multiplier)

    # Floor at 0
    score = max(score, 0)

    return score, labels


# ---------------------------------------------------------------------------
# 3. DEDUPLICATION
# ---------------------------------------------------------------------------

def load_sent_hashes() -> set[str]:
    if SENT_ARTICLES_FILE.exists():
        try:
            data = json.loads(SENT_ARTICLES_FILE.read_text())
            return set(data.get("hashes", []))
        except Exception:
            return set()
    return set()


def save_sent_hashes(hashes: set[str]):
    # Keep only most recent
    h_list = list(hashes)[-MAX_SENT_HISTORY:]
    SENT_ARTICLES_FILE.write_text(json.dumps({"hashes": h_list}, indent=2))


def article_hash(article: dict) -> str:
    raw = f"{article.get('title', '')}{article.get('link', '')}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 4. EMAIL TEMPLATE (Apple-inspired design)
# ---------------------------------------------------------------------------

def tier_label(score: int) -> tuple[str, str]:
    """Return (tier_name, color) based on score."""
    if score >= 80:
        return "🏆 TOP TIER", "#34C759"       # Green
    elif score >= 30:
        return "🔬 HIGH TIER", "#007AFF"       # Blue
    elif score >= 10:
        return "🧪 MID TIER", "#FF9500"        # Orange
    else:
        return "📋 LOW TIER", "#8E8E93"        # Gray


def build_email_html(articles: list[dict], run_date: str) -> str:
    """Build Apple-style HTML email body."""

    rows_html = ""
    for i, art in enumerate(articles):
        score = art["_score"]
        labels = art["_labels"]
        tier_name, tier_color = tier_label(score)

        label_badges = " ".join(
            f'<span style="display:inline-block;background:#F2F2F7;color:#1C1C1E;'
            f'font-size:11px;padding:2px 8px;border-radius:12px;margin:2px 2px;">'
            f'{lbl}</span>'
            for lbl in labels
        )

        source_display = art.get("source", art.get("origin", ""))
        published_display = art.get("published", "")[:16]

        rows_html += f"""
        <tr>
          <td style="padding:20px 24px;border-bottom:1px solid #E5E5EA;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <div style="display:flex;align-items:center;margin-bottom:6px;">
                    <span style="display:inline-block;background:{tier_color};color:white;
                      font-size:11px;font-weight:700;padding:3px 10px;border-radius:14px;
                      letter-spacing:0.5px;">{tier_name}</span>
                    <span style="display:inline-block;background:#1C1C1E;color:white;
                      font-size:13px;font-weight:700;padding:3px 10px;border-radius:14px;
                      margin-left:6px;">Score: {score}</span>
                  </div>
                  <a href="{art['link']}" style="color:#1C1C1E;text-decoration:none;
                    font-size:16px;font-weight:600;line-height:1.35;display:block;
                    margin:8px 0 6px 0;">{art['title']}</a>
                  <div style="font-size:13px;color:#8E8E93;margin-bottom:8px;">
                    {source_display} &middot; {published_display} &middot; {art.get('origin', '')}
                  </div>
                  <div style="margin-top:4px;">{label_badges}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    total = len(articles)
    top_count = sum(1 for a in articles if a["_score"] >= 80)
    high_count = sum(1 for a in articles if 30 <= a["_score"] < 80)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#F2F2F7;font-family:-apple-system,BlinkMacSystemFont,
  'SF Pro Display','SF Pro Text','Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F2F2F7;">
    <tr><td align="center" style="padding:32px 16px;">

      <!-- Header Card -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
        style="background:linear-gradient(135deg,#1C1C1E 0%,#2C2C2E 100%);
        border-radius:20px;overflow:hidden;margin-bottom:16px;">
        <tr><td style="padding:36px 32px 28px 32px;text-align:center;">
          <div style="font-size:40px;margin-bottom:8px;">🧬</div>
          <h1 style="margin:0;color:white;font-size:26px;font-weight:700;
            letter-spacing:-0.5px;">ALS Research Screener</h1>
          <p style="margin:8px 0 0 0;color:#AEAEB2;font-size:14px;">
            Täglicher Digest &middot; {run_date}</p>
        </td></tr>
      </table>

      <!-- Stats Bar -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
        style="background:white;border-radius:16px;overflow:hidden;margin-bottom:16px;
        box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <tr>
          <td style="padding:16px 24px;text-align:center;width:33%;border-right:1px solid #E5E5EA;">
            <div style="font-size:28px;font-weight:700;color:#1C1C1E;">{total}</div>
            <div style="font-size:12px;color:#8E8E93;margin-top:2px;">Artikel gesamt</div>
          </td>
          <td style="padding:16px 24px;text-align:center;width:33%;border-right:1px solid #E5E5EA;">
            <div style="font-size:28px;font-weight:700;color:#34C759;">{top_count}</div>
            <div style="font-size:12px;color:#8E8E93;margin-top:2px;">Top Tier</div>
          </td>
          <td style="padding:16px 24px;text-align:center;width:34%;">
            <div style="font-size:28px;font-weight:700;color:#007AFF;">{high_count}</div>
            <div style="font-size:12px;color:#8E8E93;margin-top:2px;">High Tier</div>
          </td>
        </tr>
      </table>

      <!-- Scoring Legend (collapsed) -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
        style="background:white;border-radius:16px;overflow:hidden;margin-bottom:16px;
        box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <tr><td style="padding:14px 24px;">
          <div style="font-size:12px;color:#8E8E93;line-height:1.6;">
            <strong>Scoring:</strong>&ensp;
            <span style="color:#34C759;">■</span> Top ≥80 (Zulassung)&ensp;
            <span style="color:#007AFF;">■</span> High ≥30 (Phase III)&ensp;
            <span style="color:#FF9500;">■</span> Mid ≥10 (Phase II / Biomarker)&ensp;
            <span style="color:#8E8E93;">■</span> Low &lt;10
          </div>
        </td></tr>
      </table>

      <!-- Articles Card -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
        style="background:white;border-radius:16px;overflow:hidden;
        box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        {rows_html}
      </table>

      <!-- Footer -->
      <table width="600" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:24px 16px;text-align:center;">
          <p style="margin:0;font-size:12px;color:#AEAEB2;line-height:1.5;">
            ALS Research Screener &middot; Automatisiert via GitHub Actions<br>
            Quellen: Google News · FDA · PubMed · ClinicalTrials.gov<br>
            ⚠️ Dies ist keine medizinische Beratung.
          </p>
        </td></tr>
      </table>

    </td></tr>
  </table>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# 5. EMAIL SENDER
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    """Send HTML email via Gmail SMTP."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set. Skipping email.")
        # Write HTML to file for local testing
        Path("/home/claude/als-screener/latest_digest.html").write_text(html_body)
        log.info("Saved digest to latest_digest.html for preview.")
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = f"ALS Screener <{GMAIL_ADDRESS}>"
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
        log.info(f"Email sent to {RECIPIENT_EMAIL}")
    except Exception as e:
        log.error(f"Email send failed: {e}")
        raise


# ---------------------------------------------------------------------------
# 6. MAIN PIPELINE
# ---------------------------------------------------------------------------

def main():
    log.info("=== ALS News Screener started ===")
    now = datetime.now(timezone.utc)
    run_date = now.strftime("%d.%m.%Y %H:%M UTC")

    # --- Collect articles ---
    all_articles: list[dict] = []

    for query in GOOGLE_NEWS_QUERIES:
        all_articles.extend(fetch_google_news(query))

    # German edition for select queries
    all_articles.extend(fetch_google_news_de(
        'ALS OR "Amyotrophe Lateralsklerose" "Studie" OR "Zulassung" -Spendenlauf'
    ))

    all_articles.extend(fetch_fda_press())
    all_articles.extend(fetch_pubmed_als())
    all_articles.extend(fetch_clinicaltrials())

    log.info(f"Collected {len(all_articles)} raw articles")

    # --- Deduplicate by hash ---
    sent_hashes = load_sent_hashes()
    seen: dict[str, dict] = {}
    for art in all_articles:
        h = article_hash(art)
        if h in sent_hashes:
            continue
        if h not in seen:
            seen[h] = art

    unique_articles = list(seen.values())
    log.info(f"{len(unique_articles)} new unique articles after dedup")

    if not unique_articles:
        log.info("No new articles. Sending minimal digest.")
        html = build_email_html([], run_date)
        send_email(f"🧬 ALS Screener – Keine neuen Artikel ({run_date})", html)
        return

    # --- Score ---
    for art in unique_articles:
        score, labels = compute_score(art)
        art["_score"] = score
        art["_labels"] = labels

    # Sort by score descending
    unique_articles.sort(key=lambda a: a["_score"], reverse=True)

    # Keep top 30
    top_articles = unique_articles[:30]

    log.info(f"Top article: [{top_articles[0]['_score']}] {top_articles[0]['title'][:80]}")

    # --- Build & send email ---
    best_score = top_articles[0]["_score"]
    tier_emoji = "🏆" if best_score >= 80 else "🔬" if best_score >= 30 else "🧪"
    subject = f"{tier_emoji} ALS Screener – {len(top_articles)} Artikel | Top Score: {best_score} ({run_date})"

    html = build_email_html(top_articles, run_date)
    send_email(subject, html)

    # --- Update sent hashes ---
    new_hashes = sent_hashes | {article_hash(a) for a in top_articles}
    save_sent_hashes(new_hashes)
    log.info(f"Updated sent_articles.json ({len(new_hashes)} total hashes)")

    log.info("=== ALS News Screener finished ===")


if __name__ == "__main__":
    main()
