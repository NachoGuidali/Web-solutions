from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import Cliente, Proyecto, Factura, ProyectoMensualidad, Evento
from django.contrib import messages
from django.db import models
from django.db.models import Q
from .forms import ProyectoForm, ClienteForm, FacturaForm, ProyectoMensualidadForm, EventoForm
from django.utils import timezone
from datetime import date, timedelta

# Create your views here.

def home(request):
    context = {}
    return render(request, 'home.html', context)

def servicios(request):
    return render(request, 'servicios.html')

def about(request):
    return render(request, 'about.html')


@login_required
def dashboard(request):
    total_clientes = Cliente.objects.count()
    proyectos_activos = Proyecto.objects.filter(estado__in=['pendiente', 'en_curso']).count()

    total_facturado_ars = Factura.objects.filter(moneda='ARS', estado='cobrado').aggregate(models.Sum('monto'))['monto__sum'] or 0
    total_facturado_usd = Factura.objects.filter(moneda='USD', estado='cobrado').aggregate(models.Sum('monto'))['monto__sum'] or 0

    hoy = timezone.localdate()
    ahora = timezone.localtime()

    vencimientos = (
        ProyectoMensualidad.objects
        .select_related("proyecto", "proyecto__cliente")
        .filter(activa=True, proximo_vencimiento__lte=hoy + timedelta(days=14))
        .order_by("proximo_vencimiento")
    )

    eventos = (
        Evento.objects
        .select_related("cliente", "proyecto")
        .filter(inicio__gte=ahora, inicio__lte=ahora + timedelta(days=7))
        .order_by("inicio")
    )

    facturas_pendientes = Factura.objects.filter(estado="pendiente").count()

    context = {
        'total_clientes': total_clientes,
        'proyectos_activos': proyectos_activos,
        'total_facturado_ars': total_facturado_ars,
        'total_facturado_usd': total_facturado_usd,
        'vencimientos': vencimientos,
        'eventos': eventos,
        'facturas_pendientes': facturas_pendientes,
    }
    return render(request, 'dashboard.html', context)


@login_required
def clientes_list(request):
    clientes = Cliente.objects.all()
    return render(request, 'clientes_list.html', {'clientes': clientes})

@login_required
def proyectos_list(request):
    proyectos = Proyecto.objects.all()
    return render(request, 'proyectos_list.html', {'proyectos': proyectos})


@login_required
def nuevo_proyecto(request):
    if request.method == 'POST':
        form = ProyectoForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('proyectos_list')
    else:
        form = ProyectoForm()
    return render(request, 'nuevo_proyecto.html', {'form': form})


@login_required
def nuevo_cliente(request):
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('clientes_list')
    else:
        form = ClienteForm()
    return render(request, 'nuevo_cliente.html', {'form': form})


@login_required
def editar_proyecto(request, id):
    proyecto = get_object_or_404(Proyecto, id=id)
    if request.method == 'POST':
        form = ProyectoForm(request.POST, instance=proyecto)
        if form.is_valid():
            form.save()
            return redirect('proyectos_list')
    else:
        form = ProyectoForm(instance=proyecto)
    return render(request, 'editar_proyecto.html', {'form': form, 'proyecto': proyecto})


@login_required
def facturas_list(request):
    """
    Lista + filtros básicos (search, estado, moneda, proyecto).
    """
    qs = Factura.objects.select_related("proyecto").order_by("-fecha", "-id")

    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    moneda = request.GET.get("moneda", "").strip()
    proyecto_id = request.GET.get("proyecto", "").strip()

    if q:
        qs = qs.filter(
            Q(proyecto__nombre__icontains=q)
            | Q(metodo_pago__icontains=q)
            | Q(observaciones__icontains=q)
        )

    if estado in ("pendiente", "cobrado"):
        qs = qs.filter(estado=estado)

    if moneda in ("ARS", "USD"):
        qs = qs.filter(moneda=moneda)

    if proyecto_id.isdigit():
        qs = qs.filter(proyecto_id=int(proyecto_id))

    context = {
        "facturas": qs,
        "filters": {"q": q, "estado": estado, "moneda": moneda, "proyecto": proyecto_id},
    }
    return render(request, "facturas_list.html", context)


@login_required
def factura_create(request):
    if request.method == "POST":
        form = FacturaForm(request.POST)
        if form.is_valid():
            factura = form.save()
            messages.success(request, f"Factura creada (ID {factura.id}).")
            return redirect("facturas_list")
    else:
        form = FacturaForm()

    return render(request, "factura_form.html", {"form": form, "mode": "create"})


@login_required
def factura_update(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        form = FacturaForm(request.POST, instance=factura)
        if form.is_valid():
            form.save()
            messages.success(request, "Factura actualizada.")
            return redirect("facturas_list")
    else:
        form = FacturaForm(instance=factura)

    return render(
        request,
        "factura_form.html",
        {"form": form, "mode": "update", "factura": factura},
    )


@login_required
def factura_delete(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        factura.delete()
        messages.success(request, "Factura eliminada.")
        return redirect("facturas_list")

    return render(request, "factura_confirm_delete.html", {"factura": factura})


@login_required
def factura_mark_cobrada(request, pk):
    factura = get_object_or_404(Factura, pk=pk)

    if request.method == "POST":
        factura.estado = "cobrado"
        factura.save(update_fields=["estado"])

        # recalcular cobrado real del proyecto (solo cobradas)
        proyecto = factura.proyecto
        total = Factura.objects.filter(proyecto=proyecto, estado="cobrado").aggregate(models.Sum("monto"))["monto__sum"] or 0
        proyecto.monto_cobrado = total
        proyecto.save(update_fields=["monto_cobrado"])

        messages.success(request, "Factura marcada como cobrada.")
    return redirect("facturas_list")

@login_required
def mensualidades_list(request):
    mensualidades = ProyectoMensualidad.objects.select_related("proyecto", "proyecto__cliente").order_by("proximo_vencimiento")
    return render(request, "mensualidades_list.html", {"mensualidades": mensualidades})

@login_required
def mensualidad_create(request):
    if request.method == "POST":
        form = ProyectoMensualidadForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Mensualidad creada.")
            return redirect("mensualidades_list")
    else:
        form = ProyectoMensualidadForm()
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
    return render(request, "mensualidad_confirm_delete.html", {"mensualidad": obj})

@login_required
def eventos_list(request):
    ahora = timezone.localtime()
    eventos = Evento.objects.select_related("cliente", "proyecto").order_by("-inicio")
    return render(request, "eventos_list.html", {"eventos": eventos, "ahora": ahora})

@login_required
def evento_create(request):
    if request.method == "POST":
        form = EventoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Evento creado.")
            return redirect("eventos_list")
    else:
        form = EventoForm()
    return render(request, "evento_form.html", {"form": form, "mode": "create"})

@login_required
def evento_update(request, pk):
    obj = get_object_or_404(Evento, pk=pk)
    if request.method == "POST":
        form = EventoForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Evento actualizado.")
            return redirect("eventos_list")
    else:
        form = EventoForm(instance=obj)
    return render(request, "evento_form.html", {"form": form, "mode": "update", "evento": obj})

@login_required
def evento_delete(request, pk):
    obj = get_object_or_404(Evento, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Evento eliminado.")
        return redirect("eventos_list")
    return render(request, "evento_confirm_delete.html", {"evento": obj})
