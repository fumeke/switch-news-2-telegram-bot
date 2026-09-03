import argparse
import calendar
import hashlib
import html
import json
import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from os import environ
from pathlib import Path
from typing import Iterable, List, Set
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_URL = environ.get("TELEGRAM_CHANNEL_URL", "")
DATABASE_PATH = environ.get("DATABASE_PATH", "seen.db")
TRANSLATION_ENABLED = environ.get("TRANSLATION_ENABLED", "true").lower() in {"1", "true", "yes", "si"}
MIN_RELEVANCE_SCORE = int(environ.get("MIN_RELEVANCE_SCORE", "4"))
MAX_ARTICLES_PER_RUN = int(environ.get("MAX_ARTICLES_PER_RUN", "10"))
MAX_ARTICLE_AGE_HOURS = int(environ.get("MAX_ARTICLE_AGE_HOURS", "48"))
DUPLICATE_SIMILARITY = float(environ.get("DUPLICATE_SIMILARITY", "0.76"))
CHANNEL_TIMEZONE = environ.get("CHANNEL_TIMEZONE", "Europe/Madrid")
ADMIN_CHAT_ID = environ.get("ADMIN_CHAT_ID", "")
PROMO_HOUR = int(environ.get("PROMO_HOUR", "21"))
MORNING_DIGEST_HOUR = int(environ.get("MORNING_DIGEST_HOUR", "8"))
WEEKLY_DIGEST_HOUR = int(environ.get("WEEKLY_DIGEST_HOUR", "20"))
CALENDAR_DAYS_AHEAD = int(environ.get("CALENDAR_DAYS_AHEAD", "60"))
CALENDAR_REMINDER_HOUR = int(environ.get("CALENDAR_REMINDER_HOUR", "10"))
ADMIN_STATS_HOUR = int(environ.get("ADMIN_STATS_HOUR", "22"))
CUSTOM_PROMO_TEXT = environ.get("PROMO_TEXT", "")
PROMO_MESSAGES = (
    "🎮 <b>La actualidad de Nintendo se disfruta más en compañía.</b>\n\n"
    "¿Tienes un amigo que vive pendiente de Switch 2? "
    "Envíale este canal y que no se pierda la próxima gran noticia. 🚀",
    "🚀 <b>Que la próxima noticia de Switch 2 no te pille solo.</b>\n\n"
    "Comparte el canal con ese amigo que siempre quiere enterarse el primero.",
    "🍄 <b>Una buena partida se comparte. Las buenas noticias, también.</b>\n\n"
    "Invita a tus amigos al canal y vivid juntos cada anuncio de Nintendo Switch 2.",
    "🔔 <b>¿Conoces a otro fan de Nintendo?</b>\n\n"
    "Envíale este canal y ayúdale a no perderse anuncios, lanzamientos y sorpresas.",
)
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{35,}$")


@dataclass(frozen=True)
class FeedConfig:
    url: str
    language: str = "es"
    name: str = ""
    reliability: int = 2


RSS_FEEDS = [
    FeedConfig(
        "https://news.google.com/rss/search?q=site%3Anintendo.com+%22Nintendo+Switch+2%22&hl=en-US&gl=US&ceid=US:en",
        "en", "Nintendo (oficial)", 3,
    ),
    FeedConfig("https://www.nintenderos.com/feed/", "es", "Nintenderos"),
    FeedConfig("https://www.hobbyconsolas.com/feed", "es", "Hobby Consolas"),
    FeedConfig("https://www.vidaextra.com/feed", "es", "VidaExtra"),
    FeedConfig("https://www.meristation.com/feed", "es", "MeriStation"),
    FeedConfig("https://news.google.com/rss/search?q=Nintendo+Switch+2+lang:es&hl=es&gl=ES&ceid=ES:es", "es", "Google News", 1),
    FeedConfig("https://vandal.elespanol.com/xml.cgi?type=noticias&format=feed", "es", "Vandal"),
    FeedConfig("https://www.nextn.es/feed/", "es", "NextN"),
    FeedConfig("https://www.eurogamer.es/feed/news", "es", "Eurogamer.es"),
    FeedConfig("https://www.nintendolife.com/feeds/latest", "en", "Nintendo Life", 3),
    FeedConfig("https://feeds.ign.com/ign/games-all", "en", "IGN", 3),
    FeedConfig("https://www.eurogamer.net/feed", "en", "Eurogamer", 3),
    FeedConfig("https://www.theverge.com/rss/index.xml", "en", "The Verge", 3),
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
DIRECT_TERMS = re.compile(r"\bnintendo\s+direct\b|\bdirect\s+de\s+nintendo\b", re.I)
RELEASE_TERMS = re.compile(
    r"\b(?:release date|launch(?:es|ing)?|releases?|fecha de lanzamiento|se lanza|llega(?:rá)?|sale a la venta)\b",
    re.I,
)
MONTHS = {
    "january": 1, "enero": 1, "february": 2, "febrero": 2, "march": 3, "marzo": 3,
    "april": 4, "abril": 4, "may": 5, "mayo": 5, "june": 6, "junio": 6,
    "july": 7, "julio": 7, "august": 8, "agosto": 8, "september": 9, "septiembre": 9,
    "october": 10, "octubre": 10, "november": 11, "noviembre": 11, "december": 12, "diciembre": 12,
}
SPANISH_MONTH_NAMES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}
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
    published_timestamp: int = 0
    other_sources: str = ""
    reliability: int = 2
    translation_status: str = "not_needed"


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
        "published_timestamp": "INTEGER DEFAULT 0", "other_sources": "TEXT",
        "reliability": "INTEGER DEFAULT 2", "feedback_key": "TEXT",
        "translation_status": "TEXT DEFAULT 'not_needed'",
    }
    for column, definition in migrations.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE sent_articles ADD COLUMN {column} {definition}")
    conn.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS article_feedback (article_key TEXT, user_id TEXT, rating TEXT, created_at INTEGER, "
        "PRIMARY KEY (article_key, user_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS translation_cache (story_id TEXT PRIMARY KEY, title TEXT, summary TEXT, "
        "status TEXT, created_at INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS releases (release_key TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "release_date TEXT NOT NULL, source TEXT, link TEXT, updated_at INTEGER)"
    )
    conn.commit()
    return conn


def clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def entry_timestamp(entry) -> int:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(parsed) if parsed else 0


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
    if score and story.reliability >= 3:
        score += 1
    elif score and story.reliability <= 1:
        score -= 1
    return max(0, min(score, 10))


def classify_story(story: Story) -> str:
    text = f"{story.title} {story.summary}"
    if RUMOR_TERMS.search(text):
        return "rumor"
    if CONFIRMED_TERMS.search(text) and story.reliability >= 2:
        return "confirmado"
    return "noticia"


def concise_summary(text: str, max_length: int = 340) -> str:
    text = clean_text(text)
    text = re.sub(r"^(?:read more|leer más|continue reading|seguir leyendo)[:\s-]*", "", text, flags=re.I)
    sentences = [sentence for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence]
    chosen = sentences[:2]
    focus_sentence = next(
        (sentence for sentence in sentences if any(pattern.search(sentence) for pattern, _ in SWITCH_2_TERMS)),
        None,
    )
    if focus_sentence and focus_sentence not in chosen:
        chosen = [sentences[0], focus_sentence]
    selected = " ".join(chosen).strip()
    if not selected:
        return ""
    if len(selected) <= max_length:
        return selected
    shortened = selected[:max_length].rsplit(" ", 1)[0].rstrip(" ,;:")
    return shortened + "…"


def parse_release_date(text: str):
    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    patterns = (
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+de)?\s+({months})\s+(?:de\s+)?(20\d{{2}})\b",
        rf"\b({months})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(20\d{{2}})\b",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        if index == 0:
            day, month_name, year = match.groups()
        else:
            month_name, day, year = match.groups()
        try:
            return datetime(int(year), MONTHS[month_name.lower()], int(day)).date()
        except ValueError:
            return None
    return None


def release_name_from_title(title: str) -> str:
    cleaned = re.sub(r"^(?:confirmed|confirmado|oficial|noticia)[:\s-]+", "", clean_text(title), flags=re.I)
    review_match = re.search(
        r"\breviews?\s+for\s+(.+?)\s+on\s+(?:nintendo\s+)?switch\b", cleaned, re.I
    )
    if review_match:
        return review_match.group(1).strip(" :-–—")[:160]
    review_title = re.match(r"^(?:review|análisis|preview)[:\s-]+(.+?)(?:\s+\(|\s+-|$)", cleaned, re.I)
    if review_title:
        return review_title.group(1).strip(" :-–—")[:160]
    prefix = re.split(
        r"\b(?:launches|releases|arrives|release date|llega(?:rá)?|se lanza|sale a la venta|fecha de lanzamiento)\b",
        cleaned,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" :-–—")
    return (prefix if len(prefix) >= 3 else cleaned)[:160]


def extract_release(story: Story, today=None):
    if story.status == "rumor" or story.reliability < 2:
        return None
    complete_text = f"{story.title} {story.summary}"
    if not any(pattern.search(complete_text) for pattern, _ in SWITCH_2_TERMS):
        return None
    segments = [story.title, *re.split(r"(?<=[.!?])\s+", story.summary)]
    release_date = next(
        (parse_release_date(segment) for segment in segments if RELEASE_TERMS.search(segment) and parse_release_date(segment)),
        None,
    )
    if not release_date:
        return None
    today = today or local_now().date()
    if release_date < today or release_date > today + timedelta(days=365):
        return None
    name = release_name_from_title(story.title)
    key = hashlib.sha256(title_key(name).encode()).hexdigest()[:20]
    return key, name, release_date.isoformat(), story.source, story.link


def update_releases(conn: sqlite3.Connection, stories: Iterable[Story], today=None) -> bool:
    changed = False
    for story in stories:
        release = extract_release(story, today=today)
        if not release:
            continue
        key, name, release_date, source, link = release
        existing = conn.execute(
            "SELECT name, release_date, source, link FROM releases WHERE release_key = ?", (key,)
        ).fetchone()
        values = (name, release_date, source, link)
        if existing != values:
            conn.execute(
                "INSERT INTO releases (release_key, name, release_date, source, link, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(release_key) DO UPDATE SET name = excluded.name, "
                "release_date = excluded.release_date, source = excluded.source, link = excluded.link, "
                "updated_at = excluded.updated_at",
                (key, name, release_date, source, link, int(time.time())),
            )
            changed = True
    if changed:
        conn.commit()
    return changed


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
            published_timestamp=entry_timestamp(entry),
            reliability=config.reliability,
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


def title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(title).lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"\b(?:nintendo|switch|2|the|a|an|el|la|los|las|de|del|en|un|una|para|por|and|y)\b", " ", normalized)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", normalized).split())


def titles_are_similar(first: str, second: str) -> bool:
    first_key, second_key = title_key(first), title_key(second)
    if not first_key or not second_key:
        return False
    sequence_score = SequenceMatcher(None, first_key, second_key).ratio()
    first_words = {word[:6] if len(word) > 6 else word for word in first_key.split()}
    second_words = {word[:6] if len(word) > 6 else word for word in second_key.split()}
    token_score = len(first_words & second_words) / max(1, len(first_words | second_words))
    return sequence_score >= DUPLICATE_SIMILARITY or token_score >= 0.67


