import csv
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from datetime import timedelta

from .cotizaciones import ErrorCotizaciones, sincronizar
from .forms import (
    ClienteForm, DealForm, EtapaForm, EventoForm, FacturaForm, InteraccionForm,
    PipelineForm, ProyectoForm, ProyectoMensualidadForm, TareaForm,
    TareaRapidaForm, TipoCambioForm,
)
from .models import (
    Cliente, Cotizaciones, Deal, Etapa, Evento, Factura, Interaccion,
    Pipeline, Proyecto, ProyectoMensualidad, Tarea, TipoCambio,
)

PAGE_SIZE = 20


# ---------------------------------------------------------------- helpers

def _paginar(request, queryset, por_pagina=PAGE_SIZE):
    paginator = Paginator(queryset, por_pagina)
    return paginator.get_page(request.GET.get("page"))


def _querystring_sin_page(request):
    """Querystring actual sin `page`, para armar los links de paginación."""
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""


def _suma(queryset, campo="monto"):
    return queryset.aggregate(t=Sum(campo))["t"] or Decimal("0")


def _volver(request, defecto):
    """Redirige al `next` del request si es una ruta interna, si no al defecto."""
    destino = request.POST.get("next") or request.GET.get("next")
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect(defecto)


def _primer_dia_mes(d):
    return d.replace(day=1)


def _mes_anterior(d):
    return _primer_dia_mes(d) - timedelta(days=1)


# ---------------------------------------------------------------- sitio público

def home(request):
    return render(request, 'home.html', {})


def servicios(request):
    return render(request, 'servicios.html')


def about(request):
    return render(request, 'about.html')


def privacidad(request):
    return render(request, 'privacidad.html')


def terminos(request):
    return render(request, 'terminos.html')


# ---------------------------------------------------------------- dashboard

def _suma_usd(facturas, cotizaciones):
    """
    Total en dólares de un conjunto de facturas.
    Devuelve (total, cuántas no se pudieron convertir por falta de cotización).
    """
    total, sin_cotizar = Decimal("0"), 0
    for f in facturas:
        valor = cotizaciones.a_usd(f.monto, f.moneda, f.fecha_referencia, f.tipo_cambio)
        if valor is None:
            sin_cotizar += 1
        else:
            total += valor
    return total, sin_cotizar


def _serie_ingresos(cotizaciones, meses=6):
    """
    Cobranzas de los últimos `meses`, por mes.

    Se agrupa por fecha de cobro (no de emisión) y se muestra el equivalente en
    dólares: con la inflación local, comparar pesos de meses distintos no dice nada.
    """
    hoy = timezone.localdate()
    primer_mes = _primer_dia_mes(hoy)
    for _ in range(meses - 1):
        primer_mes = _primer_dia_mes(_mes_anterior(primer_mes))

    cobradas = Factura.objects.filter(estado="cobrado").exclude(
        Q(fecha_cobro__lt=primer_mes) | Q(fecha_cobro__isnull=True, fecha__lt=primer_mes)
    )

    acumulado = {}
    for f in cobradas:
        ref = f.fecha_cobro or f.fecha
        clave = (ref.year, ref.month)
        datos = acumulado.setdefault(clave, {"ars": Decimal("0"), "usd": Decimal("0"), "eq": Decimal("0")})
        datos["ars" if f.moneda == "ARS" else "usd"] += f.monto

        equivalente = cotizaciones.a_usd(f.monto, f.moneda, ref, f.tipo_cambio)
        if equivalente:
            datos["eq"] += equivalente

    NOMBRES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    vacio = {"ars": Decimal("0"), "usd": Decimal("0"), "eq": Decimal("0")}

    serie = []
    cursor = primer_mes
    while cursor <= hoy:
        datos = acumulado.get((cursor.year, cursor.month), vacio)
        serie.append({
            "etiqueta": f"{NOMBRES[cursor.month - 1]} {str(cursor.year)[2:]}",
            "ars": datos["ars"],
            "usd": datos["usd"],
            "equivalente": datos["eq"],
        })
        cursor = _primer_dia_mes(cursor + timedelta(days=32))

    tope = max([p["equivalente"] for p in serie] + [Decimal("0")])
    for p in serie:
        p["alto"] = int(p["equivalente"] / tope * 100) if tope else 0

    return serie


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    ahora = timezone.localtime()
    inicio_mes = _primer_dia_mes(hoy)
    cotizaciones = Cotizaciones()

    cobradas = Factura.objects.filter(estado="cobrado")
    pendientes = Factura.objects.filter(estado="pendiente")
    vencidas = pendientes.filter(fecha_vencimiento__lt=hoy)

    # el mes se mide por fecha de cobro; las viejas sin ese dato caen a la de emisión
    del_mes = cobradas.filter(
        Q(fecha_cobro__gte=inicio_mes) | Q(fecha_cobro__isnull=True, fecha__gte=inicio_mes)
    )

    mes_usd, _ = _suma_usd(del_mes, cotizaciones)
    total_usd, total_sin_cotizar = _suma_usd(cobradas, cotizaciones)
    por_cobrar_usd, pendientes_sin_cotizar = _suma_usd(pendientes, cotizaciones)
    vencido_usd, _ = _suma_usd(vencidas, cotizaciones)

    proyectos = Proyecto.objects.select_related("cliente")
    abiertos = proyectos.filter(estado__in=Proyecto.ESTADOS_ABIERTOS)
    atrasados = abiertos.filter(fecha_entrega__lt=hoy).order_by("fecha_entrega")

    vencimientos = (
        ProyectoMensualidad.objects
        .select_related("proyecto", "proyecto__cliente")
        .filter(activa=True, proximo_vencimiento__lte=hoy + timedelta(days=14))
        .order_by("proximo_vencimiento")
    )

    eventos = (
        Evento.objects
        .select_related("cliente", "proyecto")
        .filter(completado=False, inicio__gte=ahora - timedelta(hours=2), inicio__lte=ahora + timedelta(days=7))
        .order_by("inicio")
    )

    cotizacion = TipoCambio.vigente(hoy)
    serie = _serie_ingresos(cotizaciones)

    grupos_tareas = _tareas_agrupadas()
    pendientes_tareas = Tarea.objects.filter(completada=False)

    pipeline = Pipeline.objects.filter(activo=True).first()
    deals_abiertos = (
        Deal.objects.filter(etapa__tipo="abierta")
        .select_related("cliente", "etapa", "pipeline")
        .order_by("cierre_estimado", "-id")
    )
    valor_pipeline, _ = Decimal("0"), None
    for d in deals_abiertos:
        equivalente = cotizaciones.a_usd(d.valor, d.moneda, hoy)
        if equivalente:
            valor_pipeline += equivalente

    context = {
        # KPIs por moneda
        'total_facturado_ars': _suma(cobradas.filter(moneda='ARS')),
        'total_facturado_usd': _suma(cobradas.filter(moneda='USD')),
        'por_cobrar_ars': _suma(pendientes.filter(moneda='ARS')),
        'por_cobrar_usd': _suma(pendientes.filter(moneda='USD')),
        'vencido_ars': _suma(vencidas.filter(moneda='ARS')),
        'vencido_usd': _suma(vencidas.filter(moneda='USD')),
        'mes_ars': _suma(del_mes.filter(moneda='ARS')),
        'mes_usd_nativo': _suma(del_mes.filter(moneda='USD')),

        # consolidado en dólares
        'mes_equivalente': mes_usd,
        'total_equivalente': total_usd,
        'por_cobrar_equivalente': por_cobrar_usd,
        'vencido_equivalente': vencido_usd,
        'sin_cotizar': total_sin_cotizar + pendientes_sin_cotizar,

        # cotización usada
        'cotizacion': cotizacion,
        'cotizacion_atrasada': bool(cotizacion and (hoy - cotizacion.fecha).days > 3),
        'dias_sin_cotizar': (hoy - cotizacion.fecha).days if cotizacion else None,

        # operativos
        'total_clientes': Cliente.objects.count(),
        'clientes_activos': Cliente.objects.filter(estado='activo').count(),
        'proyectos_activos': abiertos.count(),
        'proyectos_atrasados': atrasados.count(),
        'facturas_pendientes': pendientes.count(),
        'facturas_vencidas': vencidas.count(),

        # listas
        'vencimientos': vencimientos,
        'eventos': eventos,
        'atrasados': atrasados[:5],
        'ultimas_facturas': Factura.objects.select_related("proyecto", "proyecto__cliente")[:6],

        'serie_ingresos': serie,
        'total_del_periodo': sum(p["equivalente"] for p in serie),

        # tareas
        'tareas': grupos_tareas,
        'tareas_pendientes': pendientes_tareas.count(),
        'tareas_vencidas': len(grupos_tareas["vencidas"]),
        'tareas_hoy': len(grupos_tareas["hoy"]),
        'form_tarea': TareaRapidaForm(),

        # pipeline
        'pipeline': pipeline,
        'deals_abiertos': deals_abiertos[:5],
        'total_deals_abiertos': deals_abiertos.count(),
        'valor_pipeline': valor_pipeline,
        'deals_demorados': sum(1 for d in deals_abiertos if d.esta_demorado),

        'hoy': hoy,
    }
    return render(request, 'dashboard.html', context)


