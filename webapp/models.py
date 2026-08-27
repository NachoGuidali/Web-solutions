from bisect import bisect_right
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from datetime import date, timedelta


def _primer_dia_mes(d):
    return d.replace(day=1)


def _sumar_meses(d, n):
    """Devuelve `d` desplazada `n` meses, recortando el día a 28 para no romper febrero."""
    total = (d.year * 12 + (d.month - 1)) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, 28))


def _casa_por_defecto():
    return getattr(settings, "CRM_CASA_DOLAR", "bolsa")


class TipoCambio(models.Model):
    """
    Cotización del dólar de un día. La usamos para expresar todo en una sola
    moneda: con la inflación local, comparar pesos entre meses no dice nada,
    así que el equivalente en USD es la única serie comparable.
    """

    CASAS = [
        ("bolsa", "Dólar MEP / Bolsa"),
        ("blue", "Dólar Blue"),
        ("oficial", "Dólar Oficial"),
        ("cripto", "Dólar Cripto"),
        ("tarjeta", "Dólar Tarjeta"),
    ]

    FUENTES = [
        ("api", "API"),
        ("manual", "Cargado a mano"),
    ]

    fecha = models.DateField()
    casa = models.CharField(max_length=20, choices=CASAS, default=_casa_por_defecto)
    compra = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    venta = models.DecimalField(max_digits=12, decimal_places=2)
    fuente = models.CharField(max_length=10, choices=FUENTES, default="manual")

    class Meta:
        ordering = ["-fecha"]
        unique_together = [("fecha", "casa")]
        verbose_name = "Tipo de cambio"
        verbose_name_plural = "Tipos de cambio"

    def __str__(self):
        return f"{self.get_casa_display()} {self.fecha}: ${self.venta}"

    @classmethod
    def vigente(cls, fecha=None, casa=None):
        """La cotización de esa fecha, o la última anterior. None si no hay ninguna."""
        fecha = fecha or timezone.localdate()
        casa = casa or _casa_por_defecto()
        return (
            cls.objects.filter(casa=casa, fecha__lte=fecha).order_by("-fecha").first()
        )

    @classmethod
    def valor_vigente(cls, fecha=None, casa=None):
        tc = cls.vigente(fecha, casa)
        return tc.venta if tc else None