def digest_titles_are_related(first: str, second: str) -> bool:
    """Use a broader topic match when a digest needs editorial variety."""
    if titles_are_similar(first, second):
        return True
    first_words = title_key(first).split()
    second_words = title_key(second).split()
    first_phrases = {tuple(first_words[index:index + 3]) for index in range(len(first_words) - 2)}
    second_phrases = {tuple(second_words[index:index + 3]) for index in range(len(second_words) - 2)}
    return bool(first_phrases & second_phrases)


def select_diverse_digest_rows(rows: Iterable[tuple], limit: int = 3) -> list:
    selected = []
    for row in rows:
        if any(digest_titles_are_related(row[0], existing[0]) for existing in selected):
            continue
        selected.append(row)
        if len(selected) == limit:
            break
    return selected


def smart_deduplicate_stories(stories: Iterable[Story]) -> List[Story]:
    unique: List[Story] = []
    for story in stories:
        duplicate_index = next(
            (index for index, existing in enumerate(unique) if titles_are_similar(story.title, existing.title)),
            None,
        )
        if duplicate_index is None:
            unique.append(story)
            continue
        existing = unique[duplicate_index]
        sources = [source for source in (existing.other_sources.split(", ") if existing.other_sources else []) if source]
        if story.source != existing.source and story.source not in sources:
            sources.append(story.source)
        replacement = existing
        if story.relevance_score > existing.relevance_score or (story.image_url and not existing.image_url):
            sources = [source for source in [existing.source, *sources] if source != story.source]
            replacement = story
        unique[duplicate_index] = replace(replacement, other_sources=", ".join(sources))
    return unique


def filter_fresh_stories(stories: Iterable[Story], now_timestamp: int = None) -> List[Story]:
    now_timestamp = now_timestamp or int(datetime.now(timezone.utc).timestamp())
    cutoff = now_timestamp - MAX_ARTICLE_AGE_HOURS * 3600
    return [story for story in stories if not story.published_timestamp or story.published_timestamp >= cutoff]


def remove_recently_sent_duplicates(conn: sqlite3.Connection, stories: Iterable[Story]) -> List[Story]:
    cutoff = int(time.time()) - MAX_ARTICLE_AGE_HOURS * 3600
    recent_titles = [row[0] for row in conn.execute(
        "SELECT title FROM sent_articles WHERE created_at >= ? AND title IS NOT NULL", (cutoff,)
    )]
    return [story for story in stories if not any(titles_are_similar(story.title, title) for title in recent_titles)]


def source_feedback_adjustments(conn: sqlite3.Connection) -> dict:
    cutoff = int(time.time()) - 30 * 86400
    rows = conn.execute(
        "SELECT sent_articles.source, COUNT(*), "
        "SUM(CASE article_feedback.rating WHEN 'hot' THEN 1.0 WHEN 'useful' THEN 0.5 ELSE -1.0 END) "
        "FROM article_feedback JOIN sent_articles ON sent_articles.feedback_key = article_feedback.article_key "
        "WHERE article_feedback.created_at >= ? GROUP BY sent_articles.source",
        (cutoff,),
    ).fetchall()
    adjustments = {}
    for source, votes, total in rows:
        if votes >= 3:
            average = total / votes
            adjustments[source] = 1 if average >= 0.35 else (-1 if average <= -0.35 else 0)
    return adjustments


def apply_feedback_adjustments(conn: sqlite3.Connection, stories: Iterable[Story]) -> List[Story]:
    adjustments = source_feedback_adjustments(conn)
    adjusted = [
        replace(story, relevance_score=max(0, min(10, story.relevance_score + adjustments.get(story.source, 0))))
        for story in stories
    ]
    return [story for story in adjusted if story.relevance_score >= MIN_RELEVANCE_SCORE]


def translate_text(text: str, source_language: str) -> str:
    if not text:
        return ""
    protected_terms = []

    def protect(match):
        protected_terms.append(match.group(0))
        return f"ZXQTERM{len(protected_terms) - 1}QXZ"

    protected_text = re.sub(r"\b(?:Nintendo\s+)?Switch\s*2\b|\bNintendo\s+Direct\b", protect, text, flags=re.I)

    def restore(value: str) -> str:
        for index, term in enumerate(protected_terms):
            value = re.sub(f"ZXQTERM{index}QXZ", term, value, flags=re.I)
        return value

    errors = []
    source_code = source_language.split("-")[0]
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source_code, "tl": "es", "dt": "t", "q": protected_text},
            timeout=12,
        )
        response.raise_for_status()
        translated = "".join(segment[0] for segment in response.json()[0] if segment and segment[0])
        if translated:
            return restore(translated)
        raise ValueError("respuesta vacía")
    except Exception as exc:
        errors.append(f"Google: {exc}")
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": protected_text[:450], "langpair": f"{source_code}|es"},
            timeout=12,
        )
        response.raise_for_status()
        translated = response.json().get("responseData", {}).get("translatedText", "")
        if translated:
            return restore(translated)
        raise ValueError("respuesta vacía")
    except Exception as exc:
        errors.append(f"MyMemory: {exc}")
    raise RuntimeError("; ".join(errors))


