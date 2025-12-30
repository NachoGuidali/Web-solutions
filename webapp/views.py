from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import Cliente, Proyecto, Factura
from django.contrib import messages
from django.db import models
from django.db.models import Q
from .forms import ProyectoForm, ClienteForm, FacturaForm

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
    total_facturado_usd = Factura.objects.filter(moneda='USD').aggregate(models.Sum('monto'))['monto__sum'] or 0
    context = {
        'total_clientes': total_clientes,
        'proyectos_activos': proyectos_activos,
        'total_facturado_ars': total_facturado_ars,
        'total_facturado_usd': total_facturado_usd,
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
        messages.success(request, "Factura marcada como cobrada.")
    return redirect("facturas_list")