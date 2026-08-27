from datetime import datetime

from django.core.management.base import BaseCommand

from webapp.cotizaciones import sincronizar
from webapp.models import Factura, TipoCambio, _casa_por_defecto


class Command(BaseCommand):
    help = "Trae las cotizaciones del dólar (histórico + hoy) desde las APIs públicas."

    def add_arguments(self, parser):
        parser.add_argument("--casa", default=None, help="bolsa (default), blue, oficial, cripto, tarjeta")
        parser.add_argument("--desde", default=None, help="AAAA-MM-DD; por defecto, la factura más vieja")
        parser.add_argument("--solo-hoy", action="store_true", help="No traer el histórico")

    def handle(self, *args, **opciones):
        casa = opciones["casa"] or _casa_por_defecto()

        desde = None
        if opciones["desde"]:
            desde = datetime.strptime(opciones["desde"], "%Y-%m-%d").date()
        else:
            primera = Factura.objects.order_by("fecha").values_list("fecha", flat=True).first()
            if primera:
                desde = primera

        self.stdout.write(f"Sincronizando «{casa}»{f' desde {desde}' if desde else ''}...")

        resumen = sincronizar(casa=casa, desde=desde, con_historico=not opciones["solo_hoy"])

        for error in resumen["errores"]:
            self.stdout.write(self.style.WARNING(f"  ! {error}"))

        self.stdout.write(self.style.SUCCESS(
            f"  {resumen['creados']} nueva(s), {resumen['actualizados']} actualizada(s). "
            f"Total en base: {resumen['total']} (última: {resumen['ultima']})."
        ))

        sin_cotizar = [f for f in Factura.objects.all() if f.monto_usd is None]
        if sin_cotizar:
            self.stdout.write(self.style.WARNING(
                f"  Todavía hay {len(sin_cotizar)} factura(s) sin cotización para su fecha."
            ))
