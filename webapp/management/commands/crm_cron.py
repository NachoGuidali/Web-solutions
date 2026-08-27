"""
Corre los trabajos periódicos del CRM sin depender de Celery ni de Redis.

Pensado para un cron común o para las Scheduled Tasks de PythonAnywhere, donde
no hay forma de dejar un worker y un broker corriendo. Cada trabajo se ejecuta
aislado: si uno falla, los demás igual corren.

    python manage.py crm_cron                 # todo lo que corresponda hoy
    python manage.py crm_cron --solo cotizaciones
    python manage.py crm_cron --dry-run       # dice qué haría, sin hacerlo
"""

import traceback

from django.core.management.base import BaseCommand
from django.utils import timezone

from webapp import tasks

# nombre -> (función, descripción, ¿corre todos los días?)
TRABAJOS = {
    "cotizaciones": (
        tasks.sincronizar_cotizaciones,
        "Cotización del dólar (los importes en USD dependen de esto)",
        True,
    ),
    "mensualidades": (
        tasks.enviar_recordatorios_mensualidades,
        "Avisos de vencimiento y emisión automática de facturas",
        True,
    ),
    "eventos": (
        tasks.enviar_recordatorios_eventos,
        "Recordatorios de los eventos de la agenda",
        True,
    ),
    "vencidas": (
        tasks.avisar_facturas_vencidas,
        "Resumen de facturas vencidas (sólo los lunes)",
        False,
    ),
}


class Command(BaseCommand):
    help = "Ejecuta los trabajos periódicos del CRM (alternativa a Celery beat)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--solo", choices=sorted(TRABAJOS), action="append", dest="solo",
            help="Correr únicamente este trabajo. Se puede repetir.",
        )
        parser.add_argument(
            "--todos", action="store_true",
            help="Correr también los que no tocan hoy (por ejemplo, el resumen semanal).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Mostrar qué haría, sin ejecutar.")

    def handle(self, *args, **opciones):
        ahora = timezone.localtime()
        es_lunes = ahora.weekday() == 0

        elegidos = opciones["solo"] or list(TRABAJOS)
        fallados = []

        self.stdout.write(f"CRM cron — {ahora:%d/%m/%Y %H:%M}")

        for nombre in elegidos:
            funcion, descripcion, diario = TRABAJOS[nombre]

            # el semanal sólo corre los lunes, salvo que lo pidan explícitamente
            if not diario and not opciones["todos"] and not opciones["solo"] and not es_lunes:
                self.stdout.write(f"  · {nombre}: se saltea (no es lunes)")
                continue

            if opciones["dry_run"]:
                self.stdout.write(f"  · {nombre}: correría — {descripcion}")
                continue

            try:
                resultado = funcion()
                self.stdout.write(self.style.SUCCESS(f"  ✓ {nombre}: {resultado}"))
            except Exception as e:
                fallados.append(nombre)
                self.stdout.write(self.style.ERROR(f"  ✗ {nombre}: {type(e).__name__}: {e}"))
                self.stderr.write(traceback.format_exc())

        if fallados:
            self.stdout.write(self.style.ERROR(f"Terminó con fallas en: {', '.join(fallados)}"))
        else:
            self.stdout.write(self.style.SUCCESS("Terminó sin errores."))