class Cotizaciones:
    """
    Cotizaciones cargadas en memoria de una sola vez.

    El dashboard convierte decenas de montos por request; buscar la cotización
    de cada uno contra la base sería una consulta por factura.
    """

    def __init__(self, casa=None):
        self.casa = casa or _casa_por_defecto()
        filas = list(
            TipoCambio.objects.filter(casa=self.casa)
            .order_by("fecha")
            .values_list("fecha", "venta")
        )
        self.fechas = [f for f, _ in filas]
        self.valores = [v for _, v in filas]

    @property
    def hay_datos(self):
        return bool(self.fechas)

    def valor(self, fecha):
        """Cotización vigente en `fecha`: la de ese día o la última anterior."""
        if not self.fechas or fecha is None:
            return None
        i = bisect_right(self.fechas, fecha) - 1
        if i < 0:
            return None  # la fecha es anterior a todo lo que tenemos cargado
        return self.valores[i]

    def a_usd(self, monto, moneda, fecha, tipo_cambio=None):
        """
        Pasa `monto` a dólares. `tipo_cambio` es la cotización congelada de la
        factura; si no hay, usamos la vigente en `fecha`. None = no convertible.
        """
        if monto is None:
            return None
        if moneda == "USD":
            return Decimal(monto)

        valor = tipo_cambio or self.valor(fecha)
        if not valor:
            return None
        return (Decimal(monto) / Decimal(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Cliente(models.Model):
    ESTADOS = [
        ('activo', 'Activo'),
        ('potencial', 'Potencial'),
        ('inactivo', 'Inactivo'),
    ]

    nombre = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    empresa = models.CharField(max_length=100, blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='potencial')
    notas = models.TextField(blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def get_absolute_url(self):
        return reverse('cliente_detail', args=[self.pk])

    # --- métricas ---

    def facturado(self, moneda, estado='cobrado'):
        total = Factura.objects.filter(
            proyecto__cliente=self, moneda=moneda, estado=estado
        ).aggregate(t=Sum('monto'))['t']
        return total or Decimal('0')

    @property
    def cobrado_ars(self):
        return self.facturado('ARS')

    @property
    def cobrado_usd(self):
        return self.facturado('USD')

    @property
    def pendiente_ars(self):
        return self.facturado('ARS', estado='pendiente')

    @property
    def pendiente_usd(self):
        return self.facturado('USD', estado='pendiente')

    @property
    def proyectos_activos(self):
        return self.proyectos.filter(estado__in=Proyecto.ESTADOS_ABIERTOS).count()


class Proyecto(models.Model):
    MONEDAS = [
        ('ARS', 'Pesos Argentinos'),
        ('USD', 'Dólares Americanos'),
    ]

    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('en_curso', 'En curso'),
        ('pausado', 'Pausado'),
        ('terminado', 'Terminado'),
        ('entregado', 'Entregado'),
    ]

    # estados en los que el proyecto todavía consume trabajo
    ESTADOS_ABIERTOS = ['pendiente', 'en_curso', 'pausado']

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="proyectos")
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    fecha_inicio = models.DateField()
    fecha_entrega = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='pendiente')
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='ARS')
    precio = models.DecimalField(max_digits=12, decimal_places=2)  # total presupuestado
    monto_cobrado = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # cache de facturas cobradas
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-fecha_inicio', '-id']

    def __str__(self):
        return f"{self.nombre} ({self.cliente.nombre})"

    def get_absolute_url(self):
        return reverse('proyecto_detail', args=[self.pk])

    def simbolo_moneda(self):
        return "$" if self.moneda == 'ARS' else "USD"

    # --- métricas ---

    def recalcular_cobrado(self, guardar=True):
        """Sincroniza `monto_cobrado` con las facturas cobradas en la moneda del proyecto."""
        total = self.facturas.filter(
            estado='cobrado', moneda=self.moneda
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

        if total != self.monto_cobrado:
            self.monto_cobrado = total
            if guardar:
                Proyecto.objects.filter(pk=self.pk).update(monto_cobrado=total)
        return total

    @property
    def saldo(self):
        """Cuánto falta cobrar del presupuesto."""
        return (self.precio or Decimal('0')) - (self.monto_cobrado or Decimal('0'))

    @property
    def porcentaje_cobrado(self):
        if not self.precio:
            return 0
        pct = (self.monto_cobrado or Decimal('0')) / self.precio * 100
        return min(int(pct), 100)

    @property
    def facturado_pendiente(self):
        """Facturas emitidas todavía sin cobrar (en la moneda del proyecto)."""
        return self.facturas.filter(
            estado='pendiente', moneda=self.moneda
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    @property
    def esta_abierto(self):
        return self.estado in self.ESTADOS_ABIERTOS

    @property
    def dias_para_entrega(self):
        if not self.fecha_entrega or not self.esta_abierto:
            return None
        return (self.fecha_entrega - timezone.localdate()).days

    @property
    def esta_atrasado(self):
        d = self.dias_para_entrega
        return d is not None and d < 0

    @property
    def entrega_proxima(self):
        """Entrega dentro de la semana y todavía sin cerrar."""
        d = self.dias_para_entrega
        return d is not None and 0 <= d <= 7


class Factura(models.Model):
    MONEDAS = [
        ('ARS', 'Pesos'),
        ('USD', 'Dólares'),
    ]

    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('cobrado', 'Cobrado'),
    ]

    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name="facturas")
    fecha = models.DateField(default=timezone.localdate)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default='ARS')
    estado = models.CharField(max_length=20, choices=ESTADOS, default='pendiente')
    metodo_pago = models.CharField(max_length=50, blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    fecha_vencimiento = models.DateField(blank=True, null=True)
    fecha_cobro = models.DateField(blank=True, null=True)
    # período mensual que cubre (1er día del mes) cuando nace de una mensualidad
    periodo = models.DateField(blank=True, null=True)
    automatica = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    # cotización congelada al momento del cobro, para que el equivalente en
    # dólares de una factura vieja no cambie cada vez que se mira
    tipo_cambio = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.get_moneda_display()} {self.monto} - {self.proyecto.nombre}"

    @property
    def esta_vencida(self):
        return (
            self.estado == 'pendiente'
            and self.fecha_vencimiento is not None
            and self.fecha_vencimiento < timezone.localdate()
        )

    @property
    def dias_vencida(self):
        if not self.esta_vencida:
            return 0
        return (timezone.localdate() - self.fecha_vencimiento).days

    @property
    def fecha_referencia(self):
        """
        Fecha con la que corresponde cotizar: la del cobro si ya se cobró, y hoy
        si sigue pendiente (es lo que valdría cobrarla ahora, no cuando se emitió).
        """
        if self.estado == 'cobrado':
            return self.fecha_cobro or self.fecha
        return timezone.localdate()

    @property
    def monto_usd(self):
        """Equivalente en dólares. None si no hay cotización para esa fecha."""
        if self.moneda == "USD":
            return self.monto

        valor = self.tipo_cambio or TipoCambio.valor_vigente(self.fecha_referencia)
        if not valor:
            return None
        return (self.monto / valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def monto_ars(self):
        """Equivalente en pesos del día de la operación."""
        if self.moneda == "ARS":
            return self.monto

        valor = self.tipo_cambio or TipoCambio.valor_vigente(self.fecha_referencia)
        if not valor:
            return None
        return (self.monto * valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def congelar_tipo_cambio(self, forzar=False):
        """Guarda la cotización del día de la operación. Devuelve el valor usado."""
        if self.tipo_cambio and not forzar:
            return self.tipo_cambio
        valor = TipoCambio.valor_vigente(self.fecha_referencia)
        if valor:
            self.tipo_cambio = valor
        return valor

    def marcar_cobrada(self, fecha=None, metodo_pago=None):
        self.estado = 'cobrado'
        self.fecha_cobro = fecha or timezone.localdate()
        if metodo_pago:
            self.metodo_pago = metodo_pago
        self.save()

    def save(self, *args, **kwargs):
        anterior = self.fecha_cobro
        if self.estado == 'cobrado' and not self.fecha_cobro:
            self.fecha_cobro = timezone.localdate()
        elif self.estado != 'cobrado':
            self.fecha_cobro = None

        tc_anterior = self.tipo_cambio
        if self.estado == 'cobrado':
            self.congelar_tipo_cambio()
        else:
            # mientras está pendiente se cotiza al día, no tiene sentido congelar
            self.tipo_cambio = None

        # si nos llamaron con update_fields, sumamos los campos que tocamos acá
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            extra = []
            if anterior != self.fecha_cobro:
                extra.append("fecha_cobro")
            if tc_anterior != self.tipo_cambio:
                extra.append("tipo_cambio")
            if extra:
                kwargs["update_fields"] = list(update_fields) + extra

        super().save(*args, **kwargs)
        self.proyecto.recalcular_cobrado()

    def delete(self, *args, **kwargs):
        proyecto = self.proyecto
        super().delete(*args, **kwargs)
        proyecto.recalcular_cobrado()


class ProyectoMensualidad(models.Model):
    """Configuración de cobro mensual recurrente para un proyecto."""

    proyecto = models.OneToOneField(Proyecto, on_delete=models.CASCADE, related_name="mensualidad")

    activa = models.BooleanField(default=True)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    moneda = models.CharField(max_length=3, choices=Proyecto.MONEDAS, default="ARS")

    # vencimiento mensual (día 1-28 para evitar líos con febrero)
    dia_vencimiento = models.PositiveSmallIntegerField(default=1)
    dias_antes_recordatorio = models.PositiveSmallIntegerField(default=5)

    # emitir la factura sola cuando llega el vencimiento
    generar_factura_automatica = models.BooleanField(default=True)

    # control para no duplicar envíos / facturas
    proximo_vencimiento = models.DateField(blank=True, null=True)
    ultimo_recordatorio_enviado = models.DateField(blank=True, null=True)
    ultimo_periodo_facturado = models.DateField(blank=True, null=True)  # 1er día del mes del período

    class Meta:
        ordering = ['proximo_vencimiento']
        verbose_name_plural = "Mensualidades"

    def __str__(self):
        return f"Mensualidad {self.proyecto.nombre} ({self.moneda} {self.monto})"

    def calcular_proximo_vencimiento(self, base=None):
        """Próxima fecha de vencimiento a partir de `base`."""
        if base is None:
            base = timezone.localdate()

        d = min(int(self.dia_vencimiento), 28)
        candidato = date(base.year, base.month, d)

        if candidato < base:
            candidato = _sumar_meses(candidato, 1)

        return candidato

    def avanzar_vencimiento(self):
        """Corre el vencimiento exactamente un mes hacia adelante."""
        base = self.proximo_vencimiento or self.calcular_proximo_vencimiento()
        d = min(int(self.dia_vencimiento), 28)
        self.proximo_vencimiento = _sumar_meses(_primer_dia_mes(base), 1).replace(day=d)
        return self.proximo_vencimiento

    @property
    def esta_desfasada(self):
        """
        El vencimiento quedó en un mes anterior al actual: el cron no corrió por
        un tiempo. No queremos emitir de golpe todas las facturas atrasadas.
        """
        if not self.proximo_vencimiento:
            return False
        return _primer_dia_mes(self.proximo_vencimiento) < _primer_dia_mes(timezone.localdate())

    @property
    def dias_para_vencer(self):
        if not self.proximo_vencimiento:
            return None
        return (self.proximo_vencimiento - timezone.localdate()).days

    @property
    def periodo_pendiente(self):
        """Período (1er día del mes) que corresponde al próximo vencimiento."""
        vencimiento = self.proximo_vencimiento or self.calcular_proximo_vencimiento()
        return _primer_dia_mes(vencimiento)

    @property
    def puede_facturar_ahora(self):
        """
        Se puede emitir la factura si el período ya arrancó (o venció), o si el
        vencimiento entró en la ventana de aviso. Evita facturar meses de más
        apretando el botón varias veces.
        """
        if self.ultimo_periodo_facturado == self.periodo_pendiente:
            return False

        hoy = timezone.localdate()
        if self.periodo_pendiente <= _primer_dia_mes(hoy):
            return True

        dias = self.dias_para_vencer
        return dias is not None and dias <= int(self.dias_antes_recordatorio)

    def generar_factura(self, periodo=None, forzar=False):
        """
        Emite la factura del período (por defecto, el del próximo vencimiento).
        Devuelve la Factura creada, o None si ese período ya estaba facturado.
        """
        vencimiento = self.proximo_vencimiento or self.calcular_proximo_vencimiento()
        periodo = _primer_dia_mes(periodo or vencimiento)

        if not forzar and self.ultimo_periodo_facturado == periodo:
            return None

        if Factura.objects.filter(proyecto=self.proyecto, periodo=periodo, automatica=True).exists():
            return None

        factura = Factura.objects.create(
            proyecto=self.proyecto,
            fecha=timezone.localdate(),
            fecha_vencimiento=vencimiento,
            monto=self.monto,
            moneda=self.moneda,
            estado='pendiente',
            periodo=periodo,
            automatica=True,
            observaciones=f"Mensualidad {periodo.strftime('%m/%Y')} — generada automáticamente.",
        )

        self.ultimo_periodo_facturado = periodo
        self.save(update_fields=["ultimo_periodo_facturado"])
        return factura

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

    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos")

    inicio = models.DateTimeField()
    fin = models.DateTimeField(blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    ubicacion = models.CharField(max_length=250, blank=True, null=True, help_text="Link de Meet, dirección, etc.")

    completado = models.BooleanField(default=False)

    # Ej: 1440 = 1 día antes, 60 = 1 hora antes
    recordatorio_minutos_antes = models.PositiveIntegerField(default=1440)
    recordatorio_enviado_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['inicio']

    def __str__(self):
        return f"{self.titulo} ({self.inicio})"

    @property
    def es_pasado(self):
        return self.inicio < timezone.now()

    @property
    def es_hoy(self):
        return timezone.localtime(self.inicio).date() == timezone.localdate()


class Interaccion(models.Model):
    """Bitácora de contacto con el cliente: llamadas, mails, reuniones, notas sueltas."""

    TIPOS = [
        ("nota", "Nota"),
        ("llamada", "Llamada"),
        ("email", "Email"),
        ("whatsapp", "WhatsApp"),
        ("reunion", "Reunión"),
    ]

    ICONOS = {
        "nota": "fa-note-sticky",
        "llamada": "fa-phone",
        "email": "fa-envelope",
        "whatsapp": "fa-whatsapp",
        "reunion": "fa-users",
    }

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="interacciones")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="interacciones")
    tipo = models.CharField(max_length=20, choices=TIPOS, default="nota")
    detalle = models.TextField()
    fecha = models.DateTimeField(default=timezone.now)
    autor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name_plural = "Interacciones"

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.cliente.nombre} ({self.fecha:%d/%m/%Y})"

    @property
    def icono(self):
        return self.ICONOS.get(self.tipo, "fa-note-sticky")


