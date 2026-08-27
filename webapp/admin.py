from django.contrib import admin

from .models import (
    Cliente, Deal, Etapa, Evento, Factura, Interaccion, Pipeline,
    Proyecto, ProyectoMensualidad, Tarea, TipoCambio,
)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "email", "telefono", "estado")
    list_filter = ("estado",)
    search_fields = ("nombre", "empresa", "email", "telefono")


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "cliente", "estado", "moneda", "precio", "monto_cobrado", "fecha_entrega")
    list_filter = ("estado", "moneda")
    search_fields = ("nombre", "cliente__nombre")
    date_hierarchy = "fecha_inicio"
    autocomplete_fields = ("cliente",)


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ("id", "proyecto", "fecha", "fecha_vencimiento", "moneda", "monto", "tipo_cambio", "estado", "automatica")
    list_filter = ("estado", "moneda", "automatica")
    search_fields = ("proyecto__nombre", "proyecto__cliente__nombre", "metodo_pago")
    date_hierarchy = "fecha"
    autocomplete_fields = ("proyecto",)


@admin.register(ProyectoMensualidad)
class ProyectoMensualidadAdmin(admin.ModelAdmin):
    list_display = ("proyecto", "activa", "moneda", "monto", "dia_vencimiento", "proximo_vencimiento", "ultimo_periodo_facturado")
    list_filter = ("activa", "moneda", "generar_factura_automatica")
    search_fields = ("proyecto__nombre", "proyecto__cliente__nombre")
    autocomplete_fields = ("proyecto",)


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "tipo", "inicio", "cliente", "proyecto", "completado")
    list_filter = ("tipo", "completado")
    search_fields = ("titulo", "cliente__nombre", "proyecto__nombre")
    date_hierarchy = "inicio"


@admin.register(Interaccion)
class InteraccionAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "cliente", "proyecto", "autor")
    list_filter = ("tipo",)
    search_fields = ("cliente__nombre", "detalle")
    date_hierarchy = "fecha"


@admin.register(TipoCambio)
class TipoCambioAdmin(admin.ModelAdmin):
    list_display = ("fecha", "casa", "compra", "venta", "fuente")
    list_filter = ("casa", "fuente")
    date_hierarchy = "fecha"


@admin.register(Tarea)
class TareaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "vence", "prioridad", "completada", "cliente", "proyecto")
    list_filter = ("completada", "prioridad")
    search_fields = ("titulo", "detalle", "cliente__nombre", "proyecto__nombre")
    date_hierarchy = "vence"


class EtapaInline(admin.TabularInline):
    model = Etapa
    extra = 1


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo", "orden")
    list_filter = ("activo",)
    inlines = [EtapaInline]


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("titulo", "pipeline", "etapa", "cliente", "moneda", "valor", "cierre_estimado")
    list_filter = ("pipeline", "etapa", "moneda")
    search_fields = ("titulo", "cliente__nombre", "descripcion")
    autocomplete_fields = ("cliente",)
