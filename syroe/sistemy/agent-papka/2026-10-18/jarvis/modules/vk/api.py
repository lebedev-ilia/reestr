"""Тонкая обёртка над VK API. Только транспорт, никакой логики.

Без внешних зависимостей: urllib из стандартной библиотеки, синхронные вызовы
уводятся в поток вызывающим. Меньше зависимостей — меньше того, что сломается
при обновлении.
"""

import json
import urllib.parse
import urllib.request

API = "https://api.vk.com/method/"
VERSION = "5.199"

TIMEOUT = 40  # long-poll ждёт до 25 с, запас на сеть


class VKError(Exception):
    """Ошибка на стороне ВК. Код и сообщение — как их вернул API."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"VK error {code}: {message}")


def call(method: str, token: str, **params) -> dict:
    """Вызов метода API. Возвращает содержимое response."""
    params = {k: v for k, v in params.items() if v is not None}
    params.update(access_token=token, v=VERSION)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API + method, data=data)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = json.loads(r.read().decode())
    if "error" in body:
        e = body["error"]
        raise VKError(e.get("error_code", 0), e.get("error_msg", "?"))
    return body.get("response", {})


def poll(server: str, key: str, ts: str, wait: int = 25) -> dict:
    """Один цикл Bots Long Poll. Блокирует до wait секунд."""
    url = f"{server}?" + urllib.parse.urlencode(
        {"act": "a_check", "key": key, "ts": ts, "wait": wait})
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def download(url: str) -> bytes:
    """Скачать вложение (голосовое, файл)."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return r.read()