def translate_story(story: Story, conn: sqlite3.Connection = None) -> Story:
    if not TRANSLATION_ENABLED or story.language.lower().startswith("es"):
        return replace(story, summary=concise_summary(story.summary))
    if conn is not None:
        cached = conn.execute(
            "SELECT title, summary, status FROM translation_cache WHERE story_id = ? AND created_at >= ?",
            (story.id, int(time.time()) - 7 * 86400),
        ).fetchone()
        if cached:
            return replace(
                story, title=cached[0], summary=cached[1], original_title=story.title,
                translation_status=cached[2],
            )
    title, summary = "", ""
    failures = []
    try:
        title = translate_text(story.title, story.language)
    except Exception as exc:
        failures.append(f"titular: {exc}")
    if story.summary:
        try:
            summary = translate_text(story.summary[:1000], story.language)
        except Exception as exc:
            failures.append(f"resumen: {exc}")
    title_ok = bool(title)
    summary_ok = not story.summary or bool(summary)
    status = "complete" if title_ok and summary_ok else ("partial" if title_ok or summary_ok else "failed")
    result = replace(
        story,
        title=title or story.title,
        summary=concise_summary(summary or story.summary),
        original_title=story.title,
        translation_status=status,
    )
    if failures:
        logger.warning("Traducción %s para '%s': %s", status, story.title, "; ".join(failures))
    if conn is not None and status in {"complete", "partial"}:
        conn.execute(
            "INSERT INTO translation_cache (story_id, title, summary, status, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(story_id) DO UPDATE SET title = excluded.title, summary = excluded.summary, "
            "status = excluded.status, created_at = excluded.created_at",
            (story.id, result.title, result.summary, status, int(time.time())),
        )
        conn.commit()
    return result


def feedback_key(story: Story) -> str:
    return hashlib.sha256(story.id.encode("utf-8")).hexdigest()[:16]


def get_unsent_stories(conn: sqlite3.Connection, stories: Iterable[Story]) -> List[Story]:
    return [
        story for story in stories
        if conn.execute("SELECT 1 FROM sent_articles WHERE id = ?", (story.id,)).fetchone() is None
    ]


def mark_as_sent(conn: sqlite3.Connection, story: Story) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sent_articles "
        "(id, title, link, published, source, created_at, summary, language, image_url, relevance_score, status, "
        "original_title, published_timestamp, other_sources, reliability, feedback_key, translation_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (story.id, story.title, story.link, story.published, story.source, int(time.time()), story.summary,
         story.language, story.image_url, story.relevance_score, story.status, story.original_title,
         story.published_timestamp, story.other_sources, story.reliability, feedback_key(story), story.translation_status),
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
        parts.extend(["", html.escape(concise_summary(story.summary))])
    metadata = html.escape(story.source)
    if story.other_sources:
        metadata += f" · También: {html.escape(story.other_sources)}"
    if story.published:
        metadata += f" · {html.escape(story.published)}"
    parts.extend(["", f"📰 {metadata}", f"⭐ Relevancia: {story.relevance_score}/10"])
    if story.link:
        if story.language.lower().startswith("en"):
            translation_notes = {
                "complete": "🇬🇧 <i>Titular y resumen traducidos automáticamente. La noticia original está en inglés.</i>",
                "partial": "🇬🇧 <i>Traducción automática parcial. La noticia original está en inglés.</i>",
                "failed": "🇬🇧 <i>Noticia en inglés. La traducción no está disponible temporalmente.</i>",
                "not_needed": "🇬🇧 <i>La noticia original está en inglés.</i>",
            }
            parts.extend(["", translation_notes.get(story.translation_status, translation_notes["not_needed"])])
    return "\n".join(parts)[:1024 if story.image_url else 4096]


def parse_telegram_error(response: requests.Response) -> str:
    try:
        return response.json().get("description") or str(response.json())
    except ValueError:
        return response.text


def telegram_call(token: str, method: str, payload: dict):
    try:
        response = requests.post(f"https://api.telegram.org/bot{token.strip()}/{method}", data=payload, timeout=20)
    except requests.RequestException as exc:
        logger.error("Error conectando con Telegram: %s", exc)
        return None
    try:
        ok = response.status_code == 200 and response.json().get("ok")
    except ValueError:
        ok = False
    if not ok:
        logger.error("Error de Telegram: %s %s", response.status_code, parse_telegram_error(response))
        return None
    return response.json()


def telegram_request(token: str, method: str, payload: dict) -> bool:
    return telegram_call(token, method, payload) is not None


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
    if story.link:
        share_url = "https://t.me/share/url?url=" + quote(story.link, safe="") + "&text=" + quote(
            f"{story.title}\n\nSigue las noticias de Nintendo Switch 2 en este canal.", safe=""
        )
        keyboard = {"inline_keyboard": [[
            {"text": "📰 Leer noticia", "url": story.link},
            {"text": "🔗 Compartir", "url": share_url},
        ], [
            {"text": "🔥 Interesante", "callback_data": f"rate:hot:{feedback_key(story)}"},
            {"text": "👍 Útil", "callback_data": f"rate:useful:{feedback_key(story)}"},
            {"text": "👎 Poco relevante", "callback_data": f"rate:low:{feedback_key(story)}"},
        ]]}
        common["reply_markup"] = json.dumps(keyboard)
    if story.image_url:
        if telegram_request(token, "sendPhoto", {**common, "photo": story.image_url, "caption": message}):
            return True
        logger.warning("La imagen falló; se reintentará como mensaje de texto.")
    return telegram_request(token, "sendMessage", {
        **common, "text": message, "disable_web_page_preview": "false",
    })


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def process_pending_feedback(conn: sqlite3.Connection) -> None:
    offset = int(get_state(conn, "telegram_update_offset", "0"))
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["callback_query"])},
            timeout=15,
        )
        data = response.json()
        if response.status_code != 200 or not data.get("ok"):
            logger.warning("No se pudieron recoger valoraciones: %s", parse_telegram_error(response))
            return
    except (requests.RequestException, ValueError) as exc:
        logger.warning("No se pudieron recoger valoraciones: %s", exc)
        return
    latest_offset = offset
    for update in data.get("result", []):
        latest_offset = max(latest_offset, int(update["update_id"]) + 1)
        callback = update.get("callback_query", {})
        match = re.fullmatch(r"rate:(hot|useful|low):([a-f0-9]{16})", callback.get("data", ""))
        if not match:
            continue
        rating, article_key = match.groups()
        user_id = str(callback.get("from", {}).get("id", ""))
        if user_id:
            conn.execute(
                "INSERT INTO article_feedback (article_key, user_id, rating, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(article_key, user_id) DO UPDATE SET rating = excluded.rating, created_at = excluded.created_at",
                (article_key, user_id, rating, int(time.time())),
            )
        if callback.get("id"):
            telegram_request(TELEGRAM_BOT_TOKEN, "answerCallbackQuery", {
                "callback_query_id": callback["id"], "text": "¡Gracias por tu valoración!",
            })
    if latest_offset != offset:
        conn.commit()
        set_state(conn, "telegram_update_offset", str(latest_offset))


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


