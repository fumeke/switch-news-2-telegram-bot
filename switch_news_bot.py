import argparse
import html
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime
from os import environ
from pathlib import Path
from typing import Iterable, List, Set
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = environ.get("TELEGRAM_CHAT_ID", "")
DATABASE_PATH = environ.get("DATABASE_PATH", "seen.db")
TRANSLATION_ENABLED = environ.get("TRANSLATION_ENABLED", "true").lower() in {"1", "true", "yes", "si"}
MIN_RELEVANCE_SCORE = int(environ.get("MIN_RELEVANCE_SCORE", "4"))
MAX_ARTICLES_PER_RUN = int(environ.get("MAX_ARTICLES_PER_RUN", "10"))
CHANNEL_TIMEZONE = environ.get("CHANNEL_TIMEZONE", "Europe/Madrid")
PROMO_HOUR = int(environ.get("PROMO_HOUR", "21"))
PROMO_TEXT = environ.get(
    "PROMO_TEXT",
    "🎮 <b>La actualidad de Nintendo se disfruta más en compañía.</b>\n\n"
    "¿Tienes un amigo que vive pendiente de Switch 2? "
    "Envíale este canal y que no se pierda la próxima gran noticia. 🚀",
)
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{35,}$")


@dataclass(frozen=True)
class FeedConfig:
    url: str
    language: str = "es"
    name: str = ""


RSS_FEEDS = [
    FeedConfig("https://www.nintenderos.com/feed/", "es", "Nintenderos"),
    FeedConfig("https://www.hobbyconsolas.com/feed", "es", "Hobby Consolas"),
    FeedConfig("https://www.vidaextra.com/feed", "es", "VidaExtra"),
    FeedConfig("https://www.meristation.com/feed", "es", "MeriStation"),
    FeedConfig("https://news.google.com/rss/search?q=Nintendo+Switch+2+lang:es&hl=es&gl=ES&ceid=ES:es", "es", "Google News"),
    FeedConfig("https://vandal.elespanol.com/xml.cgi?type=noticias&format=feed", "es", "Vandal"),
    FeedConfig("https://www.nextn.es/feed/", "es", "NextN"),
    FeedConfig("https://www.eurogamer.es/feed/news", "es", "Eurogamer.es"),
    FeedConfig("https://www.nintendolife.com/feeds/latest", "en", "Nintendo Life"),
    FeedConfig("https://feeds.ign.com/ign/games-all", "en", "IGN"),
    FeedConfig("https://www.eurogamer.net/feed", "en", "Eurogamer"),
    FeedConfig("https://www.theverge.com/rss/index.xml", "en", "The Verge"),
]

