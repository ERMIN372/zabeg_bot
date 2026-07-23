"""Проверка валидации Telegram initData (webapp/auth.py)."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from webapp.auth import admin_from_init_data, validate_init_data

TOKEN = "123456:TEST-TOKEN"


def make_init_data(token=TOKEN, user_id=42, auth_date=None, tamper=False) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAAA",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        h = "0" * len(h)
    return urlencode({**fields, "hash": h})


def test_valid_init_data():
    data = validate_init_data(make_init_data(), TOKEN)
    assert data is not None
    assert data["user"]["id"] == 42


def test_tampered_hash_rejected():
    assert validate_init_data(make_init_data(tamper=True), TOKEN) is None


def test_wrong_token_rejected():
    # подпись сделана правильным токеном, проверяем чужим
    assert validate_init_data(make_init_data(), "999:OTHER") is None


def test_expired_rejected():
    old = int(time.time()) - 90000  # > 24 ч
    assert validate_init_data(make_init_data(auth_date=old), TOKEN) is None


def test_empty_rejected():
    assert validate_init_data("", TOKEN) is None
    assert validate_init_data("hash=abc", TOKEN) is None


def test_admin_gate():
    admins = (42, 7)
    init = make_init_data(user_id=42)
    assert admin_from_init_data(init, TOKEN, admins)["id"] == 42
    # не-админ с валидной подписью — отклоняем
    assert admin_from_init_data(make_init_data(user_id=999), TOKEN, admins) is None
    # валидной подписи нет — отклоняем
    assert admin_from_init_data(make_init_data(tamper=True), TOKEN, admins) is None