def daily_promo_text(now: datetime) -> str:
    if CUSTOM_PROMO_TEXT:
        return CUSTOM_PROMO_TEXT
    return PROMO_MESSAGES[now.toordinal() % len(PROMO_MESSAGES)]


def get_channel_share_url() -> str:
    configured_url = TELEGRAM_CHANNEL_URL.strip()
    if configured_url:
        return configured_url
    chat_id = TELEGRAM_CHAT_ID.strip()
    if chat_id.startswith("@"):
        return f"https://t.me/{chat_id[1:]}"
    response = telegram_call(TELEGRAM_BOT_TOKEN, "getChat", {"chat_id": chat_id})
    chat = response.get("result", {}) if response else {}
    if chat.get("username"):
        return f"https://t.me/{chat['username']}"
    return chat.get("invite_link", "")


def send_daily_promo(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    if not promo_is_due(conn, now):
        return
    promo_text = daily_promo_text(now)
    if dry_run:
        logger.info("Dry-run del mensaje promocional de las %02d:00:\n%s", PROMO_HOUR, promo_text)
        return
    payload = {
        "chat_id": TELEGRAM_CHAT_ID.strip(),
        "text": promo_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    channel_url = get_channel_share_url()
    if channel_url:
        share_url = "https://t.me/share/url?url=" + quote(channel_url, safe="") + "&text=" + quote(
            "Sigue las noticias de Nintendo Switch 2 en este canal.", safe=""
        )
        payload["reply_markup"] = json.dumps({"inline_keyboard": [[
            {"text": "📣 Compartir canal", "url": share_url},
        ]]})
    else:
        logger.warning(
            "No se pudo obtener el enlace del canal; configura TELEGRAM_CHANNEL_URL para mostrar el botón de compartir."
        )
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", payload):
        mark_promo_as_sent(conn, now)
        logger.info("Mensaje promocional diario enviado.")
    else:
        logger.warning("No se pudo enviar el mensaje promocional diario; se reintentará en otra ejecución.")


def notify_admin(text: str) -> None:
    if not ADMIN_CHAT_ID:
        return
    telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": ADMIN_CHAT_ID, "text": f"⚠️ <b>Switch News Bot</b>\n\n{html.escape(text)}", "parse_mode": "HTML",
    })


def record_feed_health(conn: sqlite3.Connection, config: FeedConfig, successful: bool) -> None:
    key_hash = hashlib.sha256(config.url.encode()).hexdigest()[:12]
    count_key, alert_key = f"feed_failures:{key_hash}", f"feed_alerted:{key_hash}"
    if successful:
        if int(get_state(conn, count_key, "0")) >= 3 and get_state(conn, alert_key) == "1":
            notify_admin(f"El feed {config.name} vuelve a funcionar.")
        set_state(conn, count_key, "0")
        set_state(conn, alert_key, "0")
        return
    failures = int(get_state(conn, count_key, "0")) + 1
    set_state(conn, count_key, str(failures))
    if failures >= 3 and get_state(conn, alert_key) != "1":
        notify_admin(f"El feed {config.name} ha fallado {failures} veces consecutivas: {config.url}")
        set_state(conn, alert_key, "1")


def record_translation_health(conn: sqlite3.Connection, attempted: bool, successful: bool) -> None:
    if not attempted:
        return
    failures = 0 if successful else int(get_state(conn, "translation_failures", "0")) + 1
    set_state(conn, "translation_failures", str(failures))
    if successful:
        if get_state(conn, "translation_alerted") == "1":
            notify_admin("El servicio de traducción automática vuelve a funcionar.")
        set_state(conn, "translation_alerted", "0")
    elif failures >= 3 and get_state(conn, "translation_alerted") != "1":
        notify_admin("La traducción automática ha fallado en tres ejecuciones consecutivas.")
        set_state(conn, "translation_alerted", "1")


def record_news_activity(conn: sqlite3.Connection, has_relevant_news: bool) -> None:
    now = int(time.time())
    if has_relevant_news:
        set_state(conn, "last_relevant_news_at", str(now))
        set_state(conn, "no_news_alerted", "0")
        return
    last_seen = int(get_state(conn, "last_relevant_news_at", str(now)))
    if not get_state(conn, "last_relevant_news_at"):
        set_state(conn, "last_relevant_news_at", str(now))
    if now - last_seen >= 48 * 3600 and get_state(conn, "no_news_alerted") != "1":
        notify_admin("No se ha encontrado ninguna noticia relevante durante las últimas 48 horas.")
        set_state(conn, "no_news_alerted", "1")