class Tarea(models.Model):
    """Pendiente concreto. A diferencia de Evento, no ocupa un horario: se hace o no."""

    PRIORIDADES = [
        ("alta", "Alta"),
        ("media", "Media"),
        ("baja", "Baja"),
    ]

    PESO_PRIORIDAD = {"alta": 0, "media": 1, "baja": 2}

    titulo = models.CharField(max_length=200)
    detalle = models.TextField(blank=True, null=True)

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, null=True, blank=True, related_name="tareas")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, null=True, blank=True, related_name="tareas")
    deal = models.ForeignKey("Deal", on_delete=models.CASCADE, null=True, blank=True, related_name="tareas")

    vence = models.DateField(blank=True, null=True)
    prioridad = models.CharField(max_length=10, choices=PRIORIDADES, default="media")

    completada = models.BooleanField(default=False)
    completada_en = models.DateTimeField(blank=True, null=True)

    asignada_a = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas")
    creada_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ["completada", "vence", "id"]

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        # la marca de tiempo sigue al estado, sin que haya que acordarse de setearla
        if self.completada and not self.completada_en:
            self.completada_en = timezone.now()
        elif not self.completada:
            self.completada_en = None
        super().save(*args, **kwargs)

    @property
    def dias_para_vencer(self):
        if not self.vence or self.completada:
            return None
        return (self.vence - timezone.localdate()).days

    @property
    def esta_vencida(self):
        d = self.dias_para_vencer
        return d is not None and d < 0

    @property
    def es_hoy(self):
        return self.dias_para_vencer == 0

    @property
    def es_esta_semana(self):
        d = self.dias_para_vencer
        return d is not None and 0 < d <= 7

    @property
    def contexto(self):
        """A qué está atada la tarea, para mostrarlo en una línea."""
        if self.proyecto_id:
            return self.proyecto
        if self.deal_id:
            return self.deal
        return self.cliente


