from django import forms
from .models import Proyecto, Cliente, Factura,ProyectoMensualidad, Evento

class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = [
            'cliente','nombre','descripcion','fecha_inicio','fecha_entrega','estado',
            'moneda',          
            'precio','monto_cobrado','observaciones'
        ]
        widgets = {
            'fecha_inicio': forms.DateInput(attrs={'type': 'date'}),
            'fecha_entrega': forms.DateInput(attrs={'type': 'date'}),
        }

class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['nombre', 'email', 'telefono', 'empresa', 'estado', 'notas']



class FacturaForm(forms.ModelForm):
    class Meta:
        model = Factura
        fields = ["proyecto","fecha","monto","moneda","estado","metodo_pago","observaciones"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01"}),
            "metodo_pago": forms.TextInput(attrs={"placeholder": "Ej: Transferencia / Efectivo / MP"}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }


class ProyectoMensualidadForm(forms.ModelForm):
    class Meta:
        model = ProyectoMensualidad
        fields = ["proyecto", "activa", "monto", "moneda", "dia_vencimiento", "dias_antes_recordatorio"]
        widgets = {
            "dia_vencimiento": forms.NumberInput(attrs={"min": 1, "max": 28}),
            "dias_antes_recordatorio": forms.NumberInput(attrs={"min": 0, "max": 30}),
            "monto": forms.NumberInput(attrs={"step": "0.01"}),
        }

class EventoForm(forms.ModelForm):
    class Meta:
        model = Evento
        fields = ["titulo","tipo","cliente","proyecto","inicio","fin","descripcion","recordatorio_minutos_antes"]
        widgets = {
            "inicio": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "fin": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "recordatorio_minutos_antes": forms.NumberInput(attrs={"min": 0}),
        }
