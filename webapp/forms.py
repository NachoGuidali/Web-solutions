from django import forms
from .models import Proyecto, Cliente, Factura

class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['cliente', 'nombre', 'descripcion', 'fecha_inicio', 'fecha_entrega', 'estado', 'precio', 'monto_cobrado', 'observaciones']
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
        fields = ['monto', 'moneda', 'observaciones']
