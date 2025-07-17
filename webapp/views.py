from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import Cliente, Proyecto, Factura
from django.db import models
from .forms import ProyectoForm, ClienteForm

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
