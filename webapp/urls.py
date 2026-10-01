from django.urls import path

from . import views

urlpatterns = [
    # --- sitio público ---
    path('', views.home, name='home'),
    path('servicios', views.servicios, name='servicios'),
    path('crm', views.crm, name='crm'),
    path('comanda', views.comanda, name='comanda'),
    path('about', views.about, name='about'),
    path('politica-de-privacidad', views.privacidad, name='privacidad'),
    path('terminos-y-condiciones', views.terminos, name='terminos'),

    # --- dashboard ---
    path('dashboard/', views.dashboard, name='dashboard'),

    # --- clientes ---
    path('clientes/', views.clientes_list, name='clientes_list'),
    path('clientes/nuevo/', views.nuevo_cliente, name='nuevo_cliente'),
    path('clientes/exportar/', views.clientes_export, name='clientes_export'),
    path('clientes/<int:pk>/', views.cliente_detail, name='cliente_detail'),
    path('clientes/<int:pk>/editar/', views.cliente_update, name='cliente_update'),
    path('clientes/<int:pk>/eliminar/', views.cliente_delete, name='cliente_delete'),

    # --- interacciones (bitácora del cliente) ---
    path('clientes/<int:cliente_id>/interaccion/', views.interaccion_create, name='interaccion_create'),
    path('interacciones/<int:pk>/eliminar/', views.interaccion_delete, name='interaccion_delete'),

    # --- proyectos ---
    path('proyectos/', views.proyectos_list, name='proyectos_list'),
    path('proyectos/nuevo/', views.nuevo_proyecto, name='nuevo_proyecto'),
    path('proyectos/<int:pk>/', views.proyecto_detail, name='proyecto_detail'),
    path('proyectos/editar/<int:id>/', views.editar_proyecto, name='editar_proyecto'),
    path('proyectos/<int:pk>/eliminar/', views.proyecto_delete, name='proyecto_delete'),
    path('proyectos/<int:pk>/estado/', views.proyecto_cambiar_estado, name='proyecto_cambiar_estado'),

    # --- facturas ---
    path("dashboard/facturas/", views.facturas_list, name="facturas_list"),
    path("dashboard/facturas/nueva/", views.factura_create, name="factura_create"),
    path("dashboard/facturas/<int:pk>/editar/", views.factura_update, name="factura_update"),
    path("dashboard/facturas/<int:pk>/eliminar/", views.factura_delete, name="factura_delete"),
    path("dashboard/facturas/<int:pk>/cobrada/", views.factura_mark_cobrada, name="factura_mark_cobrada"),
    path("dashboard/facturas/<int:pk>/pendiente/", views.factura_mark_pendiente, name="factura_mark_pendiente"),

    # --- mensualidades ---
    path("dashboard/mensualidades/", views.mensualidades_list, name="mensualidades_list"),
    path("dashboard/mensualidades/nueva/", views.mensualidad_create, name="mensualidad_create"),
    path("dashboard/mensualidades/<int:pk>/editar/", views.mensualidad_update, name="mensualidad_update"),
    path("dashboard/mensualidades/<int:pk>/eliminar/", views.mensualidad_delete, name="mensualidad_delete"),
    path("dashboard/mensualidades/<int:pk>/toggle/", views.mensualidad_toggle, name="mensualidad_toggle"),
    path("dashboard/mensualidades/<int:pk>/facturar/", views.mensualidad_facturar, name="mensualidad_facturar"),

    # --- tareas ---
    path("dashboard/tareas/", views.tareas_list, name="tareas_list"),
    path("dashboard/tareas/nueva/", views.tarea_create, name="tarea_create"),
    path("dashboard/tareas/rapida/", views.tarea_rapida, name="tarea_rapida"),
    path("dashboard/tareas/completar-vencidas/", views.tareas_completar_vencidas, name="tareas_completar_vencidas"),
    path("dashboard/tareas/<int:pk>/editar/", views.tarea_update, name="tarea_update"),
    path("dashboard/tareas/<int:pk>/toggle/", views.tarea_toggle, name="tarea_toggle"),
    path("dashboard/tareas/<int:pk>/eliminar/", views.tarea_delete, name="tarea_delete"),

    # --- kanban / pipelines ---
    path("dashboard/kanban/", views.kanban, name="kanban"),
    path("dashboard/kanban/<int:pk>/", views.kanban, name="kanban"),
    path("dashboard/kanban/pipeline/nuevo/", views.pipeline_create, name="pipeline_create"),
    path("dashboard/kanban/pipeline/<int:pk>/config/", views.pipeline_config, name="pipeline_config"),
    path("dashboard/kanban/pipeline/<int:pk>/eliminar/", views.pipeline_delete, name="pipeline_delete"),
    path("dashboard/kanban/pipeline/<int:pipeline_id>/etapa/nueva/", views.etapa_create, name="etapa_create"),
    path("dashboard/kanban/etapa/<int:pk>/editar/", views.etapa_update, name="etapa_update"),
    path("dashboard/kanban/etapa/<int:pk>/mover/", views.etapa_mover, name="etapa_mover"),
    path("dashboard/kanban/etapa/<int:pk>/eliminar/", views.etapa_delete, name="etapa_delete"),

    # --- deals ---
    path("dashboard/deals/nuevo/", views.deal_create, name="deal_create"),
    path("dashboard/kanban/<int:pipeline_id>/deal/nuevo/", views.deal_create, name="deal_create_pipeline"),
    path("dashboard/deals/<int:pk>/", views.deal_detail, name="deal_detail"),
    path("dashboard/deals/<int:pk>/editar/", views.deal_update, name="deal_update"),
    path("dashboard/deals/<int:pk>/mover/", views.deal_mover, name="deal_mover"),
    path("dashboard/deals/<int:pk>/convertir/", views.deal_convertir, name="deal_convertir"),
    path("dashboard/deals/<int:pk>/eliminar/", views.deal_delete, name="deal_delete"),

    # --- cotizaciones ---
    path("dashboard/cotizaciones/", views.cotizaciones_list, name="cotizaciones_list"),
    path("dashboard/cotizaciones/nueva/", views.cotizacion_create, name="cotizacion_create"),
    path("dashboard/cotizaciones/sincronizar/", views.cotizaciones_sync, name="cotizaciones_sync"),
    path("dashboard/cotizaciones/<int:pk>/eliminar/", views.cotizacion_delete, name="cotizacion_delete"),

    # --- agenda ---
    path("dashboard/agenda/", views.eventos_list, name="eventos_list"),
    path("dashboard/agenda/nuevo/", views.evento_create, name="evento_create"),
    path("dashboard/agenda/<int:pk>/editar/", views.evento_update, name="evento_update"),
    path("dashboard/agenda/<int:pk>/eliminar/", views.evento_delete, name="evento_delete"),
    path("dashboard/agenda/<int:pk>/toggle/", views.evento_toggle, name="evento_toggle"),
]
