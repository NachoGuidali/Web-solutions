from django.urls import path
from .views import home, servicios, about, dashboard, clientes_list, proyectos_list, editar_proyecto, nuevo_cliente, nuevo_proyecto, factura_create, factura_delete, factura_mark_cobrada, factura_update, facturas_list

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
]