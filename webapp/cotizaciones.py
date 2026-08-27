"""
Sincronización de cotizaciones del dólar.

Dos fuentes públicas y sin API key:
  - dolarapi.com          → la cotización de hoy
  - argentinadatos.com    → la serie histórica diaria (desde 2018)

Las dos usan los mismos nombres de casa (bolsa, blue, oficial, cripto, tarjeta).
"""

import json
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .models import TipoCambio, _casa_por_defecto

URL_HOY = "https://dolarapi.com/v1/dolares/{casa}"
URL_HISTORICO = "https://api.argentinadatos.com/v1/cotizaciones/dolares/{casa}"

TIMEOUT = 20


class ErrorCotizaciones(Exception):
    """No se pudo traer la cotización (sin red, API caída, respuesta rara)."""


def _pedir(url, timeout=TIMEOUT):
    peticion = urllib.request.Request(url, headers={"User-Agent": "SupReg-CRM/1.0"})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        raise ErrorCotizaciones(f"No se pudo consultar {url}: {e}") from e
    except json.JSONDecodeError as e:
        raise ErrorCotizaciones(f"Respuesta inesperada de {url}: {e}") from e


def _a_decimal(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def _a_fecha(valor):
    if isinstance(valor, date):
        return valor
    if not valor:
        return None
    texto = str(valor)[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _guardar(casa, fecha, compra, venta, fuente):
    """Alta o actualización de una cotización. Devuelve 'creado' | 'actualizado' | None."""
    if not fecha or venta is None:
        return None

    obj, creado = TipoCambio.objects.get_or_create(
        fecha=fecha, casa=casa,
        defaults={"compra": compra, "venta": venta, "fuente": fuente},
    )
    if creado:
        return "creado"

    # sólo pisamos un dato de la API con otro de la API; lo cargado a mano manda
    if obj.fuente == "manual" or (obj.venta == venta and obj.compra == compra):
        return None

    obj.compra, obj.venta, obj.fuente = compra, venta, fuente
    obj.save(update_fields=["compra", "venta", "fuente"])
    return "actualizado"


def traer_hoy(casa=None):
    """Cotización del día. Devuelve el TipoCambio guardado."""
    casa = casa or _casa_por_defecto()
    datos = _pedir(URL_HOY.format(casa=casa))

    fecha = _a_fecha(datos.get("fechaActualizacion")) or date.today()
    venta = _a_decimal(datos.get("venta"))
    compra = _a_decimal(datos.get("compra"))

    if venta is None:
        raise ErrorCotizaciones(f"La API no devolvió cotización de venta para «{casa}».")

    _guardar(casa, fecha, compra, venta, "api")
    return TipoCambio.objects.get(fecha=fecha, casa=casa)


def traer_historico(casa=None, desde=None, hasta=None):
    """
    Trae la serie diaria y guarda lo que falte.
    Devuelve (creados, actualizados, leídos).
    """
    casa = casa or _casa_por_defecto()
    datos = _pedir(URL_HISTORICO.format(casa=casa), timeout=TIMEOUT * 2)

    if not isinstance(datos, list):
        raise ErrorCotizaciones("La API de histórico no devolvió una lista.")

    creados = actualizados = leidos = 0

    for fila in datos:
        fecha = _a_fecha(fila.get("fecha"))
        if not fecha:
            continue
        if desde and fecha < desde:
            continue
        if hasta and fecha > hasta:
            continue

        leidos += 1
        resultado = _guardar(casa, fecha, _a_decimal(fila.get("compra")), _a_decimal(fila.get("venta")), "api")
        if resultado == "creado":
            creados += 1
        elif resultado == "actualizado":
            actualizados += 1

    return creados, actualizados, leidos


def sincronizar(casa=None, desde=None, con_historico=True):
    """
    Pone las cotizaciones al día. Si `con_historico`, además completa los huecos
    hacia atrás desde `desde` (por defecto, la cotización más vieja que tengamos
    o la fecha de la factura más antigua).

    Devuelve un dict con el resumen. No revienta si falla una de las dos fuentes.
    """
    casa = casa or _casa_por_defecto()
    resumen = {"casa": casa, "creados": 0, "actualizados": 0, "errores": []}

    if con_historico:
        try:
            creados, actualizados, _ = traer_historico(casa, desde=desde)
            resumen["creados"] += creados
            resumen["actualizados"] += actualizados
        except ErrorCotizaciones as e:
            resumen["errores"].append(str(e))

    try:
        traer_hoy(casa)
    except ErrorCotizaciones as e:
        resumen["errores"].append(str(e))

    resumen["total"] = TipoCambio.objects.filter(casa=casa).count()
    ultima = TipoCambio.objects.filter(casa=casa).order_by("-fecha").first()
    resumen["ultima"] = ultima.fecha if ultima else None
    return resumen
