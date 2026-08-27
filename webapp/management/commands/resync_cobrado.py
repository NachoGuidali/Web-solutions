from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from webapp.models import Factura, Proyecto


class Command(BaseCommand):
    help = (
        "Realinea Proyecto.monto_cobrado con las facturas cobradas y avisa de "
        "las facturas cuya moneda no coincide con la del proyecto."
    )

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true",
                            help="Guarda los cambios. Sin este flag sólo muestra qué haría.")

    def handle(self, *args, **opciones):
        aplicar = opciones["aplicar"]
        desfasados = []

        for p in Proyecto.objects.select_related("cliente"):
            real = p.facturas.filter(estado="cobrado", moneda=p.moneda).aggregate(
                t=Sum("monto"))["t"] or Decimal("0")

            if real != p.monto_cobrado:
                desfasados.append((p, p.monto_cobrado, real))
                if aplicar:
                    Proyecto.objects.filter(pk=p.pk).update(monto_cobrado=real)

        if desfasados:
            self.stdout.write(self.style.WARNING(f"{len(desfasados)} proyecto(s) desfasado(s):"))
            for p, guardado, real in desfasados:
                self.stdout.write(
                    f"  {p.cliente.nombre} / {p.nombre}: "
                    f"{p.moneda} {guardado} -> {p.moneda} {real}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("Todos los proyectos están sincronizados."))

        # facturas en una moneda distinta a la del proyecto
        mezcladas = [
            f for f in Factura.objects.select_related("proyecto", "proyecto__cliente")
            if f.moneda != f.proyecto.moneda
        ]
        if mezcladas:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{len(mezcladas)} factura(s) en una moneda distinta a la de su proyecto "
                f"(no suman al cobrado):"
            ))
            for f in mezcladas:
                self.stdout.write(
                    f"  #{f.id} {f.moneda} {f.monto} -> «{f.proyecto.nombre}» "
                    f"está en {f.proyecto.moneda}"
                )
            self.stdout.write(
                "  Revisá si el proyecto está mal marcado; corregir la moneda del "
                "proyecto y volver a correr este comando alcanza."
            )

        # facturas sin cotización disponible
        sin_tc = [f for f in Factura.objects.all() if f.monto_usd is None]
        if sin_tc:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{len(sin_tc)} factura(s) sin cotización para su fecha "
                f"(corré `sincronizar_cotizaciones`)."
            ))

        if desfasados and not aplicar:
            self.stdout.write("")
            self.stdout.write("Nada se guardó. Volvé a correr con --aplicar para escribir los cambios.")
