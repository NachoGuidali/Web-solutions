from django.core.management.base import BaseCommand

from webapp.models import Factura


class Command(BaseCommand):
    help = (
        "Le guarda a cada factura cobrada la cotización de su fecha, para que su "
        "equivalente en dólares quede fijo y no se mueva con el dólar de hoy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Guarda los cambios.")
        parser.add_argument("--forzar", action="store_true",
                            help="Recalcula también las que ya tienen cotización guardada.")

    def handle(self, *args, **opciones):
        aplicar, forzar = opciones["aplicar"], opciones["forzar"]
        congeladas, sin_datos = 0, []

        for f in Factura.objects.filter(estado="cobrado").select_related("proyecto"):
            if f.tipo_cambio and not forzar:
                continue

            anterior = f.tipo_cambio
            valor = f.congelar_tipo_cambio(forzar=forzar)

            if not valor:
                sin_datos.append(f)
                continue

            self.stdout.write(f"  #{f.id} {f.fecha_referencia} {f.moneda} {f.monto} -> TC {valor}"
                              + (f" (antes {anterior})" if anterior else ""))
            if aplicar:
                Factura.objects.filter(pk=f.pk).update(tipo_cambio=valor)
            congeladas += 1

        self.stdout.write(self.style.SUCCESS(f"{congeladas} factura(s) con cotización congelada."))

        if sin_datos:
            self.stdout.write(self.style.WARNING(
                f"{len(sin_datos)} sin cotización para su fecha: "
                + ", ".join(f"#{f.id} ({f.fecha_referencia})" for f in sin_datos)
            ))

        if congeladas and not aplicar:
            self.stdout.write("Nada se guardó. Volvé a correr con --aplicar.")