class Pipeline(models.Model):
    """Un flujo de trabajo del kanban: 'Ventas', 'Onboarding', lo que sea."""

    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)
    creado_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return self.nombre

    def get_absolute_url(self):
        return reverse("kanban", args=[self.pk])

    @property
    def deals_abiertos(self):
        return self.deals.filter(etapa__tipo="abierta")

    def crear_etapas_por_defecto(self):
        """Arranque típico de un embudo de ventas."""
        base = [
            ("Nuevo lead", "sky", "abierta"),
            ("Contactado", "indigo", "abierta"),
            ("Propuesta enviada", "violet", "abierta"),
            ("Negociación", "amber", "abierta"),
            ("Ganado", "emerald", "ganada"),
            ("Perdido", "rose", "perdida"),
        ]
        for i, (nombre, color, tipo) in enumerate(base):
            Etapa.objects.create(pipeline=self, nombre=nombre, color=color, tipo=tipo, orden=i)
        return self.etapas.all()


class Etapa(models.Model):
    """Una columna del kanban."""

    TIPOS = [
        ("abierta", "En curso"),
        ("ganada", "Ganada"),
        ("perdida", "Perdida"),
    ]

    COLORES = [
        ("indigo", "Índigo"),
        ("sky", "Celeste"),
        ("violet", "Violeta"),
        ("emerald", "Verde"),
        ("amber", "Ámbar"),
        ("rose", "Rosa"),
        ("slate", "Gris"),
    ]

    # las clases se escriben enteras porque Tailwind no las arma por concatenación
    PALETA = {
        "indigo": ("bg-indigo-500", "bg-indigo-500/15 text-indigo-300 border-indigo-500/30"),
        "sky": ("bg-sky-500", "bg-sky-500/15 text-sky-300 border-sky-500/30"),
        "violet": ("bg-violet-500", "bg-violet-500/15 text-violet-300 border-violet-500/30"),
        "emerald": ("bg-emerald-500", "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"),
        "amber": ("bg-amber-500", "bg-amber-500/15 text-amber-300 border-amber-500/30"),
        "rose": ("bg-rose-500", "bg-rose-500/15 text-rose-300 border-rose-500/30"),
        "slate": ("bg-slate-500", "bg-slate-500/15 text-slate-300 border-slate-500/30"),
    }

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="etapas")
    nombre = models.CharField(max_length=80)
    color = models.CharField(max_length=20, choices=COLORES, default="indigo")
    tipo = models.CharField(max_length=10, choices=TIPOS, default="abierta")
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return f"{self.pipeline.nombre} / {self.nombre}"

    @property
    def clase_barra(self):
        return self.PALETA.get(self.color, self.PALETA["indigo"])[0]

    @property
    def clase_chip(self):
        return self.PALETA.get(self.color, self.PALETA["indigo"])[1]

    @property
    def es_cierre(self):
        return self.tipo in ("ganada", "perdida")


