try:
    from celery import shared_task
except ImportError:  # pragma: no cover - depende de qué haya instalado
    # Sin Celery las tareas quedan como funciones comunes. `crm_cron` las llama
    # igual, así que el CRM funciona completo en un servidor sin Redis.
    def shared_task(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def decorador(funcion):
            return funcion

        return decorador

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

from .cotizaciones import ErrorCotizaciones, sincronizar
from .models import Evento, Factura, ProyectoMensualidad, TipoCambio


def _alertas():
    return list(getattr(settings, "CRM_ALERT_EMAILS", []))


def _enviar(asunto, cuerpo, destinatarios):
    destinatarios = [d for d in dict.fromkeys(destinatarios) if d]
    if not destinatarios:
        return
    send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, destinatarios, fail_silently=False)


@shared_task
def enviar_recordatorios_mensualidades():
    """
    Corre una vez por día. Por cada mensualidad activa:
      1. avisa por mail cuando faltan `dias_antes_recordatorio` para el vencimiento
      2. emite la factura el día del vencimiento (si está configurada para hacerlo)
    """
    hoy = timezone.localdate()
    recordatorios, facturas_generadas, desfasadas = 0, 0, 0

    qs = ProyectoMensualidad.objects.select_related("proyecto", "proyecto__cliente").filter(activa=True)

    for m in qs:
        if not m.proximo_vencimiento:
            m.proximo_vencimiento = m.calcular_proximo_vencimiento(hoy)
            m.save(update_fields=["proximo_vencimiento"])

        proyecto = m.proyecto
        cliente = proyecto.cliente
        vencimiento = m.proximo_vencimiento
        fecha_recordatorio = vencimiento - timedelta(days=int(m.dias_antes_recordatorio))

        # --- 1. recordatorio previo ---
        if fecha_recordatorio == hoy and m.ultimo_recordatorio_enviado != hoy:
            _enviar(
                f"[CRM] Vence mensualidad: {proyecto.nombre} ({cliente.nombre})",
                (
                    f"Proyecto: {proyecto.nombre}\n"
                    f"Cliente: {cliente.nombre}\n"
                    f"Vencimiento: {vencimiento}\n"
                    f"Monto: {m.moneda} {m.monto}\n"
                ),
                _alertas() + ([cliente.email] if cliente.email else []),
            )
            m.ultimo_recordatorio_enviado = hoy
            m.save(update_fields=["ultimo_recordatorio_enviado"])
            recordatorios += 1

        # --- 2. emisión de la factura al vencer ---
        if vencimiento > hoy:
            continue

        if m.esta_desfasada:
            # el cron estuvo caído: resincronizamos sin emitir las facturas viejas
            m.proximo_vencimiento = m.calcular_proximo_vencimiento(hoy)
            m.save(update_fields=["proximo_vencimiento"])
            desfasadas += 1
            _enviar(
                f"[CRM] Mensualidad desfasada: {proyecto.nombre}",
                (
                    f"La mensualidad de «{proyecto.nombre}» ({cliente.nombre}) tenía el vencimiento "
                    f"en {vencimiento}, de un mes ya cerrado.\n\n"
                    f"No se emitieron las facturas atrasadas para no duplicar cobros. "
                    f"El próximo vencimiento quedó en {m.proximo_vencimiento}.\n"
                    f"Si faltó facturar algún período, hacelo a mano desde el CRM.\n"
                ),
                _alertas(),
            )
            continue

        if m.generar_factura_automatica:
            factura = m.generar_factura()
            if factura:
                facturas_generadas += 1
                _enviar(
                    f"[CRM] Factura generada: {proyecto.nombre}",
                    (
                        f"Se emitió la factura #{factura.id} de la mensualidad.\n\n"
                        f"Cliente: {cliente.nombre}\n"
                        f"Proyecto: {proyecto.nombre}\n"
                        f"Monto: {factura.moneda} {factura.monto}\n"
                        f"Período: {factura.periodo:%m/%Y}\n"
                        f"Vencimiento: {factura.fecha_vencimiento}\n"
                    ),
                    _alertas(),
                )

        m.avanzar_vencimiento()
        m.save(update_fields=["proximo_vencimiento"])

    return (
        f"{recordatorios} recordatorio(s), {facturas_generadas} factura(s) generada(s), "
        f"{desfasadas} mensualidad(es) resincronizada(s)"
    )


