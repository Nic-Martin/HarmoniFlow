"""Gmail delivery helpers for HarmoniFlow verification messages."""

import os
import smtplib
import ssl
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SENDER_EMAIL = os.getenv("GMAIL_SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()


def _send_email(receiver_email, subject, body):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("[EMAIL SERVICE ERROR] Missing GMAIL_SENDER_EMAIL or GMAIL_APP_PASSWORD.")
        return False

    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print(f"[EMAIL SERVICE] Message sent to {receiver_email}")
        return True
    except Exception as exc:
        print(f"[EMAIL SERVICE ERROR] Failed to send email: {exc}")
        return False


def send_signup_otp_email(receiver_email, username, otp_code):
    body = f"""Hello {username},

Your HarmoniFlow signup verification code is: {otp_code}

The code expires in 10 minutes. Enter it in the app to finish creating your account.
If you did not request this, you can ignore this email.

HarmoniFlow
"""
    return _send_email(
        receiver_email,
        "HarmoniFlow - Verify your email address",
        body,
    )


def send_otp_email(receiver_email, username, otp_code):
    """Send the existing password-reset OTP email."""
    body = f"""Hello {username},

You requested a password reset for your HarmoniFlow account.
Your 6-digit verification code is: {otp_code}

This code is valid for 10 minutes. If you did not request this, please ignore this email.

HarmoniFlow Security Team
"""
    return _send_email(
        receiver_email,
        "HarmoniFlow - Password Reset Verification Code",
        body,
    )