def is_direct_story(story: Story) -> bool:
    return bool(DIRECT_TERMS.search(f"{story.title} {story.summary}"))


def build_direct_digest(stories: List[Story]) -> str:
    parts = ["🎬 <b>Especial Nintendo Direct</b>", "", f"{len(stories)} anuncios destacados:"]
    for story in stories[:10]:
        parts.append(f'• <a href="{html.escape(story.link, quote=True)}">{html.escape(story.title)}</a>')
    sources = ", ".join(dict.fromkeys(story.source for story in stories))
    parts.extend(["", f"📰 Fuentes: {html.escape(sources)}"])
    return "\n".join(parts)[:4096]


def send_direct_digest(conn: sqlite3.Connection, stories: List[Story], dry_run: bool) -> bool:
    if len(stories) < 3:
        return False
    message = build_direct_digest(stories)
    if dry_run:
        logger.info("Dry-run del especial Nintendo Direct:\n%s", message)
        return True
    sent = telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if sent:
        for story in stories:
            mark_as_sent(conn, story)
    return sent


def weekly_digest_is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    return now.weekday() == 6 and now.hour >= WEEKLY_DIGEST_HOUR and get_state(conn, "last_weekly_digest") != week_key


def morning_digest_is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    return now.hour >= MORNING_DIGEST_HOUR and get_state(conn, "last_morning_digest") != now.date().isoformat()


def previous_day_bounds(now: datetime) -> tuple:
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    return int(yesterday_start.timestamp()), int(today_start.timestamp())


def build_morning_digest(rows: list) -> str:
    parts = ["☕ <b>Las 3 noticias más importantes de ayer</b>", ""]
    for index, (title, link, source, score) in enumerate(rows, 1):
        parts.append(f"{index}. <b>{html.escape(title)}</b> · {html.escape(source)}")
    parts.extend(["", "🎮 Comienza una nueva jornada. ¡A ver qué nos trae hoy Nintendo Switch 2!"])
    return "\n".join(parts)


def send_morning_digest(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    now = local_now()
    if not morning_digest_is_due(conn, now):
        return False
    start, end = previous_day_bounds(now)
    candidates = conn.execute(
        "SELECT title, link, source, relevance_score FROM sent_articles "
        "WHERE created_at >= ? AND created_at < ? AND link IS NOT NULL AND link != '' "
        "ORDER BY relevance_score DESC, created_at DESC LIMIT 25",
        (start, end),
    ).fetchall()
    rows = select_diverse_digest_rows(candidates)
    if not rows:
        if not dry_run:
            set_state(conn, "last_morning_digest", now.date().isoformat())
        logger.info("No hay noticias de ayer para incluir en el resumen matinal.")
        return True
    message = build_morning_digest(rows)
    keyboard = {"inline_keyboard": [[
        {"text": f"{index}️⃣ Leer noticia", "url": row[1]}
    ] for index, row in enumerate(rows, 1)]}
    if dry_run:
        logger.info("Dry-run del resumen de ayer:\n%s", message)
        return True
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true", "reply_markup": json.dumps(keyboard),
    }):
        set_state(conn, "last_morning_digest", now.date().isoformat())
    return True


def calendar_rows(conn: sqlite3.Connection, today=None) -> list:
    today = today or local_now().date()
    end = today + timedelta(days=CALENDAR_DAYS_AHEAD)
    return conn.execute(
        "SELECT name, release_date, source, link FROM releases WHERE release_date >= ? AND release_date <= ? "
        "ORDER BY release_date, name",
        (today.isoformat(), end.isoformat()),
    ).fetchall()


def build_release_calendar(rows: list, now: datetime) -> str:
    parts = ["🗓 <b>Próximos lanzamientos para Nintendo Switch 2</b>"]
    current_month = None
    for name, release_date, source, link in rows:
        parsed = datetime.strptime(release_date, "%Y-%m-%d").date()
        month_key = (parsed.year, parsed.month)
        if month_key != current_month:
            parts.extend(["", f"<b>{SPANISH_MONTH_NAMES[parsed.month].capitalize()} {parsed.year}</b>"])
            current_month = month_key
        parts.append(f"• {parsed.day} de {SPANISH_MONTH_NAMES[parsed.month]} — {html.escape(name)} · {html.escape(source)}")
    parts.extend([
        "", "✅ Solo fechas confirmadas",
        f"🔄 Actualizado: {now.strftime('%d/%m/%Y %H:%M')}",
    ])
    return "\n".join(parts)[:4096]


def calendar_keyboard(rows: list) -> dict:
    return {"inline_keyboard": [[
        {"text": f"🎮 {name[:45]}", "url": link}
    ] for name, release_date, source, link in rows[:10] if link]}


