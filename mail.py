"""Thin Resend wrapper for verification/reset emails.

Free tier: 3,000/mo, 100/day, sends from onboarding@resend.dev without
needing your own domain verified. If RESEND_API_KEY isn't set (local dev
without a Resend account), emails are logged instead of sent — lets the
rest of the flow be exercised without needing real email delivery.
"""

import logging
import os

import requests

log = logging.getLogger("nexusai_cloud.mail")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_ADDRESS = os.getenv("MAIL_FROM", "NexusAI Cloud <onboarding@resend.dev>")


def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — email NOT sent, logging instead.\nTo: %s\nSubject: %s\n%s", to, subject, html)
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        if resp.status_code >= 300:
            log.error("Resend send failed (%s): %s", resp.status_code, resp.text[:500])
            return False
        return True
    except requests.RequestException as e:
        log.error("Resend send raised: %s", e)
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
