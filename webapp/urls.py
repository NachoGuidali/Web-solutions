from django.urls import path
from .views import home, servicios, about, dashboard, clientes_list, proyectos_list, editar_proyecto, nuevo_cliente, nuevo_proyecto

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
]