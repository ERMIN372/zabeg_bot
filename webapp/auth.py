"""Проверка подлинности запросов из Telegram Mini App.

Telegram подписывает `initData` (строку query-параметров) секретом, производным
от токена бота. Проверяем подпись по официальному алгоритму — так сервер
доверяет `user.id` только если данные действительно пришли из Telegram и не
были подделаны. Кнопку mini app видят лишь админы, но настоящая защита —
именно здесь: любой запрос с чужим/поддельным initData отклоняется.

См. https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 86400
) -> dict | None:
    """Валидирует initData. Возвращает распарсенные поля (с dict в `user`)
    или None, если подпись неверна/просрочена."""
    if not init_data or not bot_token:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None

    # Защита от переигрывания старого initData
    if max_age_seconds:
        try:
            auth_date = int(parsed.get("auth_date", "0"))
        except ValueError:
            return None
        if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
            return None

    raw_user = parsed.get("user")
    if raw_user:
        try:
            parsed["user"] = json.loads(raw_user)
        except (ValueError, TypeError):
            return None
    return parsed


def admin_from_init_data(
    init_data: str, bot_token: str, admin_ids: tuple[int, ...]
) -> dict | None:
    """Валидная подпись + пользователь входит в ADMIN_IDS -> данные пользователя.
    Иначе None (запрос должен получить 401)."""
    data = validate_init_data(init_data, bot_token)
    if not data:
        return None
    user = data.get("user")
    if not isinstance(user, dict) or user.get("id") not in admin_ids:
        return None
    return user
