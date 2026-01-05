from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

from .models import ProyectoMensualidad, Evento, Factura


@shared_task
def enviar_recordatorios_mensualidades():
    hoy = timezone.localdate()
    alertas = getattr(settings, "CRM_ALERT_EMAILS", [])

    qs = ProyectoMensualidad.objects.select_related("proyecto", "proyecto__cliente").filter(activa=True)

    for m in qs:
        if not m.proximo_vencimiento:
            m.proximo_vencimiento = m.calcular_proximo_vencimiento(hoy)
            m.save(update_fields=["proximo_vencimiento"])

        fecha_recordatorio = m.proximo_vencimiento - timedelta(days=int(m.dias_antes_recordatorio))

        # ¿hoy toca recordar?
        if fecha_recordatorio != hoy:
            continue

        # evitar duplicados
        if m.ultimo_recordatorio_enviado == hoy:
            continue

        proyecto = m.proyecto
        cliente = proyecto.cliente

        asunto = f"[CRM] Vence mensualidad: {proyecto.nombre} ({cliente.nombre})"
        cuerpo = (
            f"Proyecto: {proyecto.nombre}\n"
            f"Cliente: {cliente.nombre}\n"
            f"Vencimiento: {m.proximo_vencimiento}\n"
            f"Monto: {m.moneda} {m.monto}\n"
        )

        destinatarios = list(alertas)
        # opcional: si querés mandar al cliente cuando tenga email
        if cliente.email:
            destinatarios.append(cliente.email)

        send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, destinatarios, fail_silently=False)

        m.ultimo_recordatorio_enviado = hoy
        # recalcular próximo vencimiento (al siguiente mes)
        m.proximo_vencimiento = m.calcular_proximo_vencimiento(m.proximo_vencimiento + timedelta(days=1))
        m.save(update_fields=["ultimo_recordatorio_enviado", "proximo_vencimiento"])


@shared_task
def enviar_recordatorios_eventos():
    ahora = timezone.localtime()
    alertas = getattr(settings, "CRM_ALERT_EMAILS", [])

    # eventos con recordatorio pendiente
    eventos = (
        Evento.objects.select_related("cliente", "proyecto")
        .filter(recordatorio_enviado_at__isnull=True, inicio__gte=ahora - timedelta(days=1))
        .order_by("inicio")
    )

    for ev in eventos:
        momento_envio = ev.inicio - timedelta(minutes=int(ev.recordatorio_minutos_antes))

        # enviamos cuando ya entró en ventana
        if ahora < momento_envio:
            continue

        asunto = f"[CRM] Recordatorio: {ev.titulo}"
        cuerpo = (
            f"Evento: {ev.titulo}\n"
            f"Tipo: {ev.get_tipo_display()}\n"
            f"Inicio: {timezone.localtime(ev.inicio)}\n"
            f"Cliente: {ev.cliente.nombre if ev.cliente else '-'}\n"
            f"Proyecto: {ev.proyecto.nombre if ev.proyecto else '-'}\n\n"
            f"{ev.descripcion or ''}"
        )

        destinatarios = list(alertas)
        send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, destinatarios, fail_silently=False)

        ev.recordatorio_enviado_at = ahora
        ev.save(update_fields=["recordatorio_enviado_at"])
