# 🧬 ALS Research News Screener

Automatisierter täglicher Digest über die wichtigsten ALS-Forschungsnachrichten – direkt in dein Gmail-Postfach.

## Was macht das Tool?

1. **Sammelt Nachrichten** aus 4 Quellen:
   - Google News (EN + DE) mit spezifischen ALS-Suchoperatoren
   - FDA Pressemitteilungen
   - PubMed (neueste Publikationen)
   - ClinicalTrials.gov (Studien-Updates)

2. **Bewertet jeden Artikel** mit einem intelligenten Scoring-System:

   | Tier | Score | Bedeutung |
   |------|-------|-----------|
   | 🏆 Top | ≥80 | FDA/EMA-Zulassung – Game Changer |
   | 🔬 High | ≥30 | Phase-III-Ergebnisse |
   | 🧪 Mid | ≥10 | Phase II / Biomarker / Neue Fortschritte |
   | 📋 Low | <10 | Phase I / Finanznachrichten / Beobachten |

3. **Filtert Rauschen** automatisch:
   - Spendenläufe, Ice Bucket Challenges → ausgeschlossen
   - Mausmodell-Ergebnisse → Score-Abzug (-30)
   - Dubiose Stammzell-Kliniken → Score-Abzug (-40)
   - Finanzportale (MarketWatch etc.) → Multiplikator ×0.5

4. **Sendet einen Apple-Design HTML-Newsletter** per Gmail

## Einrichtung (5 Minuten)

### 1. Repository forken/klonen

```bash
git clone https://github.com/DEIN-USERNAME/als-screener.git
cd als-screener
```

### 2. Gmail App-Passwort erstellen

> ⚠️ Verwende **nicht** dein normales Gmail-Passwort!

1. Gehe zu [Google App Passwords](https://myaccount.google.com/apppasswords)
2. Wähle "Mail" und "Anderes (Name eingeben)" → z.B. "ALS Screener"
3. Kopiere das generierte 16-stellige Passwort

### 3. GitHub Secrets setzen

Gehe zu deinem Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Wert |
|---|---|
| `GMAIL_ADDRESS` | deine-email@gmail.com |
| `GMAIL_APP_PASSWORD` | das 16-stellige App-Passwort |
| `RECIPIENT_EMAIL` | Empfänger-Email (optional, Standard = GMAIL_ADDRESS) |

### 4. Fertig! 🎉

Der Screener läuft automatisch **täglich um ~08:00 Uhr deutscher Zeit**.

Du kannst ihn auch manuell auslösen: Repository → **Actions** → **ALS News Screener** → **Run workflow**.

## Lokal testen

```bash
pip install -r requirements.txt

# Ohne Email (speichert HTML-Datei lokal):
python main.py

# Mit Email:
export GMAIL_ADDRESS="deine@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
python main.py
```

## Scoring-Details

### Phasen-Scoring (höchste Treffer zählt)
- **Zulassung/Approval** → +100 Punkte
- **Phase III** → +40 Punkte
- **Phase II** → +15 Punkte
- **Phase I** → +5 Punkte

### Bonus-Keywords
- ALSFRS-R Endpunkt → +20
- FDA/EMA erwähnt → +15
- Biomarker (NfL) → +10
- Gene Therapy / ASO → +10
- Signifikantes Outcome → +15
- Artikel < 12h alt → +10

### Quellen-Multiplikator
- Premium (Nature, NEJM, Lancet, FDA, EMA, Reuters, STAT): **×2.0–2.5**
- Standard: **×1.0**
- Finanzportale (MarketWatch, Yahoo Finance, SeekingAlpha): **×0.4–0.6**

### Abzüge
- Nur Mausmodell/Präklinik → **-30**
- Dubiose Heilversprechen → **-40**
- Fundraiser/Spendenlauf → **-50**

## Dateien

```
als-screener/
├── main.py                          # Haupt-Script
├── requirements.txt                 # Python-Abhängigkeiten
├── sent_articles.json               # Duplikat-Tracking (auto-updated)
├── .github/workflows/
│   └── als-screener.yml             # GitHub Actions Workflow
├── .gitignore
└── README.md
```

## Lizenz

MIT – Frei nutzbar. Dies ist kein medizinisches Werkzeug.