def sync_release_calendar(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    rows = calendar_rows(conn, today=now.date())
    if not rows:
        return
    keyboard = calendar_keyboard(rows)
    content_hash = hashlib.sha256(json.dumps([rows, keyboard], sort_keys=True).encode()).hexdigest()
    if get_state(conn, "calendar_content_hash") == content_hash:
        return
    message = build_release_calendar(rows, now)
    if dry_run:
        logger.info("Dry-run del calendario de lanzamientos:\n%s", message)
        return
    message_id = get_state(conn, "calendar_message_id")
    common = {
        "chat_id": TELEGRAM_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true", "reply_markup": json.dumps(keyboard),
    }
    if message_id:
        result = telegram_call(TELEGRAM_BOT_TOKEN, "editMessageText", {**common, "message_id": message_id})
        if result is None:
            failures = int(get_state(conn, "calendar_edit_failures", "0")) + 1
            set_state(conn, "calendar_edit_failures", str(failures))
            if failures >= 3:
                set_state(conn, "calendar_message_id", "")
                notify_admin("No se pudo editar el calendario tras tres intentos; se recreará en la próxima ejecución.")
            return
    else:
        result = telegram_call(TELEGRAM_BOT_TOKEN, "sendMessage", {**common, "disable_notification": "true"})
        if result is None:
            return
        message_id = str(result.get("result", {}).get("message_id", ""))
        if not message_id:
            return
        set_state(conn, "calendar_message_id", message_id)
        if not telegram_request(TELEGRAM_BOT_TOKEN, "pinChatMessage", {
            "chat_id": TELEGRAM_CHAT_ID.strip(), "message_id": message_id, "disable_notification": "true",
        }):
            notify_admin("El calendario se creó, pero no pudo fijarse. Revisa el permiso para fijar mensajes.")
    set_state(conn, "calendar_edit_failures", "0")
    set_state(conn, "calendar_content_hash", content_hash)


def calendar_reminder_is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    iso = now.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    return (
        now.weekday() == 0 and now.hour >= CALENDAR_REMINDER_HOUR
        and bool(get_state(conn, "calendar_message_id"))
        and get_state(conn, "last_calendar_reminder") != week_key
    )


def send_calendar_reminder(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    if not calendar_reminder_is_due(conn, now):
        return
    message = "🗓 <b>Calendario actualizado</b>\n\nConsulta en el mensaje fijado los próximos lanzamientos confirmados para Nintendo Switch 2."
    if dry_run:
        logger.info("Dry-run del recordatorio del calendario:\n%s", message)
        return
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }):
        iso = now.isocalendar()
        set_state(conn, "last_calendar_reminder", f"{iso.year}-W{iso.week:02d}")


def send_weekly_digest(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    if not weekly_digest_is_due(conn, now):
        return
    rows = conn.execute(
        "SELECT title, link, source, relevance_score FROM sent_articles WHERE created_at >= ? "
        "ORDER BY relevance_score DESC, created_at DESC LIMIT 5",
        (int(time.time()) - 7 * 86400,),
    ).fetchall()
    if not rows:
        return
    parts = ["📊 <b>Lo más importante de la semana en Switch 2</b>", ""]
    for index, (title, link, source, score) in enumerate(rows, 1):
        parts.append(f'{index}. <a href="{html.escape(link, quote=True)}">{html.escape(title)}</a> · {html.escape(source)}')
    message = "\n".join(parts)
    if dry_run:
        logger.info("Dry-run del resumen semanal:\n%s", message)
        return
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }):
        iso = now.isocalendar()
        set_state(conn, "last_weekly_digest", f"{iso.year}-W{iso.week:02d}")


def admin_stats_is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    iso = now.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    return (
        bool(ADMIN_CHAT_ID) and now.weekday() == 6 and now.hour >= ADMIN_STATS_HOUR
        and get_state(conn, "last_admin_stats") != week_key
    )


def get_channel_member_count():
    result = telegram_call(TELEGRAM_BOT_TOKEN, "getChatMemberCount", {"chat_id": TELEGRAM_CHAT_ID.strip()})
    if result is None:
        return None
    count = result.get("result")
    return int(count) if isinstance(count, int) else None


def feed_health_summary(conn: sqlite3.Connection) -> tuple:
    failing = []
    for config in RSS_FEEDS:
        key_hash = hashlib.sha256(config.url.encode()).hexdigest()[:12]
        failures = int(get_state(conn, f"feed_failures:{key_hash}", "0"))
        if failures:
            failing.append((config.name, failures))
    return len(RSS_FEEDS) - len(failing), failing


def build_admin_weekly_stats(conn: sqlite3.Connection, now: datetime, member_count=None) -> str:
    cutoff = int(now.timestamp()) - 7 * 86400
    total, average, confirmed, rumors, english, complete, partial, failed = conn.execute(
        "SELECT COUNT(*), COALESCE(AVG(relevance_score), 0), "
        "SUM(CASE WHEN status = 'confirmado' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'rumor' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN language LIKE 'en%' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN translation_status = 'complete' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN translation_status = 'partial' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN translation_status = 'failed' THEN 1 ELSE 0 END) "
        "FROM sent_articles WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    sources = conn.execute(
        "SELECT source, COUNT(*) FROM sent_articles WHERE created_at >= ? GROUP BY source "
        "ORDER BY COUNT(*) DESC, source LIMIT 3",
        (cutoff,),
    ).fetchall()
    feedback = dict(conn.execute(
        "SELECT rating, COUNT(*) FROM article_feedback WHERE created_at >= ? GROUP BY rating", (cutoff,)
    ).fetchall())
    top_story = conn.execute(
        "SELECT title, source, relevance_score FROM sent_articles WHERE created_at >= ? "
        "ORDER BY relevance_score DESC, created_at DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM releases WHERE release_date >= ? AND release_date <= ?",
        (now.date().isoformat(), (now.date() + timedelta(days=CALENDAR_DAYS_AHEAD)).isoformat()),
    ).fetchone()[0]
    healthy_feeds, failing_feeds = feed_health_summary(conn)
    parts = [
        "📈 <b>Estadísticas semanales del canal</b>",
        f"<i>{(now.date() - timedelta(days=6)).strftime('%d/%m/%Y')} – {now.strftime('%d/%m/%Y')}</i>",
        "",
    ]
    if member_count is not None:
        previous = int(get_state(conn, "last_subscriber_count", str(member_count)))
        delta = member_count - previous
        delta_text = f"{delta:+d}" if delta else "sin cambios"
        parts.append(f"👥 Suscriptores: <b>{member_count}</b> ({delta_text})")
    parts.extend([
        f"📰 Noticias publicadas: <b>{total}</b>",
        f"🟢 Confirmadas: {confirmed or 0} · 🟡 Rumores: {rumors or 0}",
        f"⭐ Relevancia media: {float(average):.1f}/10",
        f"🇬🇧 Noticias inglesas: {english or 0} · Traducción ✅ {complete or 0} / ◐ {partial or 0} / ❌ {failed or 0}",
        f"💬 Valoraciones: 🔥 {feedback.get('hot', 0)} · 👍 {feedback.get('useful', 0)} · 👎 {feedback.get('low', 0)}",
        f"🗓 Próximos lanzamientos registrados: {upcoming}",
        f"📡 Feeds operativos: {healthy_feeds}/{len(RSS_FEEDS)}",
    ])
    if sources:
        parts.extend(["", "<b>Fuentes más publicadas</b>"])
        parts.extend(f"• {html.escape(source)}: {count}" for source, count in sources)
    if top_story:
        parts.extend([
            "", "<b>Noticia con mayor puntuación</b>",
            f"• {html.escape(top_story[0])} · {html.escape(top_story[1])} ({top_story[2]}/10)",
        ])
    if failing_feeds:
        parts.extend(["", "<b>Feeds con incidencias</b>"])
        parts.extend(f"• {html.escape(name)}: {failures} fallos consecutivos" for name, failures in failing_feeds)
    return "\n".join(parts)