class Deal(models.Model):
    """Una oportunidad: lo que estás tratando de cerrar."""

    MONEDAS = Proyecto.MONEDAS

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="deals")
    etapa = models.ForeignKey(Etapa, on_delete=models.PROTECT, related_name="deals")

    titulo = models.CharField(max_length=200)
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="deals")
    descripcion = models.TextField(blank=True, null=True)

    valor = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default="ARS")

    cierre_estimado = models.DateField(blank=True, null=True)
    cerrado_en = models.DateField(blank=True, null=True)
    motivo_cierre = models.TextField(blank=True, null=True)

    # proyecto que salió de este deal, cuando se gana
    proyecto = models.OneToOneField(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="deal")

    orden = models.PositiveIntegerField(default=0)
    creado_en = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["orden", "-id"]

    def __str__(self):
        return self.titulo

    def get_absolute_url(self):
        return reverse("deal_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        # entrar o salir de una etapa de cierre marca (o borra) la fecha
        if self.etapa_id:
            if self.etapa.es_cierre and not self.cerrado_en:
                self.cerrado_en = timezone.localdate()
            elif not self.etapa.es_cierre:
                self.cerrado_en = None
        super().save(*args, **kwargs)

    @property
    def esta_ganado(self):
        return self.etapa.tipo == "ganada"

    @property
    def esta_perdido(self):
        return self.etapa.tipo == "perdida"

    @property
    def esta_abierto(self):
        return self.etapa.tipo == "abierta"

    @property
    def valor_usd(self):
        if self.moneda == "USD":
            return self.valor
        valor = TipoCambio.valor_vigente()
        if not valor:
            return None
        return (self.valor / valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def dias_para_cierre(self):
        if not self.cierre_estimado or not self.esta_abierto:
            return None
        return (self.cierre_estimado - timezone.localdate()).days

    @property
    def esta_demorado(self):
        d = self.dias_para_cierre
        return d is not None and d < 0

    @property
    def tareas_pendientes(self):
        return self.tareas.filter(completada=False).count()

    def convertir_en_proyecto(self):
        """
        Crea el proyecto a partir del deal ganado. Devuelve el proyecto, o None
        si el deal no tiene cliente o ya fue convertido.
        """
        if self.proyecto_id or not self.cliente_id:
            return None

        proyecto = Proyecto.objects.create(
            cliente=self.cliente,
            nombre=self.titulo,
            descripcion=self.descripcion,
            fecha_inicio=timezone.localdate(),
            fecha_entrega=self.cierre_estimado,
            estado="pendiente",
            moneda=self.moneda,
            precio=self.valor,
        )
        self.proyecto = proyecto
        self.save(update_fields=["proyecto", "cerrado_en"])
        return proyecto