SWITCH_2_TERMS = (
    (re.compile(r"\b(?:nintendo\s+)?switch\s*[-]?\s*2\b|\bswitch2\b", re.I), 6),
    (re.compile(r"\bsucesor(?:a)?\s+(?:de\s+la\s+)?(?:nintendo\s+)?switch\b", re.I), 5),
    (re.compile(r"\bswitch\s+successor\b|\bsuccessor\s+to\s+(?:the\s+)?(?:nintendo\s+)?switch\b", re.I), 5),
    (re.compile(r"\bnext(?:-generation|\s+generation)?\s+nintendo\s+console\b", re.I), 5),
    (re.compile(r"\bnext\s+nintendo\s+system\b|\bnew\s+nintendo\s+console\b", re.I), 4),
)
HIGH_VALUE_TERMS = re.compile(
    r"\b(?:nintendo\s+direct|official|oficial|confirmed|confirmad[oa]|announc|anunci|release date|fecha de lanzamiento|precio|price|launch|lanzamiento|specs?|especificaciones|sales|ventas|update|actualizaci[oó]n)\b",
    re.I,
)
LOW_VALUE_TERMS = re.compile(r"\b(?:giveaway|sorteo|quiz|wallpaper|fondo de pantalla|newsletter|podcast)\b", re.I)
RUMOR_TERMS = re.compile(
    r"\b(?:rumou?r|rumor|leak(?:ed)?|filtraci[oó]n|filtrad[oa]|reportedly|allegedly|supuestamente|podr[ií]a|insider|unconfirmed|sin confirmar)\b",
    re.I,
)
CONFIRMED_TERMS = re.compile(
    r"\b(?:official(?:ly)?|oficial(?:mente)?|confirmed|confirmad[oa]|announced|anunciad[oa]|Nintendo (?:says|confirma|anuncia)|press release|comunicado)\b",
    re.I,
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class Story:
    id: str
    title: str
    link: str
    summary: str
    published: str
    source: str
    language: str = "es"
    image_url: str = ""
    relevance_score: int = 0
    status: str = "noticia"
    original_title: str = ""


def create_database(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sent_articles (id TEXT PRIMARY KEY, title TEXT, link TEXT, "
        "published TEXT, source TEXT, created_at INTEGER)"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sent_articles)")}
    migrations = {
        "summary": "TEXT", "language": "TEXT", "image_url": "TEXT",
        "relevance_score": "INTEGER DEFAULT 0", "status": "TEXT", "original_title": "TEXT",
    }
    for column, definition in migrations.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE sent_articles ADD COLUMN {column} {definition}")
    conn.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    return conn


def clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def relevance_score(story: Story) -> int:
    title = clean_text(story.title)
    combined = f"{title} {clean_text(story.summary)}"
    score = 0
    for pattern, weight in SWITCH_2_TERMS:
        if pattern.search(title):
            score = max(score, weight + 2)
        elif pattern.search(combined):
            score = max(score, weight)
    if score and HIGH_VALUE_TERMS.search(combined):
        score += 1
    if LOW_VALUE_TERMS.search(combined):
        score -= 2
    return max(0, min(score, 10))


def classify_story(story: Story) -> str:
    text = f"{story.title} {story.summary}"
    if RUMOR_TERMS.search(text):
        return "rumor"
    if CONFIRMED_TERMS.search(text):
        return "confirmado"
    return "noticia"


def extract_image(entry) -> str:
    for field in ("media_content", "media_thumbnail"):
        for media in entry.get(field, []):
            if media.get("url"):
                return media["url"]
    for enclosure in entry.get("enclosures", []):
        if enclosure.get("type", "").startswith("image/") and enclosure.get("href"):
            return enclosure["href"]
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', entry.get("summary", ""), re.I)
    return match.group(1) if match else ""


def fetch_feed(config: FeedConfig) -> List[Story]:
    logger.info("Leyendo feed: %s", config.url)
    feed = feedparser.parse(config.url, request_headers={"User-Agent": "SwitchNewsTelegramBot/2.0"})
    if feed.bozo:
        logger.warning("Feed con errores: %s - %s", config.url, getattr(feed, "bozo_exception", "desconocido"))
    stories = []
    for entry in feed.entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id:
            continue
        stories.append(Story(
            id=str(entry_id),
            title=clean_text(entry.get("title", "Sin título")),
            link=entry.get("link", ""),
            summary=clean_text(entry.get("summary", entry.get("description", ""))),
            published=entry.get("published", entry.get("updated", "")),
            source=config.name or clean_text(feed.feed.get("title", "")) or urlparse(config.url).netloc,
            language=config.language,
            image_url=extract_image(entry),
        ))
    return stories


def prepare_stories(stories: Iterable[Story]) -> List[Story]:
    prepared = []
    for story in stories:
        score = relevance_score(story)
        if score >= MIN_RELEVANCE_SCORE:
            prepared.append(replace(story, relevance_score=score, status=classify_story(story)))
    return prepared


def deduplicate_stories(stories: Iterable[Story]) -> List[Story]:
    result: List[Story] = []
    seen: Set[str] = set()
    for story in stories:
        key = (story.link or story.id).strip().lower().rstrip("/")
        if key and key not in seen:
            seen.add(key)
            result.append(story)
    return result


def translate_story(story: Story) -> Story:
    if not TRANSLATION_ENABLED or story.language.lower().startswith("es"):
        return story
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source=story.language.split("-")[0], target="es")
        title = translator.translate(story.title)
        summary = translator.translate(story.summary[:1000]) if story.summary else ""
        return replace(story, title=title or story.title, summary=summary or story.summary, original_title=story.title)
    except Exception as exc:
        logger.warning("No se pudo traducir '%s': %s", story.title, exc)
        return story


def get_unsent_stories(conn: sqlite3.Connection, stories: Iterable[Story]) -> List[Story]:
    return [
        story for story in stories
        if conn.execute("SELECT 1 FROM sent_articles WHERE id = ?", (story.id,)).fetchone() is None
    ]


def mark_as_sent(conn: sqlite3.Connection, story: Story) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sent_articles "
        "(id, title, link, published, source, created_at, summary, language, image_url, relevance_score, status, original_title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (story.id, story.title, story.link, story.published, story.source, int(time.time()), story.summary,
         story.language, story.image_url, story.relevance_score, story.status, story.original_title),
    )
    conn.commit()


def build_message(story: Story) -> str:
    labels = {
        "confirmado": "🟢 <b>Confirmado</b>",
        "rumor": "🟡 <b>Rumor</b>",
        "noticia": "🔵 <b>Noticia</b>",
    }
    parts = [labels[story.status], f"<b>{html.escape(story.title)}</b>"]
    if story.summary:
        summary = story.summary[:350].rstrip() + ("…" if len(story.summary) > 350 else "")
        parts.extend(["", html.escape(summary)])
    metadata = html.escape(story.source)
    if story.published:
        metadata += f" · {html.escape(story.published)}"
    parts.extend(["", f"📰 {metadata}", f"⭐ Relevancia: {story.relevance_score}/10"])
    if story.link:
        parts.extend(["", f'<a href="{html.escape(story.link, quote=True)}">Leer noticia completa →</a>'])
    return "\n".join(parts)[:1024 if story.image_url else 4096]


