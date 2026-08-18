"""Thin Brevo (formerly Sendinblue) wrapper for verification/reset emails.

Switched from Resend: Resend's onboarding@resend.dev sender can only send to
the email address the Resend account itself was signed up with, until a
domain is verified — fine for testing, useless for real friends signing up.
Brevo's free tier (300/day, permanent, no card) sends to ANY recipient once
you verify a single sender address you own (Settings > Senders, click a
confirmation link) — no domain purchase/DNS needed.

FROM_ADDRESS must be that verified sender address, set via MAIL_FROM.
If BREVO_API_KEY isn't set (local dev without a Brevo account), emails are
logged instead of sent — lets the rest of the flow be exercised without
needing real email delivery.
"""

import logging
import os

import requests

log = logging.getLogger("nexusai_cloud.mail")

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
FROM_ADDRESS = os.getenv("MAIL_FROM", "").strip()
FROM_NAME = "NexusAI Cloud"


def _send(to: str, subject: str, html: str) -> bool:
    if not BREVO_API_KEY or not FROM_ADDRESS:
        log.warning("BREVO_API_KEY/MAIL_FROM not set — email NOT sent, logging instead.\nTo: %s\nSubject: %s\n%s", to, subject, html)
        return False
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
            json={
                "sender": {"name": FROM_NAME, "email": FROM_ADDRESS},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            log.error("Brevo send failed (%s): %s", resp.status_code, resp.text[:500])
            return False
        return True
    except requests.RequestException as e:
        log.error("Brevo send raised: %s", e)
        return False


def send_verification_email(to: str, verify_link: str) -> bool:
    html = f"""
    <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
      <h2>Verify your email</h2>
      <p>Confirm your email to finish setting up your NexusAI Cloud account.</p>
      <p><a href="{verify_link}" style="background:#0a84ff; color:white; padding:10px 18px;
        border-radius:8px; text-decoration:none; display:inline-block;">Verify email</a></p>
      <p style="color:#888; font-size:12px;">This link expires in 24 hours. If you didn't sign up for
        NexusAI Cloud, you can ignore this email.</p>
    </div>
    """
    return _send(to, "Verify your NexusAI Cloud email", html)


def send_password_reset_email(to: str, reset_link: str) -> bool:
    html = f"""
    <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
      <h2>Reset your password</h2>
      <p>Click below to set a new password for your NexusAI Cloud account.</p>
      <p><a href="{reset_link}" style="background:#0a84ff; color:white; padding:10px 18px;
        border-radius:8px; text-decoration:none; display:inline-block;">Reset password</a></p>
      <p style="color:#888; font-size:12px;">This link expires in 1 hour. If you didn't request this,
        you can ignore this email — your password won't change.</p>
    </div>
    """
    return _send(to, "Reset your NexusAI Cloud password", html)
