from django import forms
from django.utils import timezone

from django.conf import settings
from datetime import timedelta

from .models import (
    Cliente, Deal, Etapa, Evento, Factura, Interaccion, Pipeline,
    Proyecto, ProyectoMensualidad, Tarea, TipoCambio,
)

# clases base para que todos los widgets calcen con la UI del CRM
INPUT = (
    "w-full rounded-xl px-3 py-2 bg-white text-black border border-white/10 "
    "outline-none focus:ring-2 focus:ring-indigo-400"
)
CHECK = "h-5 w-5 rounded accent-indigo-500"


class TailwindFormMixin:
    """Aplica las clases de Tailwind a todos los campos sin repetirlas widget por widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", CHECK)
            elif isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            else:
                existing = widget.attrs.get("class", "")
                widget.attrs["class"] = f"{INPUT} {existing}".strip()


class ProyectoForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = [
            'cliente', 'nombre', 'descripcion', 'fecha_inicio', 'fecha_entrega', 'estado',
            'moneda', 'precio', 'observaciones',
        ]
        widgets = {
            'fecha_inicio': forms.DateInput(attrs={'type': 'date'}),
            'fecha_entrega': forms.DateInput(attrs={'type': 'date'}),
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'observaciones': forms.Textarea(attrs={'rows': 3}),
            'precio': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get('fecha_inicio')
        entrega = cleaned.get('fecha_entrega')
        if inicio and entrega and entrega < inicio:
            self.add_error('fecha_entrega', "La entrega no puede ser anterior al inicio.")
        return cleaned


class ClienteForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['nombre', 'email', 'telefono', 'empresa', 'estado', 'notas']
        widgets = {
            'notas': forms.Textarea(attrs={'rows': 4}),
            'telefono': forms.TextInput(attrs={'placeholder': '+54 9 11 ...'}),
        }


class FacturaForm(TailwindFormMixin, forms.ModelForm):
    # una factura en otra moneda no suma al cobrado del proyecto; pedimos
    # confirmación explícita en vez de aceptarlo en silencio
    permitir_otra_moneda = forms.BooleanField(
        required=False,
        label="Facturar en una moneda distinta a la del proyecto",
        help_text="Marcalo sólo si es a propósito: esta factura no va a sumar a lo cobrado del proyecto.",
    )

    class Meta:
        model = Factura
        fields = [
            "proyecto", "fecha", "fecha_vencimiento", "monto", "moneda",
            "estado", "metodo_pago", "observaciones",
        ]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01"}),
            "metodo_pago": forms.TextInput(attrs={"placeholder": "Ej: Transferencia / Efectivo / MP"}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].queryset = Proyecto.objects.select_related("cliente")
        self.fields["fecha_vencimiento"].help_text = (
            "Si lo dejás vacío se completa solo, "
            f"{settings.CRM_DIAS_VENCIMIENTO_FACTURA} días después de la emisión."
        )

    def clean_monto(self):
        monto = self.cleaned_data["monto"]
        if monto is not None and monto <= 0:
            raise forms.ValidationError("El monto tiene que ser mayor a cero.")
        return monto

    def clean(self):
        cleaned = super().clean()
        fecha = cleaned.get("fecha")
        proyecto = cleaned.get("proyecto")
        moneda = cleaned.get("moneda")

        # vencimiento por defecto: no queremos facturas sin fecha de corte,
        # porque el panel de vencidas depende de ese dato
        if fecha and not cleaned.get("fecha_vencimiento"):
            cleaned["fecha_vencimiento"] = fecha + timedelta(
                days=settings.CRM_DIAS_VENCIMIENTO_FACTURA
            )

        # casi siempre el que está mal cargado es el proyecto, no la factura
        if proyecto and moneda and moneda != proyecto.moneda:
            if not cleaned.get("permitir_otra_moneda"):
                self.add_error(
                    "moneda",
                    f"El proyecto «{proyecto.nombre}» está en {proyecto.moneda}, y esta factura "
                    f"en {moneda} no va a sumar a lo cobrado. Si el proyecto es el que está mal, "
                    f"cambiale la moneda; si es a propósito, marcá la casilla de abajo.",
                )

        return cleaned


class ProyectoMensualidadForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ProyectoMensualidad
        fields = [
            "proyecto", "activa", "monto", "moneda", "dia_vencimiento",
            "dias_antes_recordatorio", "generar_factura_automatica",
        ]
        widgets = {
            "dia_vencimiento": forms.NumberInput(attrs={"min": 1, "max": 28}),
            "dias_antes_recordatorio": forms.NumberInput(attrs={"min": 0, "max": 30}),
            "monto": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # un proyecto sólo puede tener una mensualidad (OneToOne)
        ocupados = ProyectoMensualidad.objects.values_list("proyecto_id", flat=True)
        if self.instance.pk:
            ocupados = ocupados.exclude(pk=self.instance.pk)
        self.fields["proyecto"].queryset = (
            Proyecto.objects.select_related("cliente").exclude(id__in=list(ocupados))
        )
        self.fields["generar_factura_automatica"].help_text = (
            "Emite la factura pendiente sola el día del vencimiento."
        )


class EventoForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Evento
        fields = [
            "titulo", "tipo", "cliente", "proyecto", "inicio", "fin",
            "ubicacion", "descripcion", "recordatorio_minutos_antes", "completado",
        ]
        widgets = {
            "inicio": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "fin": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "ubicacion": forms.TextInput(attrs={"placeholder": "https://meet.google.com/... o dirección"}),
            "recordatorio_minutos_antes": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ("inicio", "fin"):
            self.fields[campo].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
        self.fields["proyecto"].queryset = Proyecto.objects.select_related("cliente")

    def clean(self):
        cleaned = super().clean()
        inicio, fin = cleaned.get("inicio"), cleaned.get("fin")
        if inicio and fin and fin < inicio:
            self.add_error("fin", "El fin no puede ser anterior al inicio.")
        return cleaned


class InteraccionForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Interaccion
        fields = ["cliente", "proyecto", "tipo", "fecha", "detalle"]
        widgets = {
            "fecha": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "detalle": forms.Textarea(attrs={"rows": 3, "placeholder": "Qué se habló, qué quedó pendiente..."}),
        }

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
        self.fields["fecha"].initial = timezone.localtime()

        if cliente is not None:
            self.fields["cliente"].initial = cliente
            self.fields["cliente"].widget = forms.HiddenInput()
            self.fields["proyecto"].queryset = cliente.proyectos.all()
        else:
            self.fields["proyecto"].queryset = Proyecto.objects.select_related("cliente")


class TipoCambioForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = TipoCambio
        fields = ["fecha", "casa", "compra", "venta"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "compra": forms.NumberInput(attrs={"step": "0.01"}),
            "venta": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha"].initial = timezone.localdate()
        self.fields["venta"].label = "Venta (la que se usa para convertir)"

    def clean_venta(self):
        venta = self.cleaned_data["venta"]
        if venta is not None and venta <= 0:
            raise forms.ValidationError("La cotización tiene que ser mayor a cero.")
        return venta


class TareaForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Tarea
        fields = ["titulo", "detalle", "cliente", "proyecto", "deal",
                  "vence", "prioridad", "asignada_a", "completada"]
        widgets = {
            "vence": forms.DateInput(attrs={"type": "date"}),
            "detalle": forms.Textarea(attrs={"rows": 3}),
            "titulo": forms.TextInput(attrs={"placeholder": "Qué hay que hacer"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].queryset = Proyecto.objects.select_related("cliente")
        self.fields["deal"].queryset = Deal.objects.select_related("cliente")
        self.fields["deal"].label = "Oportunidad"
        for campo in ("cliente", "proyecto", "deal", "asignada_a"):
            self.fields[campo].required = False


class TareaRapidaForm(forms.ModelForm):
    """Alta en una línea, para el panel del dashboard."""

    class Meta:
        model = Tarea
        fields = ["titulo", "vence", "prioridad"]
        widgets = {
            "titulo": forms.TextInput(attrs={
                "placeholder": "Nueva tarea...",
                "class": "flex-1 min-w-0 rounded-xl px-3 py-2 bg-white text-black outline-none "
                         "focus:ring-2 focus:ring-indigo-400 text-sm",
            }),
            "vence": forms.DateInput(attrs={
                "type": "date",
                "class": "rounded-xl px-3 py-2 bg-white text-black outline-none text-sm",
            }),
            "prioridad": forms.Select(attrs={
                "class": "rounded-xl px-3 py-2 bg-white text-black outline-none text-sm",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vence"].required = False
        self.fields["vence"].initial = timezone.localdate()


class PipelineForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Pipeline
        fields = ["nombre", "descripcion", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Ej: Ventas, Onboarding, Soporte"}),
            "descripcion": forms.Textarea(attrs={"rows": 2}),
        }


class EtapaForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Etapa
        fields = ["nombre", "color", "tipo"]
        widgets = {"nombre": forms.TextInput(attrs={"placeholder": "Ej: Propuesta enviada"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo"].help_text = (
            "«Ganada» y «Perdida» cierran el deal y le ponen fecha de cierre."
        )


class DealForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Deal
        fields = ["titulo", "cliente", "etapa", "valor", "moneda",
                  "cierre_estimado", "descripcion", "motivo_cierre"]
        widgets = {
            "titulo": forms.TextInput(attrs={"placeholder": "Ej: Rediseño del sitio de ACME"}),
            "cierre_estimado": forms.DateInput(attrs={"type": "date"}),
            "valor": forms.NumberInput(attrs={"step": "0.01"}),
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "motivo_cierre": forms.Textarea(attrs={
                "rows": 2, "placeholder": "Por qué se ganó o se perdió (opcional)",
            }),
        }

    def __init__(self, *args, pipeline=None, **kwargs):
        super().__init__(*args, **kwargs)

        pipeline = pipeline or (self.instance.pipeline if self.instance.pk else None)
        if pipeline:
            self.fields["etapa"].queryset = pipeline.etapas.all()
        else:
            self.fields["etapa"].queryset = Etapa.objects.select_related("pipeline")

        self.fields["cliente"].required = False
        self.fields["cliente"].help_text = "Hace falta para poder convertir el deal en proyecto."

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor is not None and valor < 0:
            raise forms.ValidationError("El valor no puede ser negativo.")
        return valor
