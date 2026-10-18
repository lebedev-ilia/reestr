#!/usr/bin/env python3
"""Отбор и скоринг заказов FL.ru под профиль «агент + пользователь».

Профиль (со слов пользователя 2026-10-13):
  - направления: Python-автоматизация/парсинг/API, TG-боты, LLM/агенты/RAG, CV (YOLO, трекинг),
    ML/DS, аудио (RVC/TTS/ASR), инфраструктура (серверы, GPU, Docker, деплой), смежное «на ходу»;
  - бюджет от 10 000 ₽;
  - время 6+ ч/день; аккаунт FL.ru без отзывов -> нужны заказы с низким порогом доверия;
  - критерий: реальная выполнимость агентом + пользователем.

Запуск: python3 my_agent/fl_rank.py --top 80
"""
import argparse
import json
import re
from pathlib import Path

NEG = [
    "seo", "продвижени", "настроить реклам", "яндекс директ", "директолог", "таргет",
    "смм", "smm", "контент-план", "контент план", "копирайт", "рерайт", "написать текст",
    "статьи", "дизайн", "логотип", "баннер", "иллюстрац", "айдент", "брендбук",
    "верстк", "верстальщик", "3d-модел", "3d модел", "анимац", "монтаж видео",
    "видеомонтаж", "рилс", "shorts", "обзвон", "менеджер по продаж", "холодн",
    "оператор", "курьер", "перевод", "репетитор", "бухгалтер", "юрист", "смета",
    "сметчик", "чертеж", "чертёж", "solidworks", "компас", "автокад", "autocad",
    "дизайн интерьер", "строительств", "отделоч", "сантехник", "электрик",
    "генеральный план", "ландшафт", "выкуп", "отзывы на маркетплейс", "wildberries",
    "ozon", "вайлдберриз", "карточки товара", "инфографик", "тендер", "госзакуп",
    "нейросеть для контента", "аватарки", "фотошоп", "ретуш", "обработка фото",
    "написание постов", "администратор группы", "модератор", "ассистент", "секретарь",
    "помощник руководителя", "личный ассистент", "набор текста", "транскрибац",
    "телефон", "прозвон", "интервью", "психолог", "нутрициолог", "фитнес", "массаж",
]

POS = {
    # (вес, [ключи])
    "A. Агенты/LLM": (6, [
        "ии-агент", "ии агент", "ai-агент", "ai агент", "агент", "llm", "gpt", "gigachat",
        "yandexgpt", "openai", "claude", "gemini", "deepseek", "qwen", "ollama", "rag",
        "векторн", "embedding", "промт", "prompt", "langchain", "n8n", "make.com", "aiogram",
        "нейросет", "нейронн", "искусственный интеллект", "чат-бот", "чатбот", "gpt-4",
        "fine-tun", "дообуч", "файнтюн", "assistant", "ассистент по", "mcp",
    ]),
    "B. Автоматизация/боты": (5, [
        "автоматизац", "парсер", "парсинг", "scrapy", "selenium", "playwright", "crm",
        "amocrm", "битрикс", "api", "интеграц", "webhook", "telegram", "телеграм", "бот",
        "whatsapp", "вотсап", "авито", "google sheets", "гугл таблиц", "excel", "скрипт",
        "python", "bash", "cron", "airflow", "etl", "выгрузк", "синхронизац", "уведомлен",
    ]),
    "C. CV/видеоаналитика": (6, [
        "компьютерное зрение", "computer vision", "yolo", "детекц", "detection", "трекинг",
        "tracking", "распознаван", "opencv", "видеоаналитик", "сегментац", "нейросеть для видео",
        "камер", "cv", "ocr", "маска", "разметк", "dataset", "датасет",
    ]),
    "D. ML/DS": (5, [
        "машинное обучение", "machine learning", "ml", "обучение модел", "обучить модел",
        "модель", "прогноз", "предсказан", "классификац", "кластер", "регресс", "pytorch",
        "tensorflow", "sklearn", "python-разработ", "data science", "аналитик данных",
        "pandas", "numpy", "статистик",
    ]),
    "E. Аудио/голос": (6, [
        "rvc", "tts", "синтез речи", "распознавание речи", "asr", "whisper", "голос",
        "voice", "озвучк", "аудио", "транскрип", "шумоподав", "speech", "диаризац",
        "клонирован", "voiceover", "подкаст",
    ]),
    "F. Инфраструктура/GPU": (5, [
        "сервер", "docker", "kubernetes", "devops", "деплой", "развертыван", "развёртыван",
        "linux", "ubuntu", "gpu", "cuda", "nvidia", "vps", "администрирован", "nginx",
        "база данных", "postgres", "mysql", "миграц", "мониторинг", "zabbix", "grafana",
        "1с", "хостинг", "бэкап", "резервное копирован",
    ]),
    "G. Разработка/продукт": (3, [
        "разработ", "приложен", "сайт", "сервис", "backend", "фронтенд", "frontend",
        "микросервис", "fastapi", "django", "flask", "react", "node", "rest", "mvp",
        "личный кабинет", "телеграм-мини", "мини-приложен", "desktop", "парсер данных",
    ]),
}

LOW_TRUST_OK = [  # сигналы «заказчик возьмёт новичка»
    "тестовое", "небольш", "недорого", "стартов", "первый этап", "пилот", "прототип",
    "поэтапно", "под ключ", "срочно", "маленьк", "простой",
]


def score(o):
    t = (o["title"] or "").lower()
    d = (o["desc"] or "").lower()
    text = t + " " + d
    neg = [k for k in NEG if k in text]
    hits, total = {}, 0
    for grp, (w, keys) in POS.items():
        h = [k for k in keys if k in text]
        if h:
            # заголовок весит вдвое
            th = [k for k in h if k in t]
            total += w * min(len(h), 4) + w * len(th)
            hits[grp] = h[:6]
    b = o.get("budget") or 0
    if b >= 10000:
        total += 3
    if b >= 30000:
        total += 3
    if b >= 80000:
        total += 3
    if b >= 200000:
        total += 2
    if b == 0:
        total -= 1  # бюджет не указан
    total -= 8 * len(neg)
    return total, hits, neg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="my_agent/fl_orders.json")
    ap.add_argument("--out", default="my_agent/fl_shortlist.json")
    ap.add_argument("--top", type=int, default=90)
    ap.add_argument("--min-budget", type=int, default=10000)
    args = ap.parse_args()

    data = json.load(open(args.inp, encoding="utf-8"))
    rows = []
    for o in data["orders"]:
        s, hits, neg = score(o)
        o = dict(o, score=s, hits=hits, neg=neg)
        rows.append(o)

    clean = [o for o in rows if not o["neg"] and o["score"] > 0
             and (o["budget"] is None or o["budget"] >= args.min_budget)]
    clean.sort(key=lambda x: (-x["score"], -(x["budget"] or 0)))
    top = clean[:args.top]

    Path(args.out).write_text(json.dumps(top, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"всего {len(rows)}; прошло фильтр {len(clean)}; в шортлисте {len(top)} -> {args.out}\n")
    for o in top:
        b = f"{o['budget']:,}".replace(",", " ") if o["budget"] else "—"
        print(f"[{o['score']:3d}] {b:>9} ₽ | откл.{o['responses'] if o['responses'] is not None else '?':>3} | "
              f"{o['category'][:12]:12s} | {o['title'][:80]}")
        print(f"       {o['url']}")
        print(f"       {o['desc'][:150]}")


if __name__ == "__main__":
    main()
