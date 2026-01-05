from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, timedelta
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
    

class ProyectoMensualidad(models.Model):
    """
    Configuración de mensualidad para un proyecto.
    """
    proyecto = models.OneToOneField("Proyecto", on_delete=models.CASCADE, related_name="mensualidad")

    activa = models.BooleanField(default=True)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    moneda = models.CharField(max_length=3, choices=Proyecto.MONEDAS, default="ARS")

    # vencimiento mensual (día 1-28 para evitar líos con febrero)
    dia_vencimiento = models.PositiveSmallIntegerField(default=1)
    dias_antes_recordatorio = models.PositiveSmallIntegerField(default=5)

    # control para no duplicar envíos / facturas
    proximo_vencimiento = models.DateField(blank=True, null=True)
    ultimo_recordatorio_enviado = models.DateField(blank=True, null=True)
    ultimo_periodo_facturado = models.DateField(blank=True, null=True)  # guardamos "1er día del mes" del período

    def __str__(self):
        return f"Mensualidad {self.proyecto.nombre} ({self.moneda} {self.monto})"

    def calcular_proximo_vencimiento(self, base=None):
        """
        Devuelve la próxima fecha de vencimiento a partir de 'base' (date).
        """
        if base is None:
            base = timezone.localdate()

        # setear vencimiento este mes
        y, m = base.year, base.month
        d = min(int(self.dia_vencimiento), 28)
        candidato = date(y, m, d)

        # si ya pasó, vamos al mes siguiente
        if candidato < base:
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
            candidato = date(y, m, d)

        return candidato

    def save(self, *args, **kwargs):
        if not self.proximo_vencimiento:
            self.proximo_vencimiento = self.calcular_proximo_vencimiento()
        super().save(*args, **kwargs)    


class Evento(models.Model):
    TIPOS = [
        ("meet", "Meet"),
        ("llamada", "Llamada"),
        ("entrega", "Entrega"),
        ("otro", "Otro"),
    ]

    titulo = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPOS, default="meet")

    cliente = models.ForeignKey("Cliente", on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos")
    proyecto = models.ForeignKey("Proyecto", on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos")

    inicio = models.DateTimeField()
    fin = models.DateTimeField(blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)

    # Ej: 1440 = 1 día antes, 60 = 1 hora antes
    recordatorio_minutos_antes = models.PositiveIntegerField(default=1440)

    recordatorio_enviado_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.titulo} ({self.inicio})"
