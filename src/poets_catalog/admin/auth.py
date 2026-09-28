"""管理员认证：Argon2id 密码、数据库限时会话、CSRF 与失败锁定。"""
import getpass
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..db import engine
from ..models import AdminSession, AdminUser

COOKIE_NAME = "poets_admin_session"
SESSION_SECONDS = 8 * 60 * 60
FAILED_LIMIT = 5
LOCK_SECONDS = 15 * 60
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
# 不暴露用户名是否存在：未知账号也执行一次真实 Argon2 验证。
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_password(password: str):
    if len(password) < 12 or len(password) > 256:
        raise ValueError("密码长度须为 12–256 个字符")


def create_user(db: Engine, username: str, password: str) -> int:
    """仅由本机 CLI 创建账号，不提供免登录的 HTTP 注册端点。"""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,79}", username):
        raise ValueError("账号须以字母开头，长度 3–80，仅使用字母数字及 _.-")
    validate_password(password)
    with Session(db) as session, session.begin():
        if session.scalar(select(AdminUser.id).where(AdminUser.username == username)):
            raise ValueError("账号已存在")
        user = AdminUser(username=username, password_hash=HASHER.hash(password))
        session.add(user)
        session.flush()
        return user.id


def set_password(db: Engine, username: str, password: str):
    """本机重置密码并使该账号所有已有会话立即失效。"""
    validate_password(password)
    with Session(db) as session, session.begin():
        user = session.scalar(select(AdminUser).where(AdminUser.username == username).with_for_update())
        if user is None:
            raise ValueError("账号不存在")
        user.password_hash = HASHER.hash(password)
        user.password_changed_at = datetime.now(timezone.utc)
        session.query(AdminSession).filter(AdminSession.user_id == user.id).delete(synchronize_session=False)


def authenticate(session: Session, username: str, password: str):
    """账号锁定状态与失败次数持久化，进程重启也不能绕过。"""
    now = datetime.now(timezone.utc)
    user = session.scalar(select(AdminUser).where(AdminUser.username == username).with_for_update())
    if user is None:
        try:
            HASHER.verify(DUMMY_HASH, password)
        except (VerificationError, VerifyMismatchError):
            pass
        return None
    if not user.is_active or (user.locked_until and user.locked_until > now):
        return None
    try:
        valid = HASHER.verify(user.password_hash, password)
    except (VerificationError, VerifyMismatchError):
        valid = False
    if not valid:
        user.failed_attempts += 1
        if user.failed_attempts >= FAILED_LIMIT:
            user.locked_until = now + timedelta(seconds=LOCK_SECONDS)
            user.failed_attempts = 0
        return None
    user.failed_attempts = 0
    user.locked_until = None
    if HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = HASHER.hash(password)
    return user


def new_session(session: Session, user: AdminUser):
    """Cookie 随机值仅保存摘要；CSRF 值需在刷新后经已验证会话恢复。"""
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)
    row = AdminSession(user_id=user.id, token_hash=sha256(token), csrf_token=csrf, expires_at=expires)
    session.add(row)
    return token, csrf, expires


def session_for_request(session: Session, request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 256:
        return None
    row = session.scalar(select(AdminSession).where(AdminSession.token_hash == sha256(token),
        AdminSession.revoked_at.is_(None), AdminSession.expires_at > datetime.now(timezone.utc)))
    if row is None:
        return None
    user = session.get(AdminUser, row.user_id)
    return (user, row) if user is not None and user.is_active else None


def require_session(session: Session, request: Request, *, mutation: bool = False):
    found = session_for_request(session, request)
    if found is None:
        raise HTTPException(401, "请先登录管理员账号")
    if mutation:
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(csrf, found[1].csrf_token):
            raise HTTPException(403, "CSRF 校验失败")
    return found


def main():
    """交互式 CLI；不从命令行参数读取明文密码，避免出现在 shell 历史中。"""
    import argparse
    parser = argparse.ArgumentParser(description="创建或重置管理员账号")
    parser.add_argument("command", choices=["create", "reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("新密码（至少 12 位）：")
    confirmation = getpass.getpass("再次输入密码：")
    if password != confirmation:
        parser.error("两次密码不一致")
    try:
        if args.command == "create":
            create_user(engine(), args.username, password)
            print("管理员账号已创建")
        else:
            set_password(engine(), args.username, password)
            print("密码已更新，旧会话已失效")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
