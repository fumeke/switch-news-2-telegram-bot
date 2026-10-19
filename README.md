# Bot de noticias Nintendo Switch 2

Agregador de noticias para Telegram con fuentes españolas e internacionales, traducción al español, puntuación de relevancia, imágenes y clasificación editorial automática.

## Funciones

- RSS en español e inglés: Nintendo Life, IGN, Eurogamer, The Verge, Nintenderos, Vandal y más.
- Idioma configurado individualmente para cada fuente.
- Traducción al español de títulos y descripciones de fuentes internacionales.
- Segundo proveedor de traducción de respaldo, tiempos máximos de espera y caché para reducir fallos y peticiones repetidas.
- Aviso visible antes del enlace cuando la página original está en inglés, incluso si fue traducida.
- Detección de `Switch 2`, `Switch successor`, `next Nintendo console` y expresiones equivalentes.
- Puntuación de relevancia de 0 a 10 y umbral configurable.
- Etiquetas `🟢 Confirmado`, `🟡 Rumor` y `🔵 Noticia` basadas en el texto del artículo.
- Publicación con imagen RSS mediante `sendPhoto`, con reintento automático como texto si la imagen falla.
- Mensajes HTML seguros, descripción breve, fuente, fecha y enlace.
- Migración automática de bases SQLite creadas por versiones anteriores.
- Límite de publicaciones por ejecución para evitar ráfagas en el canal.
- Mensaje promocional diario al final de la ejecución de las 21:00, sin duplicados.
- Promoción semanal de Switch eShop Saver los miércoles a las 20:30, con botones para iOS y Android.
- Descarte de artículos con más de 48 horas y orden cronológico normalizado.
- Detección de coberturas duplicadas aunque procedan de medios y URL diferentes.
- Botones para abrir y compartir cada noticia directamente desde Telegram.
- Enlace de lectura únicamente como botón, sin repetirlo dentro del mensaje.
- Prioridad editorial según la fiabilidad de cada fuente.
- Resúmenes limpios de un máximo de dos frases.
- Especial automático que agrupa los anuncios de un Nintendo Direct.
- Valoraciones `🔥 Interesante`, `👍 Útil` y `👎 Poco relevante` almacenadas en SQLite.
- Rotación de mensajes promocionales para evitar repeticiones.
- Alertas privadas cuando un feed falla reiteradamente y aviso cuando se recupera.
- Resumen semanal con las cinco noticias más relevantes.
- Resumen matinal como primera publicación del día, a las 08:00, con las tres noticias más importantes del día anterior.
- Calendario fijado con lanzamientos confirmados de los próximos 60 días.
- Informe privado dominical con las estadísticas principales del canal.
- Pruebas automáticas en cada push y pull request de GitHub.

Las etiquetas son una clasificación heurística: `Confirmado` indica que el texto contiene señales explícitas de confirmación oficial; no sustituye una comprobación editorial.

## Configuración

Crea un archivo `.env` en la raíz (no debe subirse al repositorio):

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_CHANNEL_URL=https://t.me/nombre_del_canal
DATABASE_PATH=seen.db
ADMIN_CHAT_ID=123456789

