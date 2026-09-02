# Bot de noticias Nintendo Switch 2

Agregador de noticias para Telegram con fuentes españolas e internacionales, traducción al español, puntuación de relevancia, imágenes y clasificación editorial automática.

## Funciones

- RSS en español e inglés: Nintendo Life, IGN, Eurogamer, The Verge, Nintenderos, Vandal y más.
- Idioma configurado individualmente para cada fuente.
- Traducción al español de títulos y descripciones de fuentes internacionales.
- Detección de `Switch 2`, `Switch successor`, `next Nintendo console` y expresiones equivalentes.
- Puntuación de relevancia de 0 a 10 y umbral configurable.
- Etiquetas `🟢 Confirmado`, `🟡 Rumor` y `🔵 Noticia` basadas en el texto del artículo.
- Publicación con imagen RSS mediante `sendPhoto`, con reintento automático como texto si la imagen falla.
- Mensajes HTML seguros, descripción breve, fuente, fecha y enlace.
- Migración automática de bases SQLite creadas por versiones anteriores.
- Límite de publicaciones por ejecución para evitar ráfagas en el canal.
- Mensaje promocional diario al final de la ejecución de las 21:00, sin duplicados.
- Descarte de artículos con más de 48 horas y orden cronológico normalizado.
- Detección de coberturas duplicadas aunque procedan de medios y URL diferentes.
- Botones para abrir y compartir cada noticia directamente desde Telegram.

Las etiquetas son una clasificación heurística: `Confirmado` indica que el texto contiene señales explícitas de confirmación oficial; no sustituye una comprobación editorial.

## Configuración

Crea un archivo `.env` en la raíz (no debe subirse al repositorio):

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
DATABASE_PATH=seen.db

# Opcionales
TRANSLATION_ENABLED=true
MIN_RELEVANCE_SCORE=4
MAX_ARTICLES_PER_RUN=10
MAX_ARTICLE_AGE_HOURS=48
DUPLICATE_SIMILARITY=0.76
CHANNEL_TIMEZONE=Europe/Madrid
PROMO_HOUR=21
PROMO_TEXT=¿Tienes un amigo que vive pendiente de Switch 2? Envíale este canal y que no se pierda la próxima gran noticia.
```

`TRANSLATION_ENABLED=false` conserva el texto original. Si el servicio de traducción no está disponible, el bot publica el original y continúa procesando el resto.

El mensaje promocional se envía una sola vez al día al final de la primera ejecución a partir de `PROMO_HOUR` en `CHANNEL_TIMEZONE`. La fecha del último envío queda guardada en SQLite, por lo que un reintento de GitHub Actions no lo duplica. `PROMO_TEXT` admite el HTML compatible con Telegram.

## Instalación y uso

```bash
python3 -m pip install -r requirements.txt
python3 switch_news_bot.py --dry-run
python3 switch_news_bot.py
```

El modo `--dry-run` no necesita credenciales, no envía publicaciones y tampoco marca artículos como vistos.

## Fuentes e idiomas

Las fuentes se definen como objetos `FeedConfig` al inicio de `switch_news_bot.py`:

```python
FeedConfig("https://www.nintenderos.com/feed/", "es", "Nintenderos")
FeedConfig("https://www.nintendolife.com/feeds/latest", "en", "Nintendo Life")
```

Para añadir una fuente, indica su URL, código de idioma y nombre. Los idiomas distintos de `es` se traducen cuando la traducción está activada.

## Filtrado y relevancia

Una mención directa a Switch 2 en el título recibe más puntuación que una mención en la descripción. Los términos de anuncio oficial, lanzamiento, precio, especificaciones, ventas o actualizaciones suman relevancia; contenido tipo sorteo, newsletter o podcast resta puntuación. Solo se procesan noticias que alcancen `MIN_RELEVANCE_SCORE`.

## Base de datos

La tabla `sent_articles` conserva título y resumen finales, título original, idioma, imagen, puntuación, clasificación, fuente y fecha. Al abrir una base antigua, las columnas nuevas se añaden automáticamente sin borrar los artículos ya registrados.

## GitHub Actions

El workflow `.github/workflows/switch-news.yml` ejecuta el bot cada hora. Configura en `Settings` → `Secrets and variables` → `Actions`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

La base `seen.db` se restaura mediante la caché del workflow para evitar publicaciones repetidas.

Si Telegram devuelve un error 404, revisa que el token no lleve el prefijo `bot`, no contenga espacios y coincida exactamente con el entregado por BotFather.