# ---------------------------------------------------------------- clientes

@login_required
def clientes_list(request):
    qs = Cliente.objects.annotate(
        n_proyectos=Count("proyectos", distinct=True),
        n_eventos=Count("eventos", distinct=True),
    )

    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    orden = request.GET.get("orden", "nombre").strip()

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q)
            | Q(email__icontains=q)
            | Q(empresa__icontains=q)
            | Q(telefono__icontains=q)
            | Q(notas__icontains=q)
        )

    if estado in dict(Cliente.ESTADOS):
        qs = qs.filter(estado=estado)

    ORDENES = {
        "nombre": "nombre",
        "-nombre": "-nombre",
        "reciente": "-id",
        "proyectos": "-n_proyectos",
    }
    qs = qs.order_by(ORDENES.get(orden, "nombre"))

    context = {
        "page_obj": _paginar(request, qs),
        "total": qs.count(),
        "filters": {"q": q, "estado": estado, "orden": orden},
        "estados": Cliente.ESTADOS,
        "qs_extra": _querystring_sin_page(request),
    }
    return render(request, "clientes_list.html", context)


@login_required
def cliente_detail(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)

    proyectos = cliente.proyectos.select_related("cliente").prefetch_related("mensualidad")
    facturas = Factura.objects.filter(proyecto__cliente=cliente).select_related("proyecto")

    form = InteraccionForm(cliente=cliente)

    context = {
        "cliente": cliente,
        "proyectos": proyectos,
        "facturas": facturas[:10],
        "total_facturas": facturas.count(),
        "eventos": cliente.eventos.select_related("proyecto").order_by("-inicio")[:8],
        "interacciones": cliente.interacciones.select_related("proyecto", "autor")[:20],
        "form_interaccion": form,
        "cobrado_ars": _suma(facturas.filter(estado="cobrado", moneda="ARS")),
        "cobrado_usd": _suma(facturas.filter(estado="cobrado", moneda="USD")),
        "pendiente_ars": _suma(facturas.filter(estado="pendiente", moneda="ARS")),
        "pendiente_usd": _suma(facturas.filter(estado="pendiente", moneda="USD")),
    }
    return render(request, "cliente_detail.html", context)


@login_required
def nuevo_cliente(request):
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            cliente = form.save()
            messages.success(request, f"Cliente «{cliente.nombre}» creado.")
            return redirect('cliente_detail', pk=cliente.pk)
    else:
        form = ClienteForm()
    return render(request, 'cliente_form.html', {'form': form, 'mode': 'create'})


