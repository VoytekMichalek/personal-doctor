import hashlib
import hmac
import secrets

from sqlalchemy import delete, func, select

from .db import LoginAttempt, LoginSession, now


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt:{salt}:{derived}"


def verify_password(password, encoded):
    if not encoded or len(password) > 1024:
        return False
    try:
        scheme, salt, _ = encoded.split(":")
        return scheme == "scrypt" and hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authenticated(request, factory, settings):
    if (
        settings.local_auth_bypass
        and request.client
        and request.client.host in ("127.0.0.1", "::1", "testclient")
    ):
        return True
    token = request.cookies.get("pd_session", "")
    if not token:
        return False
    with factory() as session:
        record = session.get(LoginSession, token_hash(token))
        return bool(record and record.expires_at > now())


def login(factory, settings, password):
    with factory.begin() as session:
        session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - 600))
        failed = session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.created_at > now() - 300, LoginAttempt.successful.is_(False))
        )
        if failed >= 10:
            return None, "Too many attempts. Wait five minutes, then try again."
        ok = verify_password(password, settings.password_hash)
        session.add(LoginAttempt(successful=ok))
        if not ok:
            return None, "That password wasn’t recognised. Check it and try again."
        session.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
        token = secrets.token_urlsafe(32)
        session.add(LoginSession(token_hash=token_hash(token), expires_at=now() + 86400 * 7))
        return token, None
