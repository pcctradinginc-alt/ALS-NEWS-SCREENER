import os
import smtplib
import json
import logging
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def send_email(subject, html_body):
    # Diese Namen müssen EXAKT mit den Secrets in GitHub übereinstimmen
    gmail_user = os.environ.get('GMAIL_USER') 
    gmail_pass = os.environ.get('GMAIL_PASS')
    recipient = os.environ.get('RECIPIENT')

    if not gmail_user or not gmail_pass:
        logging.error("GMAIL_USER oder GMAIL_PASS nicht gesetzt. Bitte Secrets prüfen!")
        return

    # Pfad-Fehler beheben: Wir speichern die Datei einfach im aktuellen Verzeichnis
    try:
        Path("latest_digest.html").write_text(html_body, encoding='utf-8')
    except Exception as e:
        logging.warning(f"Konnte HTML-Datei nicht lokal speichern: {e}")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = gmail_user
    msg['To'] = recipient if recipient else gmail_user

    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, msg['To'], msg.as_string())
        logging.info("E-Mail erfolgreich versendet!")
    except Exception as e:
        logging.error(f"Fehler beim E-Mail-Versand: {e}")

# ... (Hier der Rest deiner main() Logik für Scraper und Scoring) ...
# Stelle sicher, dass am Ende main() aufgerufen wird.
