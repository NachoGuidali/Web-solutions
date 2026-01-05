from django.urls import path
from .views import (
    home, servicios, about, dashboard,
    clientes_list, proyectos_list, editar_proyecto, nuevo_cliente, nuevo_proyecto,
    facturas_list, factura_create, factura_update, factura_delete, factura_mark_cobrada,
    mensualidades_list, mensualidad_create, mensualidad_update, mensualidad_delete,
    eventos_list, evento_create, evento_update, evento_delete,
)

urlpatterns = [
    path('', home, name='home'),
    path('servicios', servicios, name='servicios'),
    path('about', about, name='about'),

    path('dashboard/', dashboard, name='dashboard'),

    path('clientes/', clientes_list, name='clientes_list'),
    path('clientes/nuevo/', nuevo_cliente, name='nuevo_cliente'),

    path('proyectos/', proyectos_list, name='proyectos_list'),
    path('proyectos/nuevo/', nuevo_proyecto, name='nuevo_proyecto'),
    path('proyectos/editar/<int:id>/', editar_proyecto, name='editar_proyecto'),

    path("dashboard/facturas/", facturas_list, name="facturas_list"),
    path("dashboard/facturas/nueva/", factura_create, name="factura_create"),
    path("dashboard/facturas/<int:pk>/editar/", factura_update, name="factura_update"),
    path("dashboard/facturas/<int:pk>/eliminar/", factura_delete, name="factura_delete"),
    path("dashboard/facturas/<int:pk>/cobrada/", factura_mark_cobrada, name="factura_mark_cobrada"),

    path("dashboard/mensualidades/", mensualidades_list, name="mensualidades_list"),
    path("dashboard/mensualidades/nueva/", mensualidad_create, name="mensualidad_create"),
    path("dashboard/mensualidades/<int:pk>/editar/", mensualidad_update, name="mensualidad_update"),
    path("dashboard/mensualidades/<int:pk>/eliminar/", mensualidad_delete, name="mensualidad_delete"),

    path("dashboard/agenda/", eventos_list, name="eventos_list"),
    path("dashboard/agenda/nuevo/", evento_create, name="evento_create"),
    path("dashboard/agenda/<int:pk>/editar/", evento_update, name="evento_update"),
    path("dashboard/agenda/<int:pk>/eliminar/", evento_delete, name="evento_delete"),
]
