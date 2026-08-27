"""
Celery es opcional.

En un servidor con Redis, importar `celery_app` acá es lo que hace que las
tareas se registren solas. Donde no hay Redis (PythonAnywhere, por ejemplo)
el paquete ni siquiera está instalado, y el sitio tiene que arrancar igual:
los trabajos periódicos se corren con `python manage.py crm_cron`.
"""

try:
    from .celery import app as celery_app
except ImportError:  # pragma: no cover - depende de qué haya instalado
    celery_app = None

__all__ = ("celery_app",)
