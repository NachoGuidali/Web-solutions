# Deploy del CRM en el VPS

Guía para subir los cambios del CRM. Reemplazá las rutas y el nombre del
servicio por los tuyos (`supreg`, `gunicorn`, etc. son ejemplos).

---

## ⚠️ Antes que nada: la base de datos

`db.sqlite3` **está versionado en git**. Eso significa que un `git pull` puede
pisar la base de producción con la copia del repo y perder datos reales.

Hacé el backup **siempre**, antes de tocar nada:

```bash
cd /ruta/al/proyecto
cp db.sqlite3 ~/backups/db-$(date +%F-%H%M).sqlite3
ls -la ~/backups/ | tail -3
```

Cuando el deploy esté andando, conviene sacarla de git para que esto no vuelva
a pasar. **No lo hagas en el mismo deploy**: al commitear la baja, el próximo
`pull` borra el archivo del servidor. El orden seguro es:

1. Backup (arriba).
2. En tu máquina: `git rm --cached db.sqlite3 && echo "db.sqlite3" >> .gitignore`
3. Commit y push.
4. En el servidor: `cp ~/backups/db-<la-más-reciente>.sqlite3 db.sqlite3` **después** del pull.

---

## 1. Traer el código

```bash
cd /ruta/al/proyecto
git pull
```

Si git se queja por `db.sqlite3`, quedate con **la del servidor**:

```bash
git checkout --ours db.sqlite3     # en conflicto de merge
# o, si simplemente la pisó:
cp ~/backups/db-<la-más-reciente>.sqlite3 db.sqlite3
```

## 2. Dependencias

No hace falta instalar nada nuevo: el CRM anda sólo con Django.
Igual, por las dudas:

```bash
source venv/bin/activate          # o el venv que uses
pip install -r requirements.txt
```

Probado con **Django 4.2.6 y 5.0.6** — la suite pasa en las dos.

## 3. Migraciones

Son 3 nuevas (`0005`, `0006`, `0007`). Todas **aditivas**: agregan campos que
admiten null o traen default, y crean tablas nuevas. No borran ni transforman
nada de lo que ya tenés.

```bash
python manage.py migrate
```

## 4. Reiniciar

```bash
sudo systemctl restart gunicorn      # o el servicio que uses
sudo systemctl status gunicorn --no-pager
```

**No hace falta `collectstatic`**: el CRM nuevo no agrega ningún archivo
estático (Tailwind y Font Awesome van por CDN, y las imágenes que usa ya
estaban). Si hoy tus estáticos se ven bien, van a seguir viéndose bien.

## 5. Cargar las cotizaciones (una sola vez)

Las 406 cotizaciones históricas están en la base local, no en la del servidor.
En el VPS hay que traerlas:

```bash
python manage.py sincronizar_cotizaciones
python manage.py congelar_tipos_cambio --aplicar
```

El primero baja el histórico desde la factura más vieja hasta hoy. El segundo
le guarda a cada factura cobrada la cotización de su fecha, para que su
equivalente en dólares quede fijo.

## 6. Los trabajos automáticos

Hay cuatro: cotización diaria, avisos y facturación de mensualidades,
recordatorios de la agenda, y el resumen semanal de facturas vencidas.

### Opción A — cron (más simple, no necesita Redis)

Todo el CRM funciona sin Celery. Un solo comando corre lo que toque:

```bash
crontab -e
```

```cron
# CRM: cotización + mensualidades + resumen semanal de vencidas
30 8 * * *  cd /ruta/al/proyecto && /ruta/al/venv/bin/python manage.py crm_cron >> /var/log/crm_cron.log 2>&1

# Recordatorios de agenda, más seguido para que lleguen a tiempo
*/15 * * * * cd /ruta/al/proyecto && /ruta/al/venv/bin/python manage.py crm_cron --solo eventos >> /var/log/crm_cron.log 2>&1
```

Probalo antes sin ejecutar nada:

```bash
python manage.py crm_cron --dry-run
```

### Opción B — Celery + Redis (si ya lo tenés andando)

El schedule de `settings.py` ya incluye la tarea nueva de cotizaciones.
Hay que reiniciar **worker y beat**, no alcanza con uno:

```bash
pip install celery redis          # están comentados en requirements.txt
sudo systemctl restart celery celerybeat
```

Con Celery no uses el cron de la opción A: harías el trabajo dos veces.

## 7. Verificar

```bash
python manage.py check
python manage.py test webapp        # 121 tests
```

Y en el navegador, entrá a `/dashboard/` y revisá:

- Los KPIs muestran el consolidado en dólares (no "sin cotizar").
- `/dashboard/tareas/` abre y podés crear una tarea y marcarla hecha.
- `/dashboard/kanban/` ofrece crear el primer pipeline.
- `/dashboard/cotizaciones/` muestra la cotización vigente.

---

## Si algo sale mal

```bash
# volver el código
git reset --hard HEAD~1 && sudo systemctl restart gunicorn

# volver la base
cp ~/backups/db-<la-que-quieras>.sqlite3 db.sqlite3
```

Las migraciones son aditivas, así que una base restaurada de antes del deploy
funciona con el código viejo sin más vueltas.

---

## Pendiente

`DEBUG = True` sigue activo en producción. Cuando quieras encararlo, hay que
mover `SECRET_KEY` y `EMAIL_HOST_PASSWORD` a variables de entorno, poner
`DEBUG = False` y configurar nginx para servir `/static/`.
