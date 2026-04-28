import argparse
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from os import environ
except ImportError:
    import os as environ

TELEGRAM_BOT_TOKEN = environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = environ.get("TELEGRAM_CHAT_ID")
DATABASE_PATH = environ.get("DATABASE_PATH", "seen.db")
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{35,}$")

RSS_FEEDS = [
    "https://www.nintenderos.com/feed/",
    "https://www.hobbyconsolas.com/feed",
    "https://www.vidaextra.com/feed",
    "https://www.meristation.com/feed",
    "https://news.google.com/rss/search?q=Nintendo+Switch+2+lang:es&hl=es-419&gl=ES",
    "https://vandal.elespanol.com/xml.cgi?type=noticias&format=feed",
    "https://www.nintendo.es/news.xml",
    "https://www.nextn.es/feed/",
    "https://vandal.elespanol.com/xml.cgi",
    "https://www.eurogamer.es/feed/news",
]

SWITCH_2_PATTERN = re.compile(
    r"\b(?:nintendo\s+)?switch\s*[-]?\s*2\b|\bswitch2\b",
    re.IGNORECASE,
)

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


@dataclass
class Story:
    id: str
    title: str
    link: str
    summary: str
    published: str
    source: str


def create_database(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sent_articles (id TEXT PRIMARY KEY, title TEXT, link TEXT, published TEXT, source TEXT, created_at INTEGER)"
    )
    conn.commit()
    return conn


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def mentions_switch_2(text: str) -> bool:
    return SWITCH_2_PATTERN.search(normalize_text(text)) is not None


def fetch_feed(url: str) -> List[Story]:
    logger.info("Leyendo feed: %s", url)
    feed = feedparser.parse(url)
    if feed.bozo:
        logger.warning("Feed con errores: %s - %s", url, getattr(feed, "bozo_exception", "desconocido"))

    stories: List[Story] = []
    for entry in feed.entries:
        entry_id = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not entry_id:
            continue

        story = Story(
            id=entry_id,
            title=getattr(entry, "title", "Sin título"),
            link=getattr(entry, "link", ""),
            summary=getattr(entry, "summary", ""),
            published=getattr(entry, "published", ""),
            source=getattr(feed.feed, "title", url),
        )
        stories.append(story)

    return stories


def filter_stories(stories: Iterable[Story]) -> List[Story]:
    filtered = []
    for story in stories:
        text = f"{story.title} {story.summary}"
        if mentions_switch_2(text):
            filtered.append(story)
    return filtered


def deduplicate_stories(stories: Iterable[Story]) -> List[Story]:
    deduplicated: List[Story] = []
    seen_keys: Set[str] = set()

    for story in stories:
        key = story.link.strip() or story.id.strip()
        if not key or key in seen_keys:
            continue

        seen_keys.add(key)
        deduplicated.append(story)

    return deduplicated


def get_unsent_stories(conn: sqlite3.Connection, stories: Iterable[Story]) -> List[Story]:
    cursor = conn.cursor()
    unsent: List[Story] = []
    for story in stories:
        cursor.execute("SELECT 1 FROM sent_articles WHERE id = ?", (story.id,))
        if cursor.fetchone() is None:
            unsent.append(story)
    return unsent


def mark_as_sent(conn: sqlite3.Connection, story: Story) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO sent_articles (id, title, link, published, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (story.id, story.title, story.link, story.published, story.source, int(time.time())),
    )
    conn.commit()


def build_message(story: Story) -> str:
    title = story.title.strip()
    source = story.source.strip()
    published = story.published.strip()
    url = story.link.strip()

    parts = [f"*{title}*"]
    if source:
        parts.append(f"_{source}_")
    if published:
        parts.append(f"{published}")
    parts.append(url)

    return "\n".join(parts)


def parse_telegram_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text

    description = data.get("description")
    if description:
        return description

    return str(data)


def validate_telegram_config(token: str, chat_id: str) -> bool:
    token = token.strip()
    chat_id = chat_id.strip()

    if not TELEGRAM_TOKEN_PATTERN.match(token):
        logger.error(
            "TELEGRAM_BOT_TOKEN no tiene el formato esperado. Revisa el Repository Secret en GitHub Actions."
        )
        return False

    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID está vacío. Revisa el Repository Secret en GitHub Actions.")
        return False

    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    except requests.RequestException as exc:
        logger.error("No se pudo validar el bot de Telegram con getMe: %s", exc)
        return False

    if response.status_code == 404:
        logger.error(
            "Telegram no reconoce el bot token (404 Not Found). Actualiza TELEGRAM_BOT_TOKEN en los secrets de GitHub."
        )
        return False

    if response.status_code != 200:
        logger.error("Error validando bot de Telegram: %s %s", response.status_code, parse_telegram_error(response))
        return False

    data = response.json()
    if not data.get("ok"):
        logger.error("Telegram rechazó getMe: %s", data)
        return False

    bot = data.get("result", {})
    logger.info("Bot de Telegram validado: @%s", bot.get("username", "desconocido"))
    return True


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    payload = {
        "chat_id": chat_id.strip(),
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, data=payload, timeout=15)
    except requests.RequestException as exc:
        logger.error("Error conectando con Telegram: %s", exc)
        return False

    if response.status_code != 200:
        logger.error("Error enviando mensaje: %s %s", response.status_code, parse_telegram_error(response))
        return False

    data = response.json()
    if not data.get("ok"):
        logger.error("Telegram respondió con error: %s", data)
        return False

    return True


def run(dry_run: bool = False) -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.")
        return 1

    if not dry_run and not validate_telegram_config(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
        return 1

    db_path = Path(DATABASE_PATH)
    conn = create_database(str(db_path))

    all_stories: List[Story] = []
    for feed_url in RSS_FEEDS:
        try:
            stories = fetch_feed(feed_url)
            all_stories.extend(stories)
        except Exception as exc:
            logger.exception("Error leyendo el feed %s: %s", feed_url, exc)

    stories = filter_stories(all_stories)
    stories = deduplicate_stories(stories)
    stories = sorted(stories, key=lambda story: story.published or "", reverse=True)
    unsent_stories = get_unsent_stories(conn, stories)

    logger.info("Encontradas %d noticias coincidentes, %d no enviadas", len(stories), len(unsent_stories))

    for story in unsent_stories:
        message = build_message(story)
        logger.info("Noticia nueva: %s", story.title)
        if dry_run:
            logger.info("Dry-run: no se envía mensaje. Texto:\n%s", message)
            mark_as_sent(conn, story)
            continue

        sent = send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
        if sent:
            mark_as_sent(conn, story)
            time.sleep(1)
        else:
            logger.warning("No se pudo enviar la noticia: %s", story.link)

    if not unsent_stories:
        logger.info("No hay noticias nuevas para enviar.")

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agregador de noticias Nintendo Switch 2 para Telegram")
    parser.add_argument("--dry-run", action="store_true", help="No envía mensajes, solo muestra qué se enviaría")
    args = parser.parse_args()
    raise SystemExit(run(dry_run=args.dry_run))