# Opcionales
TRANSLATION_ENABLED=true
MIN_RELEVANCE_SCORE=4
MAX_ARTICLES_PER_RUN=10
MAX_ARTICLE_AGE_HOURS=48
DUPLICATE_SIMILARITY=0.76
CHANNEL_TIMEZONE=Europe/Madrid
PROMO_HOUR=21
PROMO_TEXT=¿Tienes un amigo que vive pendiente de Switch 2? Envíale este canal y que no se pierda la próxima gran noticia.
APP_PROMO_WEEKDAY=2
APP_PROMO_HOUR=20
APP_PROMO_MINUTE=30
WEEKLY_DIGEST_HOUR=20
MORNING_DIGEST_HOUR=8
CALENDAR_DAYS_AHEAD=60
CALENDAR_AVAILABLE_DAYS=30
CALENDAR_REMINDER_HOUR=10
ADMIN_STATS_HOUR=22
```

`TRANSLATION_ENABLED=false` conserva el texto original. Si el servicio de traducción no está disponible, el bot publica el original y continúa procesando el resto.

Las traducciones de titular y resumen se realizan por separado: si una falla, se conserva la otra. Para noticias inglesas, el mensaje siempre avisa antes del enlace de que la página de destino está en inglés. Los resultados se guardan durante siete días en `translation_cache`.

El mensaje promocional se envía una sola vez al día al final de la primera ejecución a partir de `PROMO_HOUR` en `CHANNEL_TIMEZONE` e incluye un botón que abre directamente la acción de compartir el canal. El bot obtiene el enlace a partir del nombre público del canal; `TELEGRAM_CHANNEL_URL` permite indicarlo explícitamente y es necesario si Telegram no devuelve un nombre público o un enlace de invitación. Si no se define `PROMO_TEXT`, el bot rota automáticamente entre cuatro mensajes. La fecha del último envío queda guardada en SQLite, por lo que un reintento de GitHub Actions no lo duplica. `PROMO_TEXT` admite el HTML compatible con Telegram.

La promoción de Switch eShop Saver se publica como mensaje independiente una vez por semana, al final de la ejecución del miércoles a las 20:30, con botones directos a App Store y Google Play. El día usa la numeración de Python (`0` es lunes y `6` domingo) y puede cambiarse con `APP_PROMO_WEEKDAY`; la hora y los minutos se controlan con `APP_PROMO_HOUR` y `APP_PROMO_MINUTE`. La semana del último envío se guarda en SQLite para evitar duplicados.

Los domingos, a partir de `WEEKLY_DIGEST_HOUR`, se publica un resumen con las cinco noticias de mayor relevancia de los últimos siete días.

A partir de `MORNING_DIGEST_HOUR`, el bot dedica la primera ejecución exclusivamente a publicar una sola vez las tres noticias con mayor relevancia del día anterior y cierra con “¡A ver qué nos trae hoy Nintendo Switch 2!”. La selección descarta titulares relacionados con un tema ya elegido para ofrecer tres noticias distintas siempre que haya suficientes candidatos. Antes de esa hora no publica noticias, por lo que el resumen siempre abre la jornada; las noticias nuevas se procesan en la siguiente ejecución. Los enlaces aparecen únicamente en botones.

## Calendario de lanzamientos

El bot detecta fechas completas asociadas explícitamente a un lanzamiento de Switch 2, siempre que la noticia no sea un rumor y la fuente tenga fiabilidad suficiente. Conserva los datos en la tabla `releases`, muestra los próximos `CALENDAR_DAYS_AHEAD` días y mantiene los lanzamientos recientes durante `CALENDAR_AVAILABLE_DAYS` días.

El calendario es un único mensaje sin botones, con una línea `fecha — juego` por lanzamiento. Los títulos de la sección «Ya disponibles» enlazan a una búsqueda de Amazon España con el tag de afiliado `desarrolloapl-21`; los juegos futuros no llevan enlace. Las noticias sobre el mismo juego y fecha se agrupan para evitar duplicados. El mensaje se edita solamente cuando cambia el contenido. Al crearlo, el bot intenta fijarlo en el canal; para ello necesita permiso de administrador para fijar mensajes. Cada lunes, a partir de `CALENDAR_REMINDER_HOUR`, publica un recordatorio breve para consultar el calendario fijado. Las expresiones imprecisas como “próximamente” o “este otoño” no se incorporan.

## Estadísticas del administrador

Con `ADMIN_CHAT_ID` configurado, también recibirás un mensaje privado con el nombre y el usuario (si dispone de él) de cada nuevo suscriptor. El bot debe ser administrador del canal para recibir los eventos `chat_member`. Los avisos se procesan en cada ejecución, incluso fuera del horario de publicación; no se envían en `--dry-run`. Se avisa también si alguien sale y vuelve a entrar, pero no por bajas, cambios de permisos ni incorporaciones de bots. Si el envío falla, se reintenta en la siguiente ejecución. Telegram conserva los eventos pendientes durante un máximo de 24 horas; no se notifican incorporaciones anteriores a la activación de esta función.

Cada domingo, a partir de `ADMIN_STATS_HOUR`, el bot envía a `ADMIN_CHAT_ID` un informe privado con suscriptores y variación semanal, publicaciones, confirmaciones y rumores, relevancia media, traducciones, valoraciones, fuentes más utilizadas, noticia principal, próximos lanzamientos y salud de los feeds. Si Telegram no permite consultar el número de miembros, el resto del informe se envía igualmente. SQLite registra la semana para evitar duplicados.

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
FeedConfig("https://www.nintenderos.com/feed/", "es", "Nintenderos", 2)
FeedConfig("https://www.nintendolife.com/feeds/latest", "en", "Nintendo Life", 3)
```

