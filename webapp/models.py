from django.db import models
from django.contrib.auth.models import User
# Create your models here.


class Cliente(models.Model):
    nombre = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    empresa = models.CharField(max_length=100, blank=True, null=True)
    estado = models.CharField(max_length=20, choices=[('activo', 'Activo'), ('potencial', 'Potencial'), ('inactivo', 'Inactivo')])
    notas = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class Proyecto(models.Model):
    MONEDAS = [
        ('ARS', 'Pesos Argentinos'),
        ('USD', 'Dólares Americanos'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="proyectos")
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    fecha_inicio = models.DateField()
    fecha_entrega = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=[
        ('pendiente', 'Pendiente'),
        ('en_curso', 'En curso'),
        ('pausado', 'Pausado'),
        ('terminado', 'Terminado'),
        ('entregado', 'Entregado')
    ])
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='ARS')  # NUEVO
    precio = models.DecimalField(max_digits=12, decimal_places=2)  # total presupuestado
    monto_cobrado = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # total facturado
    observaciones = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.cliente.nombre})"

    def simbolo_moneda(self):
        return "$" if self.moneda == 'ARS' else "USD"
    
class Factura(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE)
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    moneda = models.CharField(
        max_length=3,
        choices=[
            ('ARS', 'Pesos'),
            ('USD', 'Dólares'),
        ],
        default='ARS'
    )
    estado = models.CharField(max_length=20, choices=[('pendiente', 'Pendiente'), ('cobrado', 'Cobrado')])
    metodo_pago = models.CharField(max_length=50, blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.get_moneda_display()} {self.monto} - {self.proyecto.nombre}"