import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import switch_news_bot as bot


def story(title, summary="", language="es"):
    return bot.Story("id", title, "https://example.com/?a=1&b=2", summary, "hoy", "Fuente & Co", language)


def test_relevance_prioritizes_title_and_rejects_noise():
    direct = story("Nintendo confirma una actualización de Switch 2")
    indirect = story("Nuevo hardware", "Nintendo Switch 2 tendrá una actualización")
    noise = story("Nintendo podcast y sorteo semanal")
    assert bot.relevance_score(direct) > bot.relevance_score(indirect)
    assert bot.relevance_score(noise) == 0


def test_status_prefers_rumor_over_confirmation_words():
    item = story("Rumor: Nintendo anunciaría oficialmente Switch 2")
    assert bot.classify_story(item) == "rumor"


def test_message_uses_safe_html():
    item = story("Switch 2 <Pro> & novedades", "Precio > 400")
    item.relevance_score = 8
    message = bot.build_message(item)
    assert "&lt;Pro&gt; &amp; novedades" in message
    assert "a=1&amp;b=2" in message
    assert "Fuente &amp; Co" in message


def test_database_migrates_old_schema(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE sent_articles (id TEXT PRIMARY KEY, title TEXT, link TEXT, "
        "published TEXT, source TEXT, created_at INTEGER)"
    )
    old.close()
    conn = bot.create_database(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sent_articles)")}
    assert {"summary", "language", "image_url", "relevance_score", "status", "original_title"} <= columns


def test_daily_promo_is_only_due_once_at_configured_hour(tmp_path):
    conn = bot.create_database(str(tmp_path / "state.db"))
    at_nine = datetime(2026, 9, 2, 21, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    before_nine = datetime(2026, 9, 2, 20, 59, tzinfo=ZoneInfo("Europe/Madrid"))
    assert not bot.promo_is_due(conn, before_nine)
    assert bot.promo_is_due(conn, at_nine)
    assert bot.promo_is_due(conn, datetime(2026, 9, 2, 22, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    bot.mark_promo_as_sent(conn, at_nine)
    assert not bot.promo_is_due(conn, at_nine)
    assert bot.promo_is_due(conn, datetime(2026, 9, 3, 21, 0, tzinfo=ZoneInfo("Europe/Madrid")))
