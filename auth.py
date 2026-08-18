"""Flask-Login wiring — thin User wrapper over db.py's users table."""

from flask_login import LoginManager, UserMixin

import db


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.email = row["email"]
        self.email_verified = row.get("email_verified", False)
        self.totp_enabled = row.get("totp_enabled", False)

    @staticmethod
    def get(user_id):
        row = db.get_user_by_id(int(user_id))
        return User(row) if row else None

    @staticmethod
    def authenticate(email: str, password: str):
        """Password check only — does NOT account for lockout/verification/
        2FA, those are the caller's (server.py's route) responsibility since
        each needs a different HTTP response. Returns (user_or_None, row_or_None)
        so the caller has the raw row for lockout/verified checks without a
        second DB round-trip."""
        row = db.get_user_by_email(email)
        if row and db.verify_password(row, password):
            return User(row), row
        return None, row


def init_login_manager(app):
    lm = LoginManager()
    lm.login_view = "login_page"
    lm.init_app(app)

    @lm.user_loader
    def load_user(user_id):
        return User.get(user_id)

    return lm