def send_admin_weekly_stats(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    now = local_now()
    if not admin_stats_is_due(conn, now):
        return
    member_count = None if dry_run else get_channel_member_count()
    message = build_admin_weekly_stats(conn, now, member_count=member_count)
    if dry_run:
        logger.info("Dry-run de las estadísticas del administrador:\n%s", message)
        return
    if telegram_request(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": ADMIN_CHAT_ID.strip(), "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }):
        iso = now.isocalendar()
        set_state(conn, "last_admin_stats", f"{iso.year}-W{iso.week:02d}")
        if member_count is not None:
            set_state(conn, "last_subscriber_count", str(member_count))


def run(dry_run: bool = False) -> int:
    if not dry_run and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        logger.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.")
        return 1
    if not dry_run and not validate_telegram_config(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
        return 1

    conn = create_database(str(Path(DATABASE_PATH)))
    if not dry_run:
        process_pending_feedback(conn)

    now = local_now()
    if now.hour < MORNING_DIGEST_HOUR:
        logger.info(
            "No se publican noticias antes del resumen matinal de las %02d:00.",
            MORNING_DIGEST_HOUR,
        )
        conn.close()
        return 0
    if morning_digest_is_due(conn, now):
        send_morning_digest(conn, dry_run=dry_run)
        conn.close()
        return 0

    all_stories: List[Story] = []
    for config in RSS_FEEDS:
        try:
            feed_stories = fetch_feed(config)
            all_stories.extend(feed_stories)
            if not dry_run:
                record_feed_health(conn, config, bool(feed_stories))
        except Exception as exc:
            logger.exception("Error leyendo el feed %s: %s", config.url, exc)
            if not dry_run:
                record_feed_health(conn, config, False)

    stories = apply_feedback_adjustments(
        conn, filter_fresh_stories(deduplicate_stories(prepare_stories(all_stories)))
    )
    if not dry_run:
        record_news_activity(conn, bool(stories))
    stories.sort(key=lambda story: story.published_timestamp, reverse=True)
    release_stories = []
    for story in stories:
        if extract_release(story):
            release_stories.append(
                translate_story(story, conn) if not story.language.lower().startswith("es") else story
            )
    update_releases(conn, release_stories)
    candidates = smart_deduplicate_stories(get_unsent_stories(conn, stories))[:MAX_ARTICLES_PER_RUN * 2]
    translated = [translate_story(story, conn) for story in candidates]
    international = [story for story in candidates if not story.language.lower().startswith("es")]
    if not dry_run:
        record_translation_health(
            conn, bool(international),
            any(story.translation_status in {"complete", "partial"} for story in translated if not story.language.lower().startswith("es")),
        )
    unsent = smart_deduplicate_stories(remove_recently_sent_duplicates(conn, translated))[:MAX_ARTICLES_PER_RUN]
    logger.info("Encontradas %d noticias relevantes, %d nuevas para procesar", len(stories), len(unsent))

    direct_stories = [story for story in unsent if is_direct_story(story)]
    digest_sent = send_direct_digest(conn, direct_stories, dry_run) if len(direct_stories) >= 3 else False
    individual_stories = [story for story in unsent if not digest_sent or story not in direct_stories]

    for story in individual_stories:
        if dry_run:
            logger.info("Dry-run (no se guarda ni se envía):\n%s", build_message(story))
            continue
        if send_telegram_story(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, story):
            mark_as_sent(conn, story)
            time.sleep(1)
        else:
            logger.warning("No se pudo enviar: %s", story.link)
            notify_admin(f"No se pudo publicar la noticia: {story.title}\n{story.link}")
    if not unsent:
        logger.info("No hay noticias nuevas para enviar.")
    send_weekly_digest(conn, dry_run=dry_run)
    send_daily_promo(conn, dry_run=dry_run)
    send_calendar_reminder(conn, dry_run=dry_run)
    sync_release_calendar(conn, dry_run=dry_run)
    send_admin_weekly_stats(conn, dry_run=dry_run)
    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agregador de noticias Nintendo Switch 2 para Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Muestra noticias sin enviarlas ni marcarlas como vistas")
    raise SystemExit(run(dry_run=parser.parse_args().dry_run))
