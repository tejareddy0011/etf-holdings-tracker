import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from app.config import (
    ALERT_RECIPIENT_EMAIL,
    DATA_DIR,
    SCHEDULE_TIMEZONE,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)
from app.database import get_settings, log_alert


class KnownErrorType:
    URL_UNREACHABLE_OR_LAYOUT_CHANGED = "Target URL unreachable or layout changed"
    POPUPS_FAILED_TO_CLEAR = "Pop-ups failed to clear"
    DOWNLOAD_LINK_MISSING_OR_ALTERED = '"Download Daily Fund Holdings" link missing or altered'
    EXCEL_FAILED_OR_CORRUPTED = "Excel file download failed or file is corrupted"


class ScraperBotError(Exception):
    def __init__(self, error_type: str, message: str):
        super().__init__(f"[{error_type}] {message}")
        self.error_type = error_type
        self.message = message


def send_failure_notification(error_type: str, error_details: str) -> dict:
    """
    Immediately sends an email notification to gvarun@gmail.com (or configured recipient)
    including the current date (PT) and the specific error type.
    Also writes to data/alerts_outbox.log and alert_logs table for full auditability.
    """
    settings = get_settings()
    recipient = settings.get("alert_email") or ALERT_RECIPIENT_EMAIL
    now_pt = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE))
    current_date_str = now_pt.strftime("%Y-%m-%d %H:%M:%S %Z")
    short_date_str = now_pt.strftime("%Y-%m-%d")

    subject = f"[ETF Scraper Alert] Failure on {short_date_str}: {error_type}"
    body = (
        f"Automated ETF Holdings Scraper Failure Notification\n"
        f"===================================================\n"
        f"Current Date: {current_date_str}\n"
        f"Target ETF:   MFS Value ETF (MFSV)\n"
        f"Error Type:   {error_type}\n"
        f"Details:      {error_details}\n\n"
        f"Known Error Categories Monitored:\n"
        f"  1. {KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED}\n"
        f"  2. {KnownErrorType.POPUPS_FAILED_TO_CLEAR}\n"
        f"  3. {KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED}\n"
        f"  4. {KnownErrorType.EXCEL_FAILED_OR_CORRUPTED}\n"
    )

    delivery_status = "LOGGED_OUTBOX (SMTP credentials not configured)"
    if SMTP_USER and SMTP_PASSWORD:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = SMTP_FROM
            msg["To"] = recipient
            msg.set_content(body)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
            delivery_status = "SENT_SMTP"
        except Exception as smtp_err:
            delivery_status = f"SMTP_ERROR_FALLBACK_LOGGED ({smtp_err})"

    # Always write to local outbox log for immediate verification
    outbox_path = DATA_DIR / "alerts_outbox.log"
    with open(outbox_path, "a", encoding="utf-8") as f:
        f.write(f"\n--- EMAIL NOTIFICATION [{delivery_status}] ---\n")
        f.write(f"To: {recipient}\nSubject: {subject}\n{body}\n")

    alert_id = log_alert(
        recipient_email=recipient,
        error_date=current_date_str,
        error_type=error_type,
        error_details=error_details,
        delivery_status=delivery_status,
    )
    return {
        "alert_id": alert_id,
        "recipient": recipient,
        "date": current_date_str,
        "error_type": error_type,
        "error_details": error_details,
        "delivery_status": delivery_status,
    }