@shared_task
def enviar_recordatorios_eventos():
    """Manda el recordatorio de cada evento cuando entra en su ventana de aviso."""
    ahora = timezone.localtime()
    enviados = 0

    eventos = (
        Evento.objects.select_related("cliente", "proyecto")
        .filter(
            recordatorio_enviado_at__isnull=True,
            completado=False,
            inicio__gte=ahora - timedelta(days=1),
        )
        .order_by("inicio")
    )

    for ev in eventos:
        momento_envio = ev.inicio - timedelta(minutes=int(ev.recordatorio_minutos_antes))

        if ahora < momento_envio:
            continue

        _enviar(
            f"[CRM] Recordatorio: {ev.titulo}",
            (
                f"Evento: {ev.titulo}\n"
                f"Tipo: {ev.get_tipo_display()}\n"
                f"Inicio: {timezone.localtime(ev.inicio):%d/%m/%Y %H:%M}\n"
                f"Cliente: {ev.cliente.nombre if ev.cliente else '-'}\n"
                f"Proyecto: {ev.proyecto.nombre if ev.proyecto else '-'}\n"
                f"Dónde: {ev.ubicacion or '-'}\n\n"
                f"{ev.descripcion or ''}"
            ),
            _alertas(),
        )

        ev.recordatorio_enviado_at = ahora
        ev.save(update_fields=["recordatorio_enviado_at"])
        enviados += 1

    return f"{enviados} recordatorio(s) de evento"


@shared_task
def avisar_facturas_vencidas():
    """Resumen semanal de las facturas pendientes que ya pasaron su vencimiento."""
    hoy = timezone.localdate()

    vencidas = (
        Factura.objects.select_related("proyecto", "proyecto__cliente")
        .filter(estado="pendiente", fecha_vencimiento__lt=hoy)
        .order_by("fecha_vencimiento")
    )

    if not vencidas.exists():
        return "sin facturas vencidas"

    lineas = [
        f"#{f.id} — {f.proyecto.cliente.nombre} / {f.proyecto.nombre}: "
        f"{f.moneda} {f.monto} — vencía el {f.fecha_vencimiento} ({f.dias_vencida} días)"
        for f in vencidas
    ]

    _enviar(
        f"[CRM] {len(lineas)} factura(s) vencida(s)",
        "Facturas pendientes con vencimiento pasado:\n\n" + "\n".join(lineas),
        _alertas(),
    )

    return f"{len(lineas)} factura(s) vencida(s) informada(s)"


@shared_task(name="webapp.tasks.sincronizar_cotizaciones")
def sincronizar_cotizaciones():
    """
    Trae la cotización del día. Los importes en dólares del dashboard dependen
    de esto, así que si falla varios días seguidos conviene enterarse.
    """
    hoy = timezone.localdate()

    try:
        # el histórico ya está cargado; de acá en más alcanza con el día
        resumen = sincronizar(con_historico=False)
    except ErrorCotizaciones as e:
        resumen = {"errores": [str(e)], "creados": 0, "actualizados": 0}

    ultima = TipoCambio.vigente(hoy)
    atraso = (hoy - ultima.fecha).days if ultima else None

    # sin cotización fresca los equivalentes en USD quedan viejos: avisamos
    if atraso is None or atraso > 3:
        _enviar(
            "[CRM] Sin cotización actualizada del dólar",
            (
                f"No se pudo actualizar la cotización.\n\n"
                f"Última disponible: {ultima.fecha if ultima else 'ninguna'}"
                f"{f' ({atraso} días de atraso)' if atraso is not None else ''}.\n\n"
                + ("Errores:\n" + "\n".join(resumen["errores"]) if resumen.get("errores") else "")
            ),
            _alertas(),
        )

    return (
        f"{resumen['creados']} nueva(s), {resumen['actualizados']} actualizada(s); "
        f"última {ultima.fecha if ultima else 'ninguna'}"
    )
