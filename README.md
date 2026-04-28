# Bot de noticias Nintendo Switch 2

Bot de Telegram para agregar noticias de Nintendo Switch 2 en castellano usando RSS, filtro de palabras clave y almacenamiento de artículos ya enviados.

## Estructura

- `switch_news_bot.py`: script principal
- `requirements.txt`: dependencias de Python
- `.github/workflows/switch-news.yml`: tarea programada para GitHub Actions
- `.env`: configuración local (no se debe compartir)

## Configuración

1. Crea un bot con @BotFather en Telegram.
2. Obtén el `BOT_TOKEN` y tu `CHAT_ID`.
3. Crea un archivo `.env` en la raíz del proyecto con estos valores:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
DATABASE_PATH=seen.db
```

4. Instala dependencias:

```bash
python3 -m pip install -r requirements.txt
```

## Uso

Ejecuta el script manualmente:

```bash
python3 switch_news_bot.py
```

Para probar sin enviar mensajes, usa:

```bash
python3 switch_news_bot.py --dry-run
```

## Despliegue

Puedes ejecutarlo cada hora con GitHub Actions usando el workflow en `.github/workflows/switch-news.yml`.

Para activarlo en GitHub:

1. Sube el proyecto al repositorio, incluyendo `.github/workflows/switch-news.yml`.
2. En GitHub, entra en `Settings` -> `Secrets and variables` -> `Actions`.
3. Crea estos `Repository secrets`:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Entra en la pestaña `Actions` del repositorio y habilita workflows si GitHub te lo pide.
5. Ejecuta `Enviar noticias Switch 2 a Telegram` manualmente con `Run workflow` para probarlo.

Después de eso, GitHub Actions lo ejecutará cada hora. La base `seen.db` se restaura con cache entre ejecuciones para evitar reenviar noticias ya publicadas.

## Fuentes RSS incluidas

- `https://www.nintenderos.com/feed/`
- `https://www.hobbyconsolas.com/feed`
- `https://www.vidaextra.com/feed`
- `https://www.meristation.com/feed`
- `https://news.google.com/rss/search?q=Nintendo+Switch+2+lang:es&hl=es-419&gl=ES`

## Filtrado

El bot filtra por palabras clave relacionadas con Switch 2, Nintendo, lanzamientos y juegos exclusivos.

## Personalización

- Añade o quita palabras clave en la lista `KEYWORDS` dentro de `switch_news_bot.py`
- Añade nuevas fuentes RSS a la lista `RSS_FEEDS`
