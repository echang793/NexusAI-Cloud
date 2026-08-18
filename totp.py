"""TOTP 2FA: secret/QR generation, code verification, backup codes.

Standard authenticator-app flow (Google Authenticator, Authy, 1Password,
etc) via pyotp — free, no per-message cost, not phishable via SIM-swap the
way SMS codes are.
"""

import base64
import io
import secrets
import string

import pyotp
import qrcode

ISSUER = "NexusAI Cloud"


def generate_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """Renders the otpauth:// URI as a QR code, returns a data: URI (PNG) —
    no file written to disk, embeds directly in an <img src>."""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    # valid_window=1 tolerates ~30s of clock drift on either side, standard
    # practice for TOTP so users on a slightly-off phone clock aren't locked out.
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


_BACKUP_ALPHABET = string.ascii_uppercase + string.digits


def generate_backup_codes(n: int = 10) -> list:
    """Plaintext codes to show the user once — caller hashes before storing."""
    return ["".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10)) for _ in range(n)]