@login_required
def cliente_update(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente actualizado.")
            return redirect('cliente_detail', pk=cliente.pk)
    else:
        form = ClienteForm(instance=cliente)
    return render(request, 'cliente_form.html', {'form': form, 'mode': 'update', 'cliente': cliente})


@login_required
def cliente_delete(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method == 'POST':
        nombre = cliente.nombre
        cliente.delete()
        messages.success(request, f"Cliente «{nombre}» eliminado junto a sus proyectos y facturas.")
        return redirect('clientes_list')

    return render(request, 'confirm_delete.html', {
        'objeto': cliente,
        'titulo': 'Eliminar cliente',
        'detalle': (
            f"Se van a borrar también sus {cliente.proyectos.count()} proyecto(s), "
            f"con sus facturas y mensualidades."
        ),
        'cancelar_url': cliente.get_absolute_url(),
    })


@login_required
def clientes_export(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="clientes_{timezone.localdate()}.csv"'

    writer = csv.writer(response)
    writer.writerow(["ID", "Nombre", "Empresa", "Email", "Teléfono", "Estado", "Proyectos", "Cobrado ARS", "Cobrado USD"])

    for c in Cliente.objects.prefetch_related("proyectos"):
        writer.writerow([
            c.id, c.nombre, c.empresa or "", c.email or "", c.telefono or "",
            c.get_estado_display(), c.proyectos.count(), c.cobrado_ars, c.cobrado_usd,
        ])

    return response


# ---------------------------------------------------------------- interacciones

@login_required
def interaccion_create(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)

    if request.method == "POST":
        form = InteraccionForm(request.POST, cliente=cliente)
        if form.is_valid():
            interaccion = form.save(commit=False)
            interaccion.cliente = cliente
            interaccion.autor = request.user
            interaccion.save()
            messages.success(request, "Interacción registrada.")
        else:
            messages.error(request, "No se pudo guardar la interacción: revisá los campos.")

    return redirect('cliente_detail', pk=cliente.pk)


@login_required
def interaccion_delete(request, pk):
    interaccion = get_object_or_404(Interaccion, pk=pk)
    cliente_id = interaccion.cliente_id
    if request.method == "POST":
        interaccion.delete()
        messages.success(request, "Interacción eliminada.")
    return redirect('cliente_detail', pk=cliente_id)


# ---------------------------------------------------------------- proyectos

@login_required
def proyectos_list(request):
    qs = Proyecto.objects.select_related("cliente").prefetch_related("mensualidad")

    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    moneda = request.GET.get("moneda", "").strip()
    orden = request.GET.get("orden", "reciente").strip()

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q)
            | Q(descripcion__icontains=q)
            | Q(cliente__nombre__icontains=q)
            | Q(observaciones__icontains=q)
        )

    if estado == "abiertos":
        qs = qs.filter(estado__in=Proyecto.ESTADOS_ABIERTOS)
    elif estado == "atrasados":
        qs = qs.filter(estado__in=Proyecto.ESTADOS_ABIERTOS, fecha_entrega__lt=timezone.localdate())
    elif estado in dict(Proyecto.ESTADOS):
        qs = qs.filter(estado=estado)

    if cliente_id.isdigit():
        qs = qs.filter(cliente_id=int(cliente_id))

    if moneda in ("ARS", "USD"):
        qs = qs.filter(moneda=moneda)

    ORDENES = {
        "reciente": "-fecha_inicio",
        "antiguo": "fecha_inicio",
        "entrega": "fecha_entrega",
        "precio": "-precio",
        "nombre": "nombre",
    }
    qs = qs.order_by(ORDENES.get(orden, "-fecha_inicio"))

    context = {
        "page_obj": _paginar(request, qs),
        "total": qs.count(),
        "filters": {"q": q, "estado": estado, "cliente": cliente_id, "moneda": moneda, "orden": orden},
        "estados": Proyecto.ESTADOS,
        "clientes": Cliente.objects.all(),
        "qs_extra": _querystring_sin_page(request),
        "presupuestado_ars": _suma(qs.filter(moneda="ARS"), "precio"),
        "presupuestado_usd": _suma(qs.filter(moneda="USD"), "precio"),
    }
    return render(request, "proyectos_list.html", context)


@login_required
def proyecto_detail(request, pk):
    proyecto = get_object_or_404(Proyecto.objects.select_related("cliente"), pk=pk)
    facturas = proyecto.facturas.all()

    context = {
        "proyecto": proyecto,
        "facturas": facturas,
        "mensualidad": getattr(proyecto, "mensualidad", None),
        "eventos": proyecto.eventos.order_by("-inicio")[:8],
        "interacciones": proyecto.interacciones.select_related("autor")[:10],
        "cobrado": _suma(facturas.filter(estado="cobrado", moneda=proyecto.moneda)),
        "pendiente": _suma(facturas.filter(estado="pendiente", moneda=proyecto.moneda)),
    }
    return render(request, "proyecto_detail.html", context)


@login_required
def nuevo_proyecto(request):
    if request.method == 'POST':
        form = ProyectoForm(request.POST)
        if form.is_valid():
            proyecto = form.save()
            messages.success(request, f"Proyecto «{proyecto.nombre}» creado.")
            return redirect('proyecto_detail', pk=proyecto.pk)
    else:
        inicial = {}
        cliente_id = request.GET.get("cliente")
        if cliente_id and cliente_id.isdigit():
            inicial["cliente"] = cliente_id
        form = ProyectoForm(initial=inicial)
    return render(request, 'proyecto_form.html', {'form': form, 'mode': 'create'})


@login_required
def editar_proyecto(request, id):
    proyecto = get_object_or_404(Proyecto, id=id)
    if request.method == 'POST':
        form = ProyectoForm(request.POST, instance=proyecto)
        if form.is_valid():
            form.save()
            messages.success(request, "Proyecto actualizado.")
            return redirect('proyecto_detail', pk=proyecto.pk)
    else:
        form = ProyectoForm(instance=proyecto)
    return render(request, 'proyecto_form.html', {'form': form, 'mode': 'update', 'proyecto': proyecto})


@login_required
def proyecto_delete(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        cliente_id = proyecto.cliente_id
        nombre = proyecto.nombre
        proyecto.delete()
        messages.success(request, f"Proyecto «{nombre}» eliminado.")
        return redirect('cliente_detail', pk=cliente_id)

    return render(request, 'confirm_delete.html', {
        'objeto': proyecto,
        'titulo': 'Eliminar proyecto',
        'detalle': f"Se borran también sus {proyecto.facturas.count()} factura(s) y la mensualidad asociada.",
        'cancelar_url': proyecto.get_absolute_url(),
    })


@login_required
def proyecto_cambiar_estado(request, pk):
    """Cambio rápido de estado desde la ficha o el listado."""
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == "POST":
        nuevo = request.POST.get("estado", "")
        if nuevo in dict(Proyecto.ESTADOS):
            proyecto.estado = nuevo
            proyecto.save(update_fields=["estado"])
            messages.success(request, f"Proyecto marcado como «{proyecto.get_estado_display()}».")
        else:
            messages.error(request, "Estado inválido.")
    return _volver(request, proyecto.get_absolute_url())


# ---------------------------------------------------------------- facturas

@login_required
def facturas_list(request):
    qs = Factura.objects.select_related("proyecto", "proyecto__cliente")

    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    moneda = request.GET.get("moneda", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()

    if q:
        qs = qs.filter(
            Q(proyecto__nombre__icontains=q)
            | Q(proyecto__cliente__nombre__icontains=q)
            | Q(metodo_pago__icontains=q)
            | Q(observaciones__icontains=q)
        )

    if estado == "vencidas":
        qs = qs.filter(estado="pendiente", fecha_vencimiento__lt=timezone.localdate())
    elif estado in ("pendiente", "cobrado"):
        qs = qs.filter(estado=estado)

    if moneda in ("ARS", "USD"):
        qs = qs.filter(moneda=moneda)

    if proyecto_id.isdigit():
        qs = qs.filter(proyecto_id=int(proyecto_id))

    if cliente_id.isdigit():
        qs = qs.filter(proyecto__cliente_id=int(cliente_id))

    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)

    if request.GET.get("export") == "csv":
        return _facturas_csv(qs)

    context = {
        "page_obj": _paginar(request, qs),
        "total": qs.count(),
        "filters": {
            "q": q, "estado": estado, "moneda": moneda, "proyecto": proyecto_id,
            "cliente": cliente_id, "desde": desde, "hasta": hasta,
        },
        "clientes": Cliente.objects.all(),
        "qs_extra": _querystring_sin_page(request),
        # totales del filtro actual
        "cobrado_ars": _suma(qs.filter(estado="cobrado", moneda="ARS")),
        "cobrado_usd": _suma(qs.filter(estado="cobrado", moneda="USD")),
        "pendiente_ars": _suma(qs.filter(estado="pendiente", moneda="ARS")),
        "pendiente_usd": _suma(qs.filter(estado="pendiente", moneda="USD")),
    }
    return render(request, "facturas_list.html", context)


def _facturas_csv(qs):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="facturas_{timezone.localdate()}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "ID", "Fecha", "Vencimiento", "Cliente", "Proyecto", "Moneda",
        "Monto", "Estado", "Fecha cobro", "Método", "Automática", "Observaciones",
    ])

    for f in qs:
        writer.writerow([
            f.id, f.fecha, f.fecha_vencimiento or "", f.proyecto.cliente.nombre, f.proyecto.nombre,
            f.moneda, f.monto, f.get_estado_display(), f.fecha_cobro or "",
            f.metodo_pago or "", "sí" if f.automatica else "no", (f.observaciones or "").replace("\n", " "),
        ])

    return response


