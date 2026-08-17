"""Flask-Login wiring — thin User wrapper over db.py's users table."""

from flask_login import LoginManager, UserMixin

import db


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.email = row["email"]

    @staticmethod
    def get(user_id):
        row = db.get_user_by_id(int(user_id))
        return User(row) if row else None

    @staticmethod
    def authenticate(email: str, password: str):
        row = db.get_user_by_email(email)
        if row and db.verify_password(row, password):
            return User(row)
        return None


def init_login_manager(app):
    lm = LoginManager()
    lm.login_view = "login_page"
    lm.init_app(app)

    @lm.user_loader
    def load_user(user_id):
        return User.get(user_id)

    return lm
