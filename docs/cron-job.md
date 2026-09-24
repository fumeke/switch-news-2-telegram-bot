# Programación con cron-job.org

El bot sigue ejecutándose en GitHub Actions. cron-job.org solicita la ejecución a las horas configuradas, sin depender del disparador `schedule` de GitHub. La disponibilidad del ejecutor, la instalación de dependencias y las consultas a fuentes todavía pueden retrasar la publicación.

## 1. Preparar GitHub

Sube los cambios de `.github/workflows/switch-news.yml` a `main` antes de probar el nuevo parámetro `dry_run`. Conserva los secretos de Telegram en GitHub.

En [los tokens de GitHub](https://github.com/settings/personal-access-tokens/new), crea un token **fine-grained**:

- Nombre: `cron-job-switch-news`.
- Propietario: `fumeke`.
- Acceso únicamente al repositorio `switch-news-2-telegram-bot`.
- Permiso de repositorio **Actions: Read and write**. Metadata de lectura se incluye automáticamente.
- Elige una caducidad y anota cuándo renovarlo.

Este token se introduce únicamente en la cabecera Authorization de las dos tareas de cron-job.org. No lo incluyas en la URL, el repositorio, capturas o mensajes. cron-job.org no necesita el token del bot de Telegram.

## 2. Crear dos tareas desactivadas

En [cron-job.org](https://console.cron-job.org/), crea estas dos tareas con la zona horaria **Europe/Madrid**:

| Título | Expresión cron | Horas |
| --- | --- | --- |
| Switch News — horas en punto | `0 9,12,15,18,21 * * *` | 09:00, 12:00, 15:00, 18:00 y 21:00 |
| Switch News — 20:30 | `30 20 * * *` | 20:30 |

Selecciona **Personalizado**. Si el formulario muestra selectores en vez de permitir pegar la expresión, elige minuto `0` y las cinco horas para la primera tarea; minuto `30` y hora `20` para la segunda. En ambos casos, todos los días, meses y días de la semana. Revisa «Próximas ejecuciones» y deja **Habilitar job** apagado hasta completar la prueba.

Las dos tareas comparten esta URL:

```text
https://api.github.com/repos/fumeke/switch-news-2-telegram-bot/actions/workflows/switch-news.yml/dispatches
```

En **Avanzado**, configura el método **POST** y estas cabeceras:

| Nombre | Valor |
| --- | --- |
| Authorization | `Bearer TU_TOKEN_DE_GITHUB` |
| Accept | `application/vnd.github+json` |
| Content-Type | `application/json` |
| X-GitHub-Api-Version | `2026-03-10` |

Reemplaza `TU_TOKEN_DE_GITHUB` por el token creado en el paso anterior, conservando `Bearer ` y su espacio. En el cuerpo de la petición, usa inicialmente:

```json
{"ref":"main","inputs":{"dry_run":true}}
```

Activa las notificaciones de fallo y desactivación de las tareas. Esos avisos detectan problemas al solicitar la ejecución; el resultado del bot se comprueba en GitHub Actions.

## 3. Probar y activar

1. Usa la opción de prueba de cron-job.org en cada tarea. Una respuesta HTTP exitosa solo confirma que GitHub aceptó la solicitud. Con la versión de API indicada se espera `200` con información de la ejecución.
2. Abre [Actions del repositorio](https://github.com/fumeke/switch-news-2-telegram-bot/actions/workflows/switch-news.yml). Comprueba que la ejecución de prueba termina correctamente. `dry_run=true` no envía mensajes y no guarda cambios en la caché de `seen.db`; según la hora y el historial puede limitarse al resumen matinal, por lo que no valida todas las fuentes ni las credenciales de Telegram.
3. Cambia el cuerpo de **ambas** tareas al siguiente, para las ejecuciones reales:

   ```json
   {"ref":"main","inputs":{"dry_run":false}}
   ```

4. En GitHub → Settings → Secrets and variables → Actions → **Variables**, crea la variable de repositorio `CRON_JOB_ENABLED` con valor `true`. Espera a que termine cualquier publicación que ya estuviera en curso.
5. Activa las dos tareas de cron-job.org y revisa sus próximas ejecuciones. Haz el cambio lejos de una hora de envío. Comprueba la primera ejecución programada tanto en cron-job.org como en Actions.

No uses «Disable workflow»: se necesita el workflow activo para recibir las solicitudes externas. La variable hace que los eventos `schedule` se omitan; no los elimina del historial de Actions. `concurrency` evita ejecuciones simultáneas, pero no deduplica solicitudes repetidas ni garantiza conservar todas las solicitudes pendientes.

## Recuperación

- Si cron-job.org no funciona, desactiva sus dos tareas y cambia `CRON_JOB_ENABLED` a `false` (o elimina la variable) para volver a la programación nativa.
- `401`: comprueba el token y su caducidad.
- `403` o `404`: comprueba el propietario, el repositorio, los permisos del token y que Actions esté habilitado.
- `422`: revisa la rama `main`, el cuerpo JSON y que el workflow publicado incluya `dry_run`.
- Renueva el token antes de que caduque y actualiza la cabecera en ambas tareas.

Referencias: [activar workflows por API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) y [peticiones personalizadas de cron-job.org](https://cron-job.org/en/faq/).