@login_required
def factura_create(request):
    if request.method == "POST":
        form = FacturaForm(request.POST)
        if form.is_valid():
            factura = form.save()
            messages.success(request, f"Factura #{factura.id} creada.")
            return _volver(request, "facturas_list")
    else:
        inicial = {}
        proyecto_id = request.GET.get("proyecto")
        if proyecto_id and proyecto_id.isdigit():
            proyecto = Proyecto.objects.filter(pk=proyecto_id).first()
            if proyecto:
                inicial = {"proyecto": proyecto.pk, "moneda": proyecto.moneda}
        form = FacturaForm(initial=inicial)

    return render(request, "factura_form.html", {"form": form, "mode": "create"})


@login_required
def factura_update(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        form = FacturaForm(request.POST, instance=factura)
        if form.is_valid():
            form.save()
            messages.success(request, "Factura actualizada.")
            return _volver(request, "facturas_list")
    else:
        form = FacturaForm(instance=factura)

    return render(request, "factura_form.html", {"form": form, "mode": "update", "factura": factura})


@login_required
def factura_delete(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        factura.delete()
        messages.success(request, "Factura eliminada.")
        return _volver(request, "facturas_list")

    return render(request, "confirm_delete.html", {
        "objeto": factura,
        "titulo": f"Eliminar factura #{factura.id}",
        "detalle": f"{factura.moneda} {factura.monto} — {factura.proyecto.nombre} ({factura.fecha}).",
        "cancelar_url": None,
    })


@login_required
def factura_mark_cobrada(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        factura.marcar_cobrada(metodo_pago=request.POST.get("metodo_pago") or None)
        messages.success(request, f"Factura #{factura.id} marcada como cobrada.")

    return _volver(request, "facturas_list")


@login_required
def factura_mark_pendiente(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        factura.estado = "pendiente"
        factura.save()
        messages.success(request, f"Factura #{factura.id} vuelta a pendiente.")

    return _volver(request, "facturas_list")


# ---------------------------------------------------------------- mensualidades

@login_required
def mensualidades_list(request):
    qs = (
        ProyectoMensualidad.objects
        .select_related("proyecto", "proyecto__cliente")
        .order_by("-activa", "proximo_vencimiento")
    )

    activa = request.GET.get("activa", "").strip()
    if activa == "1":
        qs = qs.filter(activa=True)
    elif activa == "0":
        qs = qs.filter(activa=False)

    activas = ProyectoMensualidad.objects.filter(activa=True)

    context = {
        "mensualidades": qs,
        "filters": {"activa": activa},
        "mrr_ars": _suma(activas.filter(moneda="ARS")),
        "mrr_usd": _suma(activas.filter(moneda="USD")),
        "hoy": timezone.localdate(),
    }
    return render(request, "mensualidades_list.html", context)


@login_required
def mensualidad_create(request):
    if request.method == "POST":
        form = ProyectoMensualidadForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Mensualidad creada.")
            return redirect("mensualidades_list")
    else:
        inicial = {}
        proyecto_id = request.GET.get("proyecto")
        if proyecto_id and proyecto_id.isdigit():
            proyecto = Proyecto.objects.filter(pk=proyecto_id).first()
            if proyecto:
                inicial = {"proyecto": proyecto.pk, "moneda": proyecto.moneda}
        form = ProyectoMensualidadForm(initial=inicial)
    return render(request, "mensualidad_form.html", {"form": form, "mode": "create"})


@login_required
def mensualidad_update(request, pk):
    obj = get_object_or_404(ProyectoMensualidad, pk=pk)
    if request.method == "POST":
        form = ProyectoMensualidadForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Mensualidad actualizada.")
            return redirect("mensualidades_list")
    else:
        form = ProyectoMensualidadForm(instance=obj)
    return render(request, "mensualidad_form.html", {"form": form, "mode": "update", "mensualidad": obj})


@login_required
def mensualidad_delete(request, pk):
    obj = get_object_or_404(ProyectoMensualidad, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Mensualidad eliminada.")
        return redirect("mensualidades_list")

    return render(request, "confirm_delete.html", {
        "objeto": obj,
        "titulo": "Eliminar mensualidad",
        "detalle": "Las facturas ya emitidas se mantienen; sólo se corta el cobro recurrente.",
        "cancelar_url": None,
    })


@login_required
def mensualidad_toggle(request, pk):
    obj = get_object_or_404(ProyectoMensualidad, pk=pk)
    if request.method == "POST":
        obj.activa = not obj.activa
        obj.save(update_fields=["activa"])
        messages.success(request, "Mensualidad " + ("activada." if obj.activa else "pausada."))
    return _volver(request, "mensualidades_list")


@login_required
def mensualidad_facturar(request, pk):
    """Emite ya la factura del período pendiente, sin esperar al cron."""
    obj = get_object_or_404(ProyectoMensualidad, pk=pk)

    if request.method == "POST":
        # `forzar` permite adelantar un período a propósito
        forzar = request.POST.get("forzar") == "1"

        if not forzar and not obj.puede_facturar_ahora:
            messages.warning(
                request,
                f"El período {obj.periodo_pendiente:%m/%Y} todavía no toca facturarlo "
                f"(vence el {obj.proximo_vencimiento:%d/%m/%Y}).",
            )
            return _volver(request, "mensualidades_list")

        factura = obj.generar_factura(forzar=forzar)
        if factura:
            obj.avanzar_vencimiento()
            obj.save(update_fields=["proximo_vencimiento"])
            messages.success(request, f"Factura #{factura.id} generada por {factura.moneda} {factura.monto}.")
        else:
            messages.warning(request, "Ese período ya estaba facturado.")

    return _volver(request, "mensualidades_list")


# ---------------------------------------------------------------- agenda

@login_required
def eventos_list(request):
    qs = Evento.objects.select_related("cliente", "proyecto")
    ahora = timezone.localtime()

    vista = request.GET.get("vista", "proximos").strip()
    tipo = request.GET.get("tipo", "").strip()

    if vista == "proximos":
        qs = qs.filter(inicio__gte=ahora - timedelta(hours=2)).order_by("inicio")
    elif vista == "pasados":
        qs = qs.filter(inicio__lt=ahora).order_by("-inicio")
    elif vista == "pendientes":
        qs = qs.filter(completado=False).order_by("inicio")
    else:  # todos
        qs = qs.order_by("-inicio")

    if tipo in dict(Evento.TIPOS):
        qs = qs.filter(tipo=tipo)

    context = {
        "page_obj": _paginar(request, qs),
        "total": qs.count(),
        "filters": {"vista": vista, "tipo": tipo},
        "tipos": Evento.TIPOS,
        "qs_extra": _querystring_sin_page(request),
        "ahora": ahora,
        "hoy": timezone.localdate(),
    }
    return render(request, "eventos_list.html", context)


@login_required
def evento_create(request):
    if request.method == "POST":
        form = EventoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Evento creado.")
            return _volver(request, "eventos_list")
    else:
        inicial = {}
        for campo in ("cliente", "proyecto"):
            valor = request.GET.get(campo)
            if valor and valor.isdigit():
                inicial[campo] = valor
        form = EventoForm(initial=inicial)
    return render(request, "evento_form.html", {"form": form, "mode": "create"})


@login_required
def evento_update(request, pk):
    obj = get_object_or_404(Evento, pk=pk)
    if request.method == "POST":
        form = EventoForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Evento actualizado.")
            return _volver(request, "eventos_list")
    else:
        form = EventoForm(instance=obj)
    return render(request, "evento_form.html", {"form": form, "mode": "update", "evento": obj})


@login_required
def evento_delete(request, pk):
    obj = get_object_or_404(Evento, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Evento eliminado.")
        return _volver(request, "eventos_list")

    return render(request, "confirm_delete.html", {
        "objeto": obj,
        "titulo": "Eliminar evento",
        "detalle": f"{obj.get_tipo_display()} — {timezone.localtime(obj.inicio):%d/%m/%Y %H:%M}",
        "cancelar_url": None,
    })


@login_required
def evento_toggle(request, pk):
    obj = get_object_or_404(Evento, pk=pk)
    if request.method == "POST":
        obj.completado = not obj.completado
        obj.save(update_fields=["completado"])
        messages.success(request, "Evento " + ("completado." if obj.completado else "reabierto."))
    return _volver(request, "eventos_list")


# ---------------------------------------------------------------- cotizaciones

@login_required
def cotizaciones_list(request):
    """Cotizaciones cargadas + alta manual y sincronización con la API."""
    casa = request.GET.get("casa", "").strip() or None
    qs = TipoCambio.objects.all()
    if casa:
        qs = qs.filter(casa=casa)

    hoy = timezone.localdate()
    vigente = TipoCambio.vigente(hoy)

    # facturas que hoy no se pueden expresar en dólares
    sin_cotizar = [f for f in Factura.objects.select_related("proyecto") if f.monto_usd is None]

    context = {
        "page_obj": _paginar(request, qs, 30),
        "total": qs.count(),
        "form": TipoCambioForm(),
        "casas": TipoCambio.CASAS,
        "filters": {"casa": casa or ""},
        "qs_extra": _querystring_sin_page(request),
        "vigente": vigente,
        "dias_sin_cotizar": (hoy - vigente.fecha).days if vigente else None,
        "casa_activa": settings.CRM_CASA_DOLAR,
        "sin_cotizar": sin_cotizar,
    }
    return render(request, "cotizaciones_list.html", context)


@login_required
def cotizacion_create(request):
    if request.method == "POST":
        form = TipoCambioForm(request.POST)
        if form.is_valid():
            tc = form.save(commit=False)
            tc.fuente = "manual"
            tc.save()
            messages.success(request, f"Cotización del {tc.fecha} guardada (${tc.venta}).")
        else:
            errores = "; ".join(f"{campo}: {e.as_text()}" for campo, e in form.errors.items())
            messages.error(request, f"No se pudo guardar: {errores}")
    return redirect("cotizaciones_list")


@login_required
def cotizacion_delete(request, pk):
    tc = get_object_or_404(TipoCambio, pk=pk)
    if request.method == "POST":
        tc.delete()
        messages.success(request, "Cotización eliminada.")
    return _volver(request, "cotizaciones_list")


@login_required
def cotizaciones_sync(request):
    """Trae las cotizaciones de la API a pedido, desde el botón del CRM."""
    if request.method != "POST":
        return redirect("cotizaciones_list")

    con_historico = request.POST.get("historico") == "1"
    desde = None
    if con_historico:
        desde = Factura.objects.order_by("fecha").values_list("fecha", flat=True).first()

    try:
        resumen = sincronizar(desde=desde, con_historico=con_historico)
    except ErrorCotizaciones as e:
        messages.error(request, f"No se pudo sincronizar: {e}")
        return redirect("cotizaciones_list")

    if resumen["errores"]:
        for error in resumen["errores"]:
            messages.warning(request, error)

    if resumen["creados"] or resumen["actualizados"]:
        messages.success(
            request,
            f"{resumen['creados']} cotización(es) nueva(s) y "
            f"{resumen['actualizados']} actualizada(s). Última: {resumen['ultima']}.",
        )
    elif not resumen["errores"]:
        messages.info(request, f"Ya estaba todo al día (última: {resumen['ultima']}).")

    return redirect("cotizaciones_list")


# ---------------------------------------------------------------- tareas

def _tareas_agrupadas(base=None):
    """
    Pendientes partidos por urgencia. Es la forma en que uno mira la lista:
    primero lo que ya se pasó, después lo de hoy, después el resto.
    """
    hoy = timezone.localdate()
    qs = (base if base is not None else Tarea.objects.all()).filter(completada=False)
    qs = qs.select_related("cliente", "proyecto", "deal", "asignada_a")

    def ordenadas(consulta):
        return sorted(consulta, key=lambda t: (Tarea.PESO_PRIORIDAD.get(t.prioridad, 1), t.vence or hoy, t.id))

    return {
        "vencidas": ordenadas(qs.filter(vence__lt=hoy)),
        "hoy": ordenadas(qs.filter(vence=hoy)),
        "semana": ordenadas(qs.filter(vence__gt=hoy, vence__lte=hoy + timedelta(days=7))),
        "despues": ordenadas(qs.filter(vence__gt=hoy + timedelta(days=7))),
        "sin_fecha": ordenadas(qs.filter(vence__isnull=True)),
    }


@login_required
def tareas_list(request):
    vista = request.GET.get("vista", "pendientes").strip()
    prioridad = request.GET.get("prioridad", "").strip()
    cliente_id = request.GET.get("cliente", "").strip()

    qs = Tarea.objects.select_related("cliente", "proyecto", "deal", "asignada_a")

    if prioridad in dict(Tarea.PRIORIDADES):
        qs = qs.filter(prioridad=prioridad)
    if cliente_id.isdigit():
        qs = qs.filter(cliente_id=int(cliente_id))

    hoy = timezone.localdate()
    completadas = qs.filter(completada=True).order_by("-completada_en")[:50]

    context = {
        "grupos": _tareas_agrupadas(qs),
        "completadas": completadas if vista == "completadas" else [],
        "vista": vista,
        "filters": {"vista": vista, "prioridad": prioridad, "cliente": cliente_id},
        "prioridades": Tarea.PRIORIDADES,
        "clientes": Cliente.objects.all(),
        "form_rapido": TareaRapidaForm(),
        "pendientes": qs.filter(completada=False).count(),
        "vencidas": qs.filter(completada=False, vence__lt=hoy).count(),
        "hoy": hoy,
    }
    return render(request, "tareas_list.html", context)


@login_required
def tarea_create(request):
    if request.method == "POST":
        form = TareaForm(request.POST)
        if form.is_valid():
            tarea = form.save(commit=False)
            if not tarea.asignada_a_id:
                tarea.asignada_a = request.user
            tarea.save()
            messages.success(request, "Tarea creada.")
            return _volver(request, "tareas_list")
    else:
        inicial = {}
        for campo in ("cliente", "proyecto", "deal"):
            valor = request.GET.get(campo)
            if valor and valor.isdigit():
                inicial[campo] = valor
        form = TareaForm(initial=inicial)

    return render(request, "tarea_form.html", {"form": form, "mode": "create"})


@login_required
def tarea_rapida(request):
    """Alta desde el campo de una línea del dashboard o del listado."""
    if request.method == "POST":
        form = TareaRapidaForm(request.POST)
        if form.is_valid():
            tarea = form.save(commit=False)
            tarea.asignada_a = request.user
            for campo in ("cliente", "proyecto", "deal"):
                valor = request.POST.get(campo)
                if valor and valor.isdigit():
                    setattr(tarea, f"{campo}_id", int(valor))
            tarea.save()
            messages.success(request, f"Tarea «{tarea.titulo}» agregada.")
        else:
            messages.error(request, "Falta el título de la tarea.")
    return _volver(request, "tareas_list")


@login_required
def tarea_update(request, pk):
    tarea = get_object_or_404(Tarea, pk=pk)
    if request.method == "POST":
        form = TareaForm(request.POST, instance=tarea)
        if form.is_valid():
            form.save()
            messages.success(request, "Tarea actualizada.")
            return _volver(request, "tareas_list")
    else:
        form = TareaForm(instance=tarea)
    return render(request, "tarea_form.html", {"form": form, "mode": "update", "tarea": tarea})


@login_required
def tarea_toggle(request, pk):
    """Marcar hecha / reabrir. Responde JSON si la llaman por fetch."""
    tarea = get_object_or_404(Tarea, pk=pk)

    if request.method == "POST":
        tarea.completada = not tarea.completada
        tarea.save()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({
                "ok": True,
                "completada": tarea.completada,
                "id": tarea.pk,
            })

        messages.success(request, "Tarea completada." if tarea.completada else "Tarea reabierta.")

    return _volver(request, "tareas_list")


@login_required
def tarea_delete(request, pk):
    tarea = get_object_or_404(Tarea, pk=pk)
    if request.method == "POST":
        tarea.delete()
        messages.success(request, "Tarea eliminada.")
        return _volver(request, "tareas_list")

    return render(request, "confirm_delete.html", {
        "objeto": tarea,
        "titulo": "Eliminar tarea",
        "detalle": f"Vence {tarea.vence or 'sin fecha'}.",
        "cancelar_url": None,
    })


@login_required
def tareas_completar_vencidas(request):
    """Cierra de una todas las vencidas, para cuando se acumularon."""
    if request.method == "POST":
        hoy = timezone.localdate()
        pendientes = Tarea.objects.filter(completada=False, vence__lt=hoy)
        total = pendientes.count()
        for tarea in pendientes:
            tarea.completada = True
            tarea.save()
        messages.success(request, f"{total} tarea(s) vencida(s) marcadas como hechas.")
    return _volver(request, "tareas_list")


# ---------------------------------------------------------------- kanban

def _resumen_columna(deals, cotizaciones):
    """Cuántos deals y cuánto suman, en dólares, para el encabezado de la columna."""
    total, sin_cotizar = Decimal("0"), 0
    for d in deals:
        valor = cotizaciones.a_usd(d.valor, d.moneda, timezone.localdate())
        if valor is None:
            sin_cotizar += 1
        else:
            total += valor
    return {"cantidad": len(deals), "total_usd": total, "sin_cotizar": sin_cotizar}


@login_required
def kanban(request, pk=None):
    """Tablero de un pipeline: una columna por etapa, una tarjeta por deal."""
    pipelines = Pipeline.objects.filter(activo=True)

    if pk:
        pipeline = get_object_or_404(Pipeline, pk=pk)
    else:
        pipeline = pipelines.first() or Pipeline.objects.first()

    if not pipeline:
        return render(request, "kanban.html", {
            "pipelines": pipelines,
            "pipeline": None,
            "columnas": [],
        })

    cotizaciones = Cotizaciones()
    q = request.GET.get("q", "").strip()

    deals = (
        pipeline.deals
        .select_related("cliente", "etapa", "proyecto")
        .prefetch_related("tareas")
        .order_by("orden", "-id")
    )
    if q:
        deals = deals.filter(Q(titulo__icontains=q) | Q(cliente__nombre__icontains=q))

    por_etapa = {}
    for d in deals:
        por_etapa.setdefault(d.etapa_id, []).append(d)

    columnas = []
    for etapa in pipeline.etapas.all():
        de_la_etapa = por_etapa.get(etapa.id, [])
        columnas.append({
            "etapa": etapa,
            "deals": de_la_etapa,
            "resumen": _resumen_columna(de_la_etapa, cotizaciones),
        })

    abiertos = [d for d in deals if d.esta_abierto]
    ganados = [d for d in deals if d.esta_ganado]
    perdidos = [d for d in deals if d.esta_perdido]
    cerrados = len(ganados) + len(perdidos)

    context = {
        "pipelines": pipelines,
        "pipeline": pipeline,
        "columnas": columnas,
        "q": q,
        "form_deal": DealForm(pipeline=pipeline),
        "valor_abierto": _resumen_columna(abiertos, cotizaciones)["total_usd"],
        "valor_ganado": _resumen_columna(ganados, cotizaciones)["total_usd"],
        "abiertos": len(abiertos),
        "ganados": len(ganados),
        "perdidos": len(perdidos),
        # tasa de conversión sobre lo que ya se cerró, que es lo único medible
        "conversion": int(len(ganados) / cerrados * 100) if cerrados else None,
    }
    return render(request, "kanban.html", context)


@login_required
def pipeline_create(request):
    if request.method == "POST":
        form = PipelineForm(request.POST)
        if form.is_valid():
            pipeline = form.save(commit=False)
            pipeline.orden = Pipeline.objects.count()
            pipeline.save()

            if request.POST.get("etapas_por_defecto") == "1":
                pipeline.crear_etapas_por_defecto()
                messages.success(request, f"Pipeline «{pipeline.nombre}» creado con las etapas típicas de ventas.")
            else:
                messages.success(request, f"Pipeline «{pipeline.nombre}» creado. Agregale etapas.")

            return redirect("pipeline_config", pk=pipeline.pk)
    else:
        form = PipelineForm()
    return render(request, "pipeline_form.html", {"form": form, "mode": "create"})


@login_required
def pipeline_config(request, pk):
    """Alta, edición y orden de las etapas del pipeline."""
    pipeline = get_object_or_404(Pipeline, pk=pk)

    if request.method == "POST":
        form = PipelineForm(request.POST, instance=pipeline)
        if form.is_valid():
            form.save()
            messages.success(request, "Pipeline actualizado.")
            return redirect("pipeline_config", pk=pipeline.pk)
    else:
        form = PipelineForm(instance=pipeline)

    etapas = []
    for etapa in pipeline.etapas.all():
        etapas.append({"etapa": etapa, "form": EtapaForm(instance=etapa, prefix=f"e{etapa.id}")})

    return render(request, "pipeline_config.html", {
        "pipeline": pipeline,
        "form": form,
        "etapas": etapas,
        "form_etapa": EtapaForm(),
    })


@login_required
def pipeline_delete(request, pk):
    pipeline = get_object_or_404(Pipeline, pk=pk)
    if request.method == "POST":
        nombre = pipeline.nombre
        pipeline.delete()
        messages.success(request, f"Pipeline «{nombre}» eliminado con sus etapas y deals.")
        return redirect("kanban")

    return render(request, "confirm_delete.html", {
        "objeto": pipeline,
        "titulo": "Eliminar pipeline",
        "detalle": f"Se borran sus {pipeline.etapas.count()} etapa(s) y {pipeline.deals.count()} deal(s).",
        "cancelar_url": None,
    })


@login_required
def etapa_create(request, pipeline_id):
    pipeline = get_object_or_404(Pipeline, pk=pipeline_id)

    if request.method == "POST":
        form = EtapaForm(request.POST)
        if form.is_valid():
            etapa = form.save(commit=False)
            etapa.pipeline = pipeline
            etapa.orden = pipeline.etapas.count()
            etapa.save()
            messages.success(request, f"Etapa «{etapa.nombre}» agregada.")
        else:
            messages.error(request, "Falta el nombre de la etapa.")

    return redirect("pipeline_config", pk=pipeline.pk)


@login_required
def etapa_update(request, pk):
    etapa = get_object_or_404(Etapa, pk=pk)

    if request.method == "POST":
        form = EtapaForm(request.POST, instance=etapa, prefix=f"e{etapa.id}")
        if form.is_valid():
            form.save()
            messages.success(request, "Etapa actualizada.")
        else:
            messages.error(request, "No se pudo actualizar la etapa.")

    return redirect("pipeline_config", pk=etapa.pipeline_id)


@login_required
def etapa_delete(request, pk):
    etapa = get_object_or_404(Etapa, pk=pk)
    pipeline_id = etapa.pipeline_id

    if request.method == "POST":
        # PROTECT en el FK: hay que mover los deals antes de borrar la columna
        if etapa.deals.exists():
            messages.error(
                request,
                f"«{etapa.nombre}» tiene {etapa.deals.count()} deal(s). "
                f"Movelos a otra etapa antes de borrarla.",
            )
        else:
            etapa.delete()
            messages.success(request, "Etapa eliminada.")

    return redirect("pipeline_config", pk=pipeline_id)


@login_required
def etapa_mover(request, pk):
    """Sube o baja la columna en el tablero."""
    etapa = get_object_or_404(Etapa, pk=pk)

    if request.method == "POST":
        direccion = request.POST.get("direccion")
        hermanas = list(etapa.pipeline.etapas.all())
        i = hermanas.index(etapa)
        j = i - 1 if direccion == "izquierda" else i + 1

        if 0 <= j < len(hermanas):
            hermanas[i], hermanas[j] = hermanas[j], hermanas[i]
            for orden, e in enumerate(hermanas):
                Etapa.objects.filter(pk=e.pk).update(orden=orden)

    return redirect("pipeline_config", pk=etapa.pipeline_id)


# ---------------------------------------------------------------- deals

@login_required
def deal_create(request, pipeline_id=None):
    pipeline = None
    if pipeline_id:
        pipeline = get_object_or_404(Pipeline, pk=pipeline_id)

    if request.method == "POST":
        form = DealForm(request.POST, pipeline=pipeline)
        if form.is_valid():
            deal = form.save(commit=False)
            deal.pipeline = pipeline or deal.etapa.pipeline
            deal.orden = deal.etapa.deals.count()
            deal.save()
            messages.success(request, f"Deal «{deal.titulo}» creado.")
            return _volver(request, deal.get_absolute_url())
    else:
        inicial = {}
        etapa_id = request.GET.get("etapa")
        if etapa_id and etapa_id.isdigit():
            inicial["etapa"] = etapa_id
        cliente_id = request.GET.get("cliente")
        if cliente_id and cliente_id.isdigit():
            inicial["cliente"] = cliente_id
        form = DealForm(pipeline=pipeline, initial=inicial)

    return render(request, "deal_form.html", {"form": form, "mode": "create", "pipeline": pipeline})


@login_required
def deal_detail(request, pk):
    deal = get_object_or_404(
        Deal.objects.select_related("cliente", "etapa", "pipeline", "proyecto"), pk=pk
    )
    return render(request, "deal_detail.html", {
        "deal": deal,
        "tareas": deal.tareas.select_related("asignada_a"),
        "form_rapido": TareaRapidaForm(),
        "etapas": deal.pipeline.etapas.all(),
    })


@login_required
def deal_update(request, pk):
    deal = get_object_or_404(Deal, pk=pk)

    if request.method == "POST":
        form = DealForm(request.POST, instance=deal)
        if form.is_valid():
            form.save()
            messages.success(request, "Deal actualizado.")
            return _volver(request, deal.get_absolute_url())
    else:
        form = DealForm(instance=deal)

    return render(request, "deal_form.html", {"form": form, "mode": "update", "deal": deal,
                                              "pipeline": deal.pipeline})


@login_required
def deal_delete(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if request.method == "POST":
        pipeline_id = deal.pipeline_id
        deal.delete()
        messages.success(request, "Deal eliminado.")
        return redirect("kanban", pk=pipeline_id)

    return render(request, "confirm_delete.html", {
        "objeto": deal,
        "titulo": "Eliminar deal",
        "detalle": f"{deal.moneda} {deal.valor} — etapa «{deal.etapa.nombre}».",
        "cancelar_url": deal.get_absolute_url(),
    })


@login_required
@transaction.atomic
def deal_mover(request, pk):
    """
    Mueve la tarjeta a otra etapa y reordena la columna destino.
    La llama el drag & drop del tablero; también funciona por POST normal.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Método no permitido"}, status=405)

    deal = get_object_or_404(Deal.objects.select_for_update(), pk=pk)

    etapa_id = request.POST.get("etapa")
    if not etapa_id or not str(etapa_id).isdigit():
        return JsonResponse({"ok": False, "error": "Falta la etapa"}, status=400)

    etapa = Etapa.objects.filter(pk=int(etapa_id), pipeline=deal.pipeline).first()
    if not etapa:
        return JsonResponse({"ok": False, "error": "Esa etapa no es de este pipeline"}, status=400)

    deal.etapa = etapa
    deal.save()

    # el orden llega como la lista de ids tal cual quedó la columna
    ids = [int(x) for x in request.POST.getlist("orden[]") if str(x).isdigit()]
    if ids:
        for posicion, deal_id in enumerate(ids):
            Deal.objects.filter(pk=deal_id, pipeline=deal.pipeline).update(orden=posicion)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True,
            "etapa": etapa.nombre,
            "cerrado": etapa.es_cierre,
            "ganado": etapa.tipo == "ganada",
            "puede_convertir": etapa.tipo == "ganada" and not deal.proyecto_id and bool(deal.cliente_id),
        })

    messages.success(request, f"«{deal.titulo}» movido a {etapa.nombre}.")
    return _volver(request, deal.pipeline.get_absolute_url())


@login_required
def deal_convertir(request, pk):
    """Del deal ganado sale el proyecto, sin recargar los datos a mano."""
    deal = get_object_or_404(Deal, pk=pk)

    if request.method == "POST":
        if deal.proyecto_id:
            messages.warning(request, "Este deal ya tiene un proyecto asociado.")
        elif not deal.cliente_id:
            messages.error(request, "Asignale un cliente al deal antes de convertirlo en proyecto.")
        else:
            proyecto = deal.convertir_en_proyecto()
            messages.success(request, f"Proyecto «{proyecto.nombre}» creado desde el deal.")
            return redirect("proyecto_detail", pk=proyecto.pk)

    return redirect("deal_detail", pk=deal.pk)
