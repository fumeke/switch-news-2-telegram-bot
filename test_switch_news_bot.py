import sqlite3
import json
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
    assert {
        "summary", "language", "image_url", "relevance_score", "status", "original_title",
        "published_timestamp", "other_sources", "reliability", "feedback_key",
    } <= columns
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"sent_articles", "bot_state", "article_feedback"} <= tables


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


def test_old_articles_are_filtered_but_unknown_dates_are_kept(monkeypatch):
    monkeypatch.setattr(bot, "MAX_ARTICLE_AGE_HOURS", 48)
    recent = story("Noticia reciente de Switch 2")
    recent.published_timestamp = 900_000
    old = story("Noticia antigua de Switch 2")
    old.published_timestamp = 700_000
    unknown = story("Noticia sin fecha de Switch 2")
    assert bot.filter_fresh_stories([recent, old, unknown], now_timestamp=1_000_000) == [recent, unknown]


def test_similar_coverage_is_merged_and_sources_are_preserved():
    first = story("Nintendo anuncia la nueva actualización de Switch 2")
    first.source = "Medio A"
    first.relevance_score = 8
    second = story("Nueva actualización de Nintendo Switch 2 anunciada")
    second.source = "Medio B"
    second.relevance_score = 7
    merged = bot.smart_deduplicate_stories([first, second])
    assert len(merged) == 1
    assert merged[0].source == "Medio A"
    assert merged[0].other_sources == "Medio B"


def test_telegram_story_includes_read_and_share_buttons(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "telegram_request", lambda token, method, payload: calls.append(payload) or True)
    item = story("Nueva actualización de Switch 2")
    assert bot.send_telegram_story("token", "chat", item)
    keyboard = json.loads(calls[0]["reply_markup"])["inline_keyboard"][0]
    assert keyboard[0] == {"text": "📰 Leer noticia", "url": item.link}
    assert keyboard[1]["text"] == "🔗 Compartir"
    assert keyboard[1]["url"].startswith("https://t.me/share/url?")
    feedback_buttons = json.loads(calls[0]["reply_markup"])["inline_keyboard"][1]
    assert [button["text"] for button in feedback_buttons] == ["🔥 Interesante", "👍 Útil", "👎 Poco relevante"]


def test_concise_summary_uses_at_most_two_sentences():
    result = bot.concise_summary("Primera frase. Segunda frase importante. Tercera frase que sobra.")
    assert result == "Primera frase. Segunda frase importante."


def test_summary_includes_later_switch_2_context():
    result = bot.concise_summary(
        "Capcom presentará sus novedades. El evento será en Tokio. Uno de los juegos llegará a Nintendo Switch 2."
    )
    assert result == "Capcom presentará sus novedades. Uno de los juegos llegará a Nintendo Switch 2."


def test_reliable_source_gets_higher_relevance():
    regular = story("Nintendo Switch 2 recibe una actualización")
    trusted = story("Nintendo Switch 2 recibe una actualización")
    trusted.reliability = 3
    aggregator = story("Nintendo Switch 2 recibe una actualización")
    aggregator.reliability = 1
    assert bot.relevance_score(trusted) > bot.relevance_score(regular) > bot.relevance_score(aggregator)


def test_low_reliability_source_cannot_mark_story_as_confirmed():
    item = story("Oficial: Nintendo confirma Switch 2")
    item.reliability = 1
    assert bot.classify_story(item) == "noticia"


def test_direct_digest_groups_links():
    stories = [story(f"Nintendo Direct: anuncio {index}") for index in range(3)]
    for index, item in enumerate(stories):
        item.link = f"https://example.com/{index}"
    digest = bot.build_direct_digest(stories)
    assert "3 anuncios destacados" in digest
    assert all(item.link in digest for item in stories)


def test_promotional_messages_rotate(monkeypatch):
    monkeypatch.setattr(bot, "CUSTOM_PROMO_TEXT", "")
    first = bot.daily_promo_text(datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Madrid")))
    second = bot.daily_promo_text(datetime(2026, 9, 2, tzinfo=ZoneInfo("Europe/Madrid")))
    assert first != second


def test_weekly_digest_only_runs_once_on_sunday(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "WEEKLY_DIGEST_HOUR", 20)
    conn = bot.create_database(str(tmp_path / "weekly.db"))
    sunday = datetime(2026, 9, 6, 20, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    assert bot.weekly_digest_is_due(conn, sunday)
    iso = sunday.isocalendar()
    bot.set_state(conn, "last_weekly_digest", f"{iso.year}-W{iso.week:02d}")
    assert not bot.weekly_digest_is_due(conn, sunday)


def test_reader_feedback_adjusts_source_score(tmp_path):
    conn = bot.create_database(str(tmp_path / "feedback.db"))
    published = story("Nintendo Switch 2 recibe novedades")
    published.source = "Fuente favorita"
    bot.mark_as_sent(conn, published)
    key = bot.feedback_key(published)
    for user_id in ("1", "2", "3"):
        conn.execute(
            "INSERT INTO article_feedback (article_key, user_id, rating, created_at) VALUES (?, ?, 'hot', ?)",
            (key, user_id, int(bot.time.time())),
        )
    conn.commit()
    candidate = story("Nintendo Switch 2 estrena un juego")
    candidate.source = "Fuente favorita"
    candidate.relevance_score = 7
    assert bot.apply_feedback_adjustments(conn, [candidate])[0].relevance_score == 8