def parse_telegram_error(response: requests.Response) -> str:
    try:
        return response.json().get("description") or str(response.json())
    except ValueError:
        return response.text


def telegram_request(token: str, method: str, payload: dict) -> bool:
    try:
        response = requests.post(f"https://api.telegram.org/bot{token.strip()}/{method}", data=payload, timeout=20)
    except requests.RequestException as exc:
        logger.error("Error conectando con Telegram: %s", exc)
        return False
    try:
        ok = response.status_code == 200 and response.json().get("ok")
    except ValueError:
        ok = False
    if not ok:
        logger.error("Error de Telegram: %s %s", response.status_code, parse_telegram_error(response))
        return False
    return True


def validate_telegram_config(token: str, chat_id: str) -> bool:
    if not TELEGRAM_TOKEN_PATTERN.match(token.strip()) or not chat_id.strip():
        logger.error("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no tienen el formato esperado.")
        return False
    try:
        response = requests.get(f"https://api.telegram.org/bot{token.strip()}/getMe", timeout=15)
        valid = response.status_code == 200 and response.json().get("ok")
    except (requests.RequestException, ValueError) as exc:
        logger.error("No se pudo validar el bot: %s", exc)
        return False
    if not valid:
        logger.error("Telegram rechazó las credenciales: %s", parse_telegram_error(response))
    return valid


def send_telegram_story(token: str, chat_id: str, story: Story) -> bool:
    message = build_message(story)
    common = {"chat_id": chat_id.strip(), "parse_mode": "HTML"}
    if story.image_url:
        if telegram_request(token, "sendPhoto", {**common, "photo": story.image_url, "caption": message}):
            return True
        logger.warning("La imagen falló; se reintentará como mensaje de texto.")
    return telegram_request(token, "sendMessage", {
        **common, "text": message, "disable_web_page_preview": "false",
    })


def local_now() -> datetime:
    try:
        timezone = ZoneInfo(CHANNEL_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.error("Zona horaria desconocida '%s'; se usará UTC.", CHANNEL_TIMEZONE)
        timezone = ZoneInfo("UTC")
    return datetime.now(timezone)


def promo_is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    if now.hour < PROMO_HOUR:
        return False
    last_date = conn.execute("SELECT value FROM bot_state WHERE key = 'last_promo_date'").fetchone()
    return last_date is None or last_date[0] != now.date().isoformat()


def mark_promo_as_sent(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES ('last_promo_date', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (now.date().isoformat(),),
    )
    conn.commit()


def send_daily_promo(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    if not promo_is_due(conn, now):
        return
    if dry_run:
        logger.info("Dry-run del mensaje promocional de las %02d:00:\n%s", PROMO_HOUR, PROMO_TEXT)
        return
    payload = {
        "chat_id": TELEGRAM_CHAT_ID.strip(),
        "text": PROMO_TEXT,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", payload):
        mark_promo_as_sent(conn, now)
        logger.info("Mensaje promocional diario enviado.")
    else:
        logger.warning("No se pudo enviar el mensaje promocional diario; se reintentará en otra ejecución.")


def run(dry_run: bool = False) -> int:
    if not dry_run and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        logger.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.")
        return 1
    if not dry_run and not validate_telegram_config(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
        return 1

    conn = create_database(str(Path(DATABASE_PATH)))
    all_stories: List[Story] = []
    for config in RSS_FEEDS:
        try:
            all_stories.extend(fetch_feed(config))
        except Exception as exc:
            logger.exception("Error leyendo el feed %s: %s", config.url, exc)

    stories = deduplicate_stories(prepare_stories(all_stories))
    stories.sort(key=lambda story: story.published or "", reverse=True)
    unsent = get_unsent_stories(conn, stories)[:MAX_ARTICLES_PER_RUN]
    logger.info("Encontradas %d noticias relevantes, %d nuevas para procesar", len(stories), len(unsent))

    for original_story in unsent:
        story = translate_story(original_story)
        if dry_run:
            logger.info("Dry-run (no se guarda ni se envía):\n%s", build_message(story))
            continue
        if send_telegram_story(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, story):
            mark_as_sent(conn, story)
            time.sleep(1)
        else:
            logger.warning("No se pudo enviar: %s", story.link)
    if not unsent:
        logger.info("No hay noticias nuevas para enviar.")
    send_daily_promo(conn, dry_run=dry_run)
    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agregador de noticias Nintendo Switch 2 para Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Muestra noticias sin enviarlas ni marcarlas como vistas")
    raise SystemExit(run(dry_run=parser.parse_args().dry_run))