Para añadir una fuente, indica su URL, código de idioma, nombre y fiabilidad de 1 a 3. Los idiomas distintos de `es` se traducen cuando la traducción está activada. Las fuentes de nivel 3 reciben un punto adicional de relevancia; los agregadores de nivel 1 reciben uno menos.

## Filtrado y relevancia

Una mención directa a Switch 2 en el título recibe más puntuación que una mención en la descripción. Los términos de anuncio oficial, lanzamiento, precio, especificaciones, ventas o actualizaciones suman relevancia; contenido tipo sorteo, newsletter o podcast resta puntuación. Solo se procesan noticias que alcancen `MIN_RELEVANCE_SCORE`.

Cuando coinciden tres o más noticias relacionadas con un Nintendo Direct, el bot publica un especial único con enlaces a todos los anuncios en lugar de inundar el canal.

## Valoraciones y supervisión

Los botones de valoración usan callbacks de Telegram. El bot recoge las respuestas pendientes al inicio de cada ejecución y conserva un voto por usuario y noticia. La confirmación puede demorarse hasta la siguiente ejecución programada; para respuesta inmediata sería necesario ejecutar el bot como servicio permanente con webhook.

Si se configura `ADMIN_CHAT_ID`, el administrador recibe un aviso privado después de tres fallos consecutivos de una fuente, otro cuando se recupera y alertas por publicaciones rechazadas. Añádelo también como `Repository secret` en GitHub.

## Base de datos

La tabla `sent_articles` conserva título y resumen finales, título original, idioma, imagen, puntuación, clasificación, fiabilidad, fuentes relacionadas y fecha. `article_feedback` almacena las valoraciones y `bot_state` controla promociones, resúmenes, alertas y actualizaciones de Telegram. Al abrir una base antigua, las columnas nuevas se añaden automáticamente sin borrar los artículos ya registrados.

## GitHub Actions

El workflow `.github/workflows/switch-news.yml` permite ejecutar el bot desde cron-job.org mediante `workflow_dispatch`, o manualmente desde Actions. Consulta la [guía de configuración de cron-job.org](docs/cron-job.md). Los horarios son 09:00, 12:00, 15:00, 18:00, 20:30 y 21:00 en `Europe/Madrid`; la primera ejecución se dedica al resumen matinal y la última incluye el mensaje para compartir el canal. Configura en `Settings` → `Secrets and variables` → `Actions`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_CHANNEL_URL` (opcional)
- `ADMIN_CHAT_ID` (opcional)

La programación nativa de GitHub sigue activa durante la configuración. Una vez comprobado cron-job.org, establece la variable de repositorio `CRON_JOB_ENABLED=true` para omitir los envíos disparados por `schedule`. No deshabilites el workflow completo: también impediría las activaciones externas. El programador externo evita depender del disparador horario de GitHub, pero el arranque sigue sujeto a la disponibilidad de sus ejecutores.

La base `seen.db` se restaura mediante la caché del workflow para evitar publicaciones repetidas. Las ejecuciones se serializan para que no publiquen simultáneamente. El modo `dry_run` permite comprobar la integración sin enviar mensajes ni guardar cambios en la caché de la base. La caché sigue siendo almacenamiento temporal, por lo que no sustituye una copia de seguridad permanente.

El workflow configura `ADMIN_STATS_HOUR=21` para mantener el informe privado de los domingos en la última ejecución del día.

El workflow `tests.yml` ejecuta las pruebas y valida la sintaxis en cada cambio enviado a `main` y en cada pull request.

Si Telegram devuelve un error 404, revisa que el token no lleve el prefijo `bot`, no contenga espacios y coincida exactamente con el entregado por BotFather.
