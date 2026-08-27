from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.utils import timezone

from .cotizaciones import ErrorCotizaciones, traer_historico, traer_hoy
from .models import (
    Cliente, Cotizaciones, Deal, Etapa, Evento, Factura, Interaccion,
    Pipeline, Proyecto, ProyectoMensualidad, Tarea, TipoCambio,
)
from .tasks import (
    avisar_facturas_vencidas, enviar_recordatorios_eventos,
    enviar_recordatorios_mensualidades,
)

SEGURO = dict(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
LOCMEM = dict(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class BaseCRM(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.user = User.objects.create_user("tester", password="x", is_staff=True, is_superuser=True)
        self.cliente = Cliente.objects.create(nombre="ACME", email="acme@test.com", estado="activo")
        self.proyecto = Proyecto.objects.create(
            cliente=self.cliente, nombre="Sitio web", fecha_inicio=self.hoy - timedelta(days=30),
            fecha_entrega=self.hoy + timedelta(days=30), estado="en_curso",
            moneda="ARS", precio=Decimal("100000"),
        )
        self.client = Client()
        self.client.force_login(self.user)


# ------------------------------------------------------------------ modelos

class SincronizacionCobradoTests(BaseCRM):
    """`Proyecto.monto_cobrado` tiene que seguir siempre a las facturas cobradas."""

    def test_factura_pendiente_no_suma(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                               moneda="ARS", estado="pendiente")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("0"))

    def test_factura_cobrada_suma_y_setea_fecha_cobro(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                                   moneda="ARS", estado="cobrado")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("5000"))
        self.assertEqual(f.fecha_cobro, self.hoy)

    def test_editar_factura_cobrada_recalcula(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                                   moneda="ARS", estado="cobrado")
        f.monto = Decimal("8000")
        f.save()
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("8000"))

    def test_borrar_factura_cobrada_recalcula(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                                   moneda="ARS", estado="cobrado")
        f.delete()
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("0"))

    def test_volver_a_pendiente_descuenta_y_limpia_fecha_cobro(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                                   moneda="ARS", estado="cobrado")
        f.estado = "pendiente"
        f.save()
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("0"))
        self.assertIsNone(f.fecha_cobro)

    def test_no_mezcla_monedas(self):
        """Una factura en USD no puede sumar al cobrado de un proyecto en ARS."""
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100"),
                               moneda="USD", estado="cobrado")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("0"))


class ProyectoMetricasTests(BaseCRM):
    def test_atraso_solo_en_proyectos_abiertos(self):
        self.proyecto.fecha_entrega = self.hoy - timedelta(days=5)
        self.proyecto.save()
        self.assertTrue(self.proyecto.esta_atrasado)

        self.proyecto.estado = "entregado"
        self.proyecto.save()
        self.assertFalse(self.proyecto.esta_atrasado)

    def test_porcentaje_y_saldo(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("25000"),
                               moneda="ARS", estado="cobrado")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.porcentaje_cobrado, 25)
        self.assertEqual(self.proyecto.saldo, Decimal("75000"))

    def test_factura_vencida(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=20),
                                   fecha_vencimiento=self.hoy - timedelta(days=10),
                                   monto=Decimal("100"), moneda="ARS", estado="pendiente")
        self.assertTrue(f.esta_vencida)
        self.assertEqual(f.dias_vencida, 10)

        f.estado = "cobrado"
        f.save()
        self.assertFalse(f.esta_vencida)


class MensualidadTests(BaseCRM):
    def _mensualidad(self, **kwargs):
        datos = dict(proyecto=self.proyecto, activa=True, monto=Decimal("1500"), moneda="ARS",
                     dia_vencimiento=min(self.hoy.day, 28), dias_antes_recordatorio=3)
        datos.update(kwargs)
        return ProyectoMensualidad.objects.create(**datos)

    def test_avanzar_vencimiento_corre_exactamente_un_mes(self):
        m = self._mensualidad(dia_vencimiento=5, proximo_vencimiento=self.hoy.replace(day=5))
        original = m.proximo_vencimiento
        m.avanzar_vencimiento()
        esperado_mes = original.month % 12 + 1
        self.assertEqual(m.proximo_vencimiento.month, esperado_mes)
        self.assertEqual(m.proximo_vencimiento.day, 5)

    def test_generar_factura_no_duplica_el_periodo(self):
        m = self._mensualidad(proximo_vencimiento=self.hoy)
        f1 = m.generar_factura()
        self.assertIsNotNone(f1)
        self.assertTrue(f1.automatica)
        self.assertEqual(f1.periodo, self.hoy.replace(day=1))
        self.assertIsNone(m.generar_factura())
        self.assertEqual(Factura.objects.filter(proyecto=self.proyecto).count(), 1)

    def test_no_deja_facturar_un_periodo_futuro_sin_forzar(self):
        futuro = self.hoy + timedelta(days=40)
        m = self._mensualidad(dia_vencimiento=min(futuro.day, 28), proximo_vencimiento=futuro)
        self.assertFalse(m.puede_facturar_ahora)

    def test_permite_facturar_el_periodo_en_curso(self):
        m = self._mensualidad(proximo_vencimiento=self.hoy)
        self.assertTrue(m.puede_facturar_ahora)

    def test_boton_facturar_bloquea_periodo_futuro(self):
        futuro = self.hoy + timedelta(days=40)
        m = self._mensualidad(dia_vencimiento=min(futuro.day, 28), proximo_vencimiento=futuro)
        with override_settings(**SEGURO):
            self.client.post(f"/dashboard/mensualidades/{m.id}/facturar/")
        self.assertEqual(Factura.objects.count(), 0)

    def test_boton_adelantar_factura_con_forzar(self):
        futuro = self.hoy + timedelta(days=40)
        m = self._mensualidad(dia_vencimiento=min(futuro.day, 28), proximo_vencimiento=futuro)
        with override_settings(**SEGURO):
            self.client.post(f"/dashboard/mensualidades/{m.id}/facturar/", {"forzar": "1"})
        self.assertEqual(Factura.objects.count(), 1)


# ------------------------------------------------------------------ tareas

@override_settings(**LOCMEM)
class TareasTests(BaseCRM):
    def _mensualidad(self, **kwargs):
        datos = dict(proyecto=self.proyecto, activa=True, monto=Decimal("1500"), moneda="ARS",
                     dia_vencimiento=min(self.hoy.day, 28), dias_antes_recordatorio=3,
                     generar_factura_automatica=True)
        datos.update(kwargs)
        return ProyectoMensualidad.objects.create(**datos)

    def test_factura_al_vencer_y_no_duplica_en_la_segunda_corrida(self):
        m = self._mensualidad(proximo_vencimiento=self.hoy)

        enviar_recordatorios_mensualidades()
        self.assertEqual(Factura.objects.filter(automatica=True).count(), 1)

        m.refresh_from_db()
        self.assertGreater(m.proximo_vencimiento, self.hoy)

        enviar_recordatorios_mensualidades()
        self.assertEqual(Factura.objects.filter(automatica=True).count(), 1)

    def test_mensualidad_desfasada_se_resincroniza_sin_emitir_atrasadas(self):
        """Si el cron estuvo caído meses, no queremos 7 facturas de golpe."""
        m = self._mensualidad(dia_vencimiento=7, proximo_vencimiento=self.hoy - timedelta(days=200))

        enviar_recordatorios_mensualidades()

        self.assertEqual(Factura.objects.count(), 0)
        m.refresh_from_db()
        self.assertGreaterEqual(m.proximo_vencimiento, self.hoy)
        self.assertTrue(any("desfasada" in x.subject for x in mail.outbox))

    def test_mensualidad_pausada_no_factura(self):
        self._mensualidad(activa=False, proximo_vencimiento=self.hoy)
        enviar_recordatorios_mensualidades()
        self.assertEqual(Factura.objects.count(), 0)

    def test_sin_factura_automatica_solo_avanza(self):
        m = self._mensualidad(proximo_vencimiento=self.hoy, generar_factura_automatica=False)
        enviar_recordatorios_mensualidades()
        self.assertEqual(Factura.objects.count(), 0)
        m.refresh_from_db()
        self.assertGreater(m.proximo_vencimiento, self.hoy)

    def test_recordatorio_previo_se_manda_una_sola_vez(self):
        venc = self.hoy + timedelta(days=3)
        self._mensualidad(dia_vencimiento=min(venc.day, 28), proximo_vencimiento=venc,
                          dias_antes_recordatorio=3)

        enviar_recordatorios_mensualidades()
        avisos = [x for x in mail.outbox if "Vence mensualidad" in x.subject]
        self.assertEqual(len(avisos), 1)

        enviar_recordatorios_mensualidades()
        avisos = [x for x in mail.outbox if "Vence mensualidad" in x.subject]
        self.assertEqual(len(avisos), 1)

    def test_aviso_de_facturas_vencidas(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=30),
                               fecha_vencimiento=self.hoy - timedelta(days=10),
                               monto=Decimal("900"), moneda="ARS", estado="pendiente")
        avisar_facturas_vencidas()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("vencida", mail.outbox[0].subject)

    def test_no_avisa_si_no_hay_vencidas(self):
        avisar_facturas_vencidas()
        self.assertEqual(len(mail.outbox), 0)

    def test_recordatorio_de_evento_no_se_repite(self):
        Evento.objects.create(titulo="Meet", tipo="meet", cliente=self.cliente,
                              inicio=timezone.now() + timedelta(minutes=30),
                              recordatorio_minutos_antes=60)
        enviar_recordatorios_eventos()
        self.assertEqual(len(mail.outbox), 1)
        enviar_recordatorios_eventos()
        self.assertEqual(len(mail.outbox), 1)

    def test_evento_completado_no_dispara_recordatorio(self):
        Evento.objects.create(titulo="Meet", tipo="meet", inicio=timezone.now() + timedelta(minutes=30),
                              recordatorio_minutos_antes=60, completado=True)
        enviar_recordatorios_eventos()
        self.assertEqual(len(mail.outbox), 0)


# ------------------------------------------------------------------ vistas

@override_settings(**SEGURO)
class VistasTests(BaseCRM):
    def test_todas_las_vistas_del_crm_responden(self):
        factura = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100"),
                                         moneda="ARS", estado="pendiente")
        mens = ProyectoMensualidad.objects.create(proyecto=self.proyecto, monto=Decimal("500"),
                                                  moneda="ARS", dia_vencimiento=5)
        evento = Evento.objects.create(titulo="Meet", inicio=timezone.now() + timedelta(days=1))

        rutas = [
            "/dashboard/",
            "/clientes/", "/clientes/nuevo/", "/clientes/exportar/",
            f"/clientes/{self.cliente.id}/", f"/clientes/{self.cliente.id}/editar/",
            f"/clientes/{self.cliente.id}/eliminar/",
            "/proyectos/", "/proyectos/?estado=atrasados", "/proyectos/nuevo/",
            f"/proyectos/{self.proyecto.id}/", f"/proyectos/editar/{self.proyecto.id}/",
            f"/proyectos/{self.proyecto.id}/eliminar/",
            "/dashboard/facturas/", "/dashboard/facturas/?estado=vencidas",
            "/dashboard/facturas/?export=csv", "/dashboard/facturas/nueva/",
            f"/dashboard/facturas/{factura.id}/editar/", f"/dashboard/facturas/{factura.id}/eliminar/",
            "/dashboard/mensualidades/", "/dashboard/mensualidades/nueva/",
            f"/dashboard/mensualidades/{mens.id}/editar/", f"/dashboard/mensualidades/{mens.id}/eliminar/",
            "/dashboard/agenda/", "/dashboard/agenda/?vista=todos", "/dashboard/agenda/nuevo/",
            f"/dashboard/agenda/{evento.id}/editar/", f"/dashboard/agenda/{evento.id}/eliminar/",
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_el_crm_pide_login(self):
        anonimo = Client()
        for ruta in ["/dashboard/", "/clientes/", "/proyectos/", "/dashboard/facturas/"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(anonimo.get(ruta).status_code, 302)

    def test_busqueda_de_clientes(self):
        Cliente.objects.create(nombre="Otro SRL", estado="potencial")
        resp = self.client.get("/clientes/?q=ACME")
        self.assertEqual([c.nombre for c in resp.context["page_obj"]], ["ACME"])

    def test_filtro_de_proyectos_atrasados(self):
        atrasado = Proyecto.objects.create(cliente=self.cliente, nombre="Tarde",
                                           fecha_inicio=self.hoy - timedelta(days=60),
                                           fecha_entrega=self.hoy - timedelta(days=5),
                                           estado="en_curso", moneda="ARS", precio=Decimal("1"))
        resp = self.client.get("/proyectos/?estado=atrasados")
        self.assertEqual([p.id for p in resp.context["page_obj"]], [atrasado.id])

    def test_marcar_cobrada_y_revertir(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("5000"),
                                   moneda="ARS", estado="pendiente")
        self.client.post(f"/dashboard/facturas/{f.id}/cobrada/")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("5000"))

        self.client.post(f"/dashboard/facturas/{f.id}/pendiente/")
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("0"))

    def test_registrar_interaccion_guarda_el_autor(self):
        self.client.post(f"/clientes/{self.cliente.id}/interaccion/", {
            "cliente": self.cliente.id, "tipo": "llamada",
            "fecha": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "detalle": "Charla inicial",
        })
        i = Interaccion.objects.get()
        self.assertEqual(i.autor, self.user)
        self.assertEqual(i.cliente, self.cliente)

    def test_cambio_rapido_de_estado(self):
        self.client.post(f"/proyectos/{self.proyecto.id}/estado/", {"estado": "entregado"})
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.estado, "entregado")

    def test_estado_invalido_no_rompe_el_proyecto(self):
        self.client.post(f"/proyectos/{self.proyecto.id}/estado/", {"estado": "inventado"})
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.estado, "en_curso")

    def test_borrar_cliente_arrastra_proyectos_y_facturas(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1"),
                               moneda="ARS", estado="pendiente")
        self.client.post(f"/clientes/{self.cliente.id}/eliminar/")
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(Proyecto.objects.count(), 0)
        self.assertEqual(Factura.objects.count(), 0)

    def test_export_csv_de_facturas(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("777"),
                               moneda="ARS", estado="cobrado")
        resp = self.client.get("/dashboard/facturas/?export=csv")
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("777", resp.content.decode())

    def test_redirect_next_solo_acepta_rutas_internas(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1"),
                                   moneda="ARS", estado="pendiente")
        for hostil in ["https://evil.com", "//evil.com", "http://evil.com/x", "javascript:alert(1)"]:
            with self.subTest(next=hostil):
                resp = self.client.post(f"/dashboard/facturas/{f.id}/cobrada/", {"next": hostil})
                self.assertEqual(resp["Location"], "/dashboard/facturas/")

    def test_redirect_next_respeta_una_ruta_interna(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1"),
                                   moneda="ARS", estado="pendiente")
        destino = f"/proyectos/{self.proyecto.id}/"
        resp = self.client.post(f"/dashboard/facturas/{f.id}/cobrada/", {"next": destino})
        self.assertEqual(resp["Location"], destino)


@override_settings(**SEGURO)
class FormulariosTests(BaseCRM):
    def test_no_deja_entrega_anterior_al_inicio(self):
        resp = self.client.post("/proyectos/nuevo/", {
            "cliente": self.cliente.id, "nombre": "X", "descripcion": "",
            "fecha_inicio": "2026-05-10", "fecha_entrega": "2026-05-01",
            "estado": "pendiente", "moneda": "ARS", "precio": "100", "observaciones": "",
        })
        self.assertIn("fecha_entrega", resp.context["form"].errors)

    def test_no_deja_monto_cero(self):
        resp = self.client.post("/dashboard/facturas/nueva/", {
            "proyecto": self.proyecto.id, "fecha": str(self.hoy), "fecha_vencimiento": "",
            "monto": "0", "moneda": "ARS", "estado": "pendiente", "metodo_pago": "", "observaciones": "",
        })
        self.assertIn("monto", resp.context["form"].errors)

    def test_un_proyecto_no_puede_tener_dos_mensualidades(self):
        ProyectoMensualidad.objects.create(proyecto=self.proyecto, monto=Decimal("1"),
                                           moneda="ARS", dia_vencimiento=1)
        resp = self.client.get("/dashboard/mensualidades/nueva/")
        opciones = resp.context["form"].fields["proyecto"].queryset
        self.assertNotIn(self.proyecto, opciones)


# ------------------------------------------------------------------ cotizaciones

class TipoCambioTests(BaseCRM):
    def _tc(self, fecha, venta, casa="bolsa"):
        return TipoCambio.objects.create(fecha=fecha, casa=casa, venta=Decimal(venta), fuente="api")

    def test_vigente_usa_la_ultima_anterior(self):
        self._tc(self.hoy - timedelta(days=10), "1000")
        self._tc(self.hoy - timedelta(days=3), "1200")

        self.assertEqual(TipoCambio.valor_vigente(self.hoy), Decimal("1200"))
        self.assertEqual(TipoCambio.valor_vigente(self.hoy - timedelta(days=5)), Decimal("1000"))

    def test_sin_cotizacion_anterior_devuelve_none(self):
        self._tc(self.hoy, "1500")
        self.assertIsNone(TipoCambio.valor_vigente(self.hoy - timedelta(days=1)))

    def test_conversor_en_memoria_coincide_con_la_consulta(self):
        self._tc(self.hoy - timedelta(days=10), "1000")
        self._tc(self.hoy - timedelta(days=3), "1200")
        c = Cotizaciones()

        self.assertEqual(c.valor(self.hoy), Decimal("1200"))
        self.assertEqual(c.valor(self.hoy - timedelta(days=5)), Decimal("1000"))
        self.assertIsNone(c.valor(self.hoy - timedelta(days=30)))

    def test_conversor_sin_datos_no_rompe(self):
        c = Cotizaciones()
        self.assertFalse(c.hay_datos)
        self.assertIsNone(c.a_usd(Decimal("1000"), "ARS", self.hoy))
        # una factura ya en dólares no necesita cotización
        self.assertEqual(c.a_usd(Decimal("50"), "USD", self.hoy), Decimal("50"))

    def test_conversion_a_usd(self):
        self._tc(self.hoy, "1000")
        c = Cotizaciones()
        self.assertEqual(c.a_usd(Decimal("150000"), "ARS", self.hoy), Decimal("150.00"))
        self.assertEqual(c.a_usd(Decimal("150"), "USD", self.hoy), Decimal("150"))

    def test_factura_congela_el_tipo_de_cambio_al_cobrar(self):
        self._tc(self.hoy, "1000")
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100000"),
                                   moneda="ARS", estado="cobrado")
        self.assertEqual(f.tipo_cambio, Decimal("1000"))
        self.assertEqual(f.monto_usd, Decimal("100.00"))

    def test_el_equivalente_no_se_mueve_cuando_cambia_el_dolar(self):
        """Una factura vieja tiene que valer lo que valía, no lo que valdría hoy."""
        self._tc(self.hoy - timedelta(days=30), "1000")
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=30),
                                   monto=Decimal("100000"), moneda="ARS", estado="cobrado")
        self.assertEqual(f.monto_usd, Decimal("100.00"))

        self._tc(self.hoy, "2000")  # el dólar se duplica
        f.refresh_from_db()
        self.assertEqual(f.monto_usd, Decimal("100.00"))

    def test_factura_pendiente_cotiza_al_dia_de_hoy(self):
        self._tc(self.hoy - timedelta(days=30), "1000")
        self._tc(self.hoy, "2000")
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=30),
                                   monto=Decimal("100000"), moneda="ARS", estado="pendiente")
        self.assertIsNone(f.tipo_cambio)
        self.assertEqual(f.monto_usd, Decimal("50.00"))

    def test_volver_a_pendiente_descongela(self):
        self._tc(self.hoy, "1000")
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1000"),
                                   moneda="ARS", estado="cobrado")
        self.assertIsNotNone(f.tipo_cambio)
        f.estado = "pendiente"
        f.save()
        self.assertIsNone(f.tipo_cambio)

    def test_factura_en_usd_no_necesita_cotizacion(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("500"),
                                   moneda="USD", estado="cobrado")
        self.assertEqual(f.monto_usd, Decimal("500"))


class SincronizacionCotizacionesTests(TestCase):
    """La capa que habla con las APIs, sin salir a la red."""

    def test_traer_hoy_guarda_lo_que_devuelve_la_api(self):
        respuesta = {"compra": 1541.6, "venta": 1543.6, "fechaActualizacion": "2026-08-26T19:00:00.000Z"}
        with patch("webapp.cotizaciones._pedir", return_value=respuesta):
            tc = traer_hoy(casa="bolsa")

        self.assertEqual(tc.fecha, date(2026, 8, 26))
        self.assertEqual(tc.venta, Decimal("1543.60"))
        self.assertEqual(tc.fuente, "api")

    def test_traer_hoy_sin_venta_falla_claro(self):
        with patch("webapp.cotizaciones._pedir", return_value={"compra": 100}):
            with self.assertRaises(ErrorCotizaciones):
                traer_hoy(casa="bolsa")

    def test_error_de_red_se_traduce(self):
        with patch("webapp.cotizaciones._pedir", side_effect=ErrorCotizaciones("sin red")):
            with self.assertRaises(ErrorCotizaciones):
                traer_hoy(casa="bolsa")

    def test_historico_respeta_el_rango(self):
        serie = [
            {"fecha": "2025-01-10", "compra": 1000, "venta": 1010},
            {"fecha": "2025-06-10", "compra": 1200, "venta": 1210},
            {"fecha": "2025-12-10", "compra": 1400, "venta": 1410},
        ]
        with patch("webapp.cotizaciones._pedir", return_value=serie):
            creados, _, leidos = traer_historico(casa="bolsa", desde=date(2025, 5, 1))

        self.assertEqual((creados, leidos), (2, 2))
        self.assertFalse(TipoCambio.objects.filter(fecha=date(2025, 1, 10)).exists())

    def test_la_api_no_pisa_lo_cargado_a_mano(self):
        TipoCambio.objects.create(fecha=date(2025, 6, 10), casa="bolsa",
                                  venta=Decimal("9999"), fuente="manual")
        serie = [{"fecha": "2025-06-10", "compra": 1200, "venta": 1210}]
        with patch("webapp.cotizaciones._pedir", return_value=serie):
            traer_historico(casa="bolsa")

        tc = TipoCambio.objects.get(fecha=date(2025, 6, 10), casa="bolsa")
        self.assertEqual(tc.venta, Decimal("9999"))
        self.assertEqual(tc.fuente, "manual")

    def test_reimportar_lo_mismo_no_cuenta_como_cambio(self):
        serie = [{"fecha": "2025-06-10", "compra": 1200, "venta": 1210}]
        with patch("webapp.cotizaciones._pedir", return_value=serie):
            traer_historico(casa="bolsa")
            creados, actualizados, _ = traer_historico(casa="bolsa")
        self.assertEqual((creados, actualizados), (0, 0))


@override_settings(**SEGURO)
class DashboardConsolidadoTests(BaseCRM):
    def test_consolida_las_dos_monedas_en_dolares(self):
        TipoCambio.objects.create(fecha=self.hoy, casa="bolsa", venta=Decimal("1000"), fuente="api")

        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100000"),
                               moneda="ARS", estado="cobrado")
        proyecto_usd = Proyecto.objects.create(cliente=self.cliente, nombre="USD", fecha_inicio=self.hoy,
                                               estado="en_curso", moneda="USD", precio=Decimal("1000"))
        Factura.objects.create(proyecto=proyecto_usd, fecha=self.hoy, monto=Decimal("250"),
                               moneda="USD", estado="cobrado")

        ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["total_equivalente"], Decimal("350.00"))  # 100 + 250
        self.assertEqual(ctx["total_facturado_ars"], Decimal("100000"))
        self.assertEqual(ctx["total_facturado_usd"], Decimal("250"))
        self.assertEqual(ctx["sin_cotizar"], 0)

    def test_cuenta_las_facturas_que_no_puede_convertir(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100000"),
                               moneda="ARS", estado="cobrado")
        ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["sin_cotizar"], 1)
        self.assertEqual(ctx["total_equivalente"], Decimal("0"))
        self.assertIsNone(ctx["cotizacion"])

    def test_el_mes_se_mide_por_fecha_de_cobro_no_de_emision(self):
        TipoCambio.objects.create(fecha=self.hoy - timedelta(days=90), casa="bolsa",
                                  venta=Decimal("1000"), fuente="api")

        # emitida hace tres meses, cobrada hoy: cuenta este mes
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=90),
                                   monto=Decimal("100000"), moneda="ARS", estado="pendiente")
        f.marcar_cobrada(fecha=self.hoy)

        ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["mes_ars"], Decimal("100000"))

    def test_una_factura_vieja_cobrada_hace_meses_no_cuenta_este_mes(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy - timedelta(days=90),
                                   monto=Decimal("100000"), moneda="ARS", estado="pendiente")
        f.marcar_cobrada(fecha=self.hoy - timedelta(days=80))

        ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["mes_ars"], Decimal("0"))

    def test_avisa_cuando_la_cotizacion_esta_vieja(self):
        TipoCambio.objects.create(fecha=self.hoy - timedelta(days=10), casa="bolsa",
                                  venta=Decimal("1000"), fuente="api")
        ctx = self.client.get("/dashboard/").context
        self.assertTrue(ctx["cotizacion_atrasada"])
        self.assertEqual(ctx["dias_sin_cotizar"], 10)


@override_settings(**SEGURO)
class VistasCotizacionesTests(BaseCRM):
    def test_pantalla_responde(self):
        self.assertEqual(self.client.get("/dashboard/cotizaciones/").status_code, 200)

    def test_alta_manual(self):
        self.client.post("/dashboard/cotizaciones/nueva/", {
            "fecha": str(self.hoy), "casa": "bolsa", "compra": "1500", "venta": "1520",
        })
        tc = TipoCambio.objects.get()
        self.assertEqual(tc.venta, Decimal("1520"))
        self.assertEqual(tc.fuente, "manual")

    def test_no_acepta_cotizacion_negativa(self):
        self.client.post("/dashboard/cotizaciones/nueva/", {
            "fecha": str(self.hoy), "casa": "bolsa", "compra": "", "venta": "-5",
        })
        self.assertEqual(TipoCambio.objects.count(), 0)

    def test_no_permite_duplicar_fecha_y_casa(self):
        datos = {"fecha": str(self.hoy), "casa": "bolsa", "compra": "", "venta": "1500"}
        self.client.post("/dashboard/cotizaciones/nueva/", datos)
        self.client.post("/dashboard/cotizaciones/nueva/", datos)
        self.assertEqual(TipoCambio.objects.count(), 1)

    def test_borrar(self):
        tc = TipoCambio.objects.create(fecha=self.hoy, casa="bolsa", venta=Decimal("1"), fuente="manual")
        self.client.post(f"/dashboard/cotizaciones/{tc.pk}/eliminar/")
        self.assertEqual(TipoCambio.objects.count(), 0)

    def test_boton_sincronizar_usa_la_api(self):
        respuesta = {"compra": 1500, "venta": 1520, "fechaActualizacion": "2026-08-26T19:00:00.000Z"}
        with patch("webapp.cotizaciones._pedir", return_value=respuesta):
            self.client.post("/dashboard/cotizaciones/sincronizar/")
        self.assertEqual(TipoCambio.objects.count(), 1)

    def test_si_la_api_falla_avisa_y_no_rompe(self):
        with patch("webapp.cotizaciones._pedir", side_effect=ErrorCotizaciones("sin red")):
            resp = self.client.post("/dashboard/cotizaciones/sincronizar/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TipoCambio.objects.count(), 0)


@override_settings(**SEGURO)
class FacturaFormMonedaTests(BaseCRM):
    def _datos(self, **extra):
        datos = {
            "proyecto": self.proyecto.id, "fecha": str(self.hoy), "fecha_vencimiento": "",
            "monto": "1000", "moneda": "ARS", "estado": "pendiente",
            "metodo_pago": "", "observaciones": "",
        }
        datos.update(extra)
        return datos

    def test_completa_el_vencimiento_solo(self):
        self.client.post("/dashboard/facturas/nueva/", self._datos())
        f = Factura.objects.get()
        self.assertEqual(f.fecha_vencimiento, self.hoy + timedelta(days=15))

    def test_respeta_el_vencimiento_cargado(self):
        self.client.post("/dashboard/facturas/nueva/",
                         self._datos(fecha_vencimiento=str(self.hoy + timedelta(days=60))))
        self.assertEqual(Factura.objects.get().fecha_vencimiento, self.hoy + timedelta(days=60))

    def test_bloquea_moneda_distinta_a_la_del_proyecto(self):
        resp = self.client.post("/dashboard/facturas/nueva/", self._datos(moneda="USD"))
        self.assertIn("moneda", resp.context["form"].errors)
        self.assertEqual(Factura.objects.count(), 0)

    def test_deja_pasar_si_se_confirma_a_proposito(self):
        self.client.post("/dashboard/facturas/nueva/",
                         self._datos(moneda="USD", permitir_otra_moneda="on"))
        self.assertEqual(Factura.objects.count(), 1)


class ComandosTests(BaseCRM):
    def test_resync_cobrado_no_escribe_sin_aplicar(self):
        Proyecto.objects.filter(pk=self.proyecto.pk).update(monto_cobrado=Decimal("99999"))
        salida = StringIO()
        call_command("resync_cobrado", stdout=salida)

        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("99999"))
        self.assertIn("desfasado", salida.getvalue())

    def test_resync_cobrado_corrige_con_aplicar(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("500"),
                               moneda="ARS", estado="cobrado")
        Proyecto.objects.filter(pk=self.proyecto.pk).update(monto_cobrado=Decimal("99999"))

        call_command("resync_cobrado", "--aplicar", stdout=StringIO())
        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.monto_cobrado, Decimal("500"))

    def test_resync_cobrado_reporta_monedas_mezcladas(self):
        Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("100"),
                               moneda="USD", estado="cobrado")
        salida = StringIO()
        call_command("resync_cobrado", stdout=salida)
        self.assertIn("moneda distinta", salida.getvalue())

    def test_congelar_tipos_cambio(self):
        TipoCambio.objects.create(fecha=self.hoy, casa="bolsa", venta=Decimal("1000"), fuente="api")
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1000"),
                                   moneda="ARS", estado="cobrado")
        Factura.objects.filter(pk=f.pk).update(tipo_cambio=None)

        call_command("congelar_tipos_cambio", "--aplicar", stdout=StringIO())
        f.refresh_from_db()
        self.assertEqual(f.tipo_cambio, Decimal("1000"))

    def test_congelar_avisa_de_las_que_no_puede(self):
        f = Factura.objects.create(proyecto=self.proyecto, fecha=self.hoy, monto=Decimal("1000"),
                                   moneda="ARS", estado="cobrado")
        Factura.objects.filter(pk=f.pk).update(tipo_cambio=None)

        salida = StringIO()
        call_command("congelar_tipos_cambio", "--aplicar", stdout=salida)
        self.assertIn("sin cotización", salida.getvalue())


@override_settings(**LOCMEM)
class TareaCotizacionTests(BaseCRM):
    def test_avisa_si_no_hay_cotizacion_fresca(self):
        from .tasks import sincronizar_cotizaciones as tarea
        with patch("webapp.tasks.sincronizar", return_value={"creados": 0, "actualizados": 0, "errores": ["sin red"]}):
            tarea()
        self.assertTrue(any("cotización" in m.subject for m in mail.outbox))

    def test_no_avisa_si_esta_al_dia(self):
        from .tasks import sincronizar_cotizaciones as tarea
        TipoCambio.objects.create(fecha=self.hoy, casa="bolsa", venta=Decimal("1000"), fuente="api")
        with patch("webapp.tasks.sincronizar", return_value={"creados": 1, "actualizados": 0, "errores": []}):
            tarea()
        self.assertEqual(len(mail.outbox), 0)


# ------------------------------------------------------------------ tareas

@override_settings(**SEGURO)
class TareaTests(BaseCRM):
    def _tarea(self, **kwargs):
        datos = dict(titulo="Llamar a ACME", prioridad="media")
        datos.update(kwargs)
        return Tarea.objects.create(**datos)

    def test_completar_pone_la_marca_de_tiempo(self):
        t = self._tarea()
        self.assertIsNone(t.completada_en)
        t.completada = True
        t.save()
        self.assertIsNotNone(t.completada_en)

    def test_reabrir_limpia_la_marca(self):
        t = self._tarea(completada=True)
        self.assertIsNotNone(t.completada_en)
        t.completada = False
        t.save()
        self.assertIsNone(t.completada_en)

    def test_vencida_y_de_hoy(self):
        vencida = self._tarea(vence=self.hoy - timedelta(days=2))
        de_hoy = self._tarea(vence=self.hoy)
        futura = self._tarea(vence=self.hoy + timedelta(days=3))

        self.assertTrue(vencida.esta_vencida)
        self.assertEqual(vencida.dias_para_vencer, -2)
        self.assertTrue(de_hoy.es_hoy)
        self.assertFalse(futura.esta_vencida)
        self.assertTrue(futura.es_esta_semana)

    def test_una_tarea_completada_nunca_figura_vencida(self):
        t = self._tarea(vence=self.hoy - timedelta(days=5), completada=True)
        self.assertFalse(t.esta_vencida)
        self.assertIsNone(t.dias_para_vencer)

    def test_toggle_por_post_normal(self):
        t = self._tarea()
        self.client.post(f"/dashboard/tareas/{t.id}/toggle/")
        t.refresh_from_db()
        self.assertTrue(t.completada)

        self.client.post(f"/dashboard/tareas/{t.id}/toggle/")
        t.refresh_from_db()
        self.assertFalse(t.completada)

    def test_toggle_por_ajax_devuelve_json(self):
        t = self._tarea()
        resp = self.client.post(f"/dashboard/tareas/{t.id}/toggle/",
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"ok": True, "completada": True, "id": t.id})

    def test_alta_rapida_asigna_al_usuario(self):
        self.client.post("/dashboard/tareas/rapida/",
                         {"titulo": "Mandar propuesta", "vence": str(self.hoy), "prioridad": "alta"})
        t = Tarea.objects.get()
        self.assertEqual(t.asignada_a, self.user)
        self.assertEqual(t.prioridad, "alta")

    def test_alta_rapida_sin_titulo_no_crea_nada(self):
        self.client.post("/dashboard/tareas/rapida/", {"titulo": "", "prioridad": "media"})
        self.assertEqual(Tarea.objects.count(), 0)

    def test_alta_rapida_puede_atarse_a_un_proyecto(self):
        self.client.post("/dashboard/tareas/rapida/", {
            "titulo": "Revisar diseño", "prioridad": "media", "proyecto": self.proyecto.id,
        })
        self.assertEqual(Tarea.objects.get().proyecto, self.proyecto)

    def test_agrupacion_por_urgencia(self):
        self._tarea(titulo="vencida", vence=self.hoy - timedelta(days=1))
        self._tarea(titulo="hoy", vence=self.hoy)
        self._tarea(titulo="semana", vence=self.hoy + timedelta(days=3))
        self._tarea(titulo="lejos", vence=self.hoy + timedelta(days=30))
        self._tarea(titulo="sin fecha")
        self._tarea(titulo="hecha", vence=self.hoy, completada=True)

        grupos = self.client.get("/dashboard/tareas/").context["grupos"]
        self.assertEqual([t.titulo for t in grupos["vencidas"]], ["vencida"])
        self.assertEqual([t.titulo for t in grupos["hoy"]], ["hoy"])
        self.assertEqual([t.titulo for t in grupos["semana"]], ["semana"])
        self.assertEqual([t.titulo for t in grupos["despues"]], ["lejos"])
        self.assertEqual([t.titulo for t in grupos["sin_fecha"]], ["sin fecha"])

    def test_dentro_del_grupo_manda_la_prioridad(self):
        self._tarea(titulo="baja", vence=self.hoy, prioridad="baja")
        self._tarea(titulo="alta", vence=self.hoy, prioridad="alta")
        self._tarea(titulo="media", vence=self.hoy, prioridad="media")

        grupos = self.client.get("/dashboard/tareas/").context["grupos"]
        self.assertEqual([t.titulo for t in grupos["hoy"]], ["alta", "media", "baja"])

    def test_completar_todas_las_vencidas(self):
        for i in range(3):
            self._tarea(titulo=f"v{i}", vence=self.hoy - timedelta(days=i + 1))
        self._tarea(titulo="hoy", vence=self.hoy)

        self.client.post("/dashboard/tareas/completar-vencidas/")
        self.assertEqual(Tarea.objects.filter(completada=True).count(), 3)
        self.assertFalse(Tarea.objects.get(titulo="hoy").completada)

    def test_el_dashboard_muestra_las_tareas(self):
        self._tarea(titulo="urgente", vence=self.hoy - timedelta(days=1))
        ctx = self.client.get("/dashboard/").context
        self.assertEqual(ctx["tareas_pendientes"], 1)
        self.assertEqual(ctx["tareas_vencidas"], 1)
        self.assertEqual([t.titulo for t in ctx["tareas"]["vencidas"]], ["urgente"])

    def test_el_contador_del_sidebar_esta_en_todas_las_paginas(self):
        self._tarea()
        for ruta in ["/dashboard/", "/clientes/", "/proyectos/"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).context["tareas_pendientes_nav"], 1)

    def test_borrar_el_cliente_se_lleva_sus_tareas(self):
        self._tarea(cliente=self.cliente)
        self.client.post(f"/clientes/{self.cliente.id}/eliminar/")
        self.assertEqual(Tarea.objects.count(), 0)


# ------------------------------------------------------------------ kanban

@override_settings(**SEGURO)
class KanbanTests(BaseCRM):
    def setUp(self):
        super().setUp()
        self.pipeline = Pipeline.objects.create(nombre="Ventas")
        self.pipeline.crear_etapas_por_defecto()
        self.etapas = list(self.pipeline.etapas.all())
        self.nuevo = self.etapas[0]
        self.ganado = self.pipeline.etapas.get(tipo="ganada")
        self.perdido = self.pipeline.etapas.get(tipo="perdida")

    def _deal(self, **kwargs):
        datos = dict(pipeline=self.pipeline, etapa=self.nuevo, titulo="Sitio nuevo",
                     cliente=self.cliente, valor=Decimal("100000"), moneda="ARS")
        datos.update(kwargs)
        return Deal.objects.create(**datos)

    def test_las_etapas_por_defecto_salen_en_orden(self):
        self.assertEqual(
            [e.nombre for e in self.etapas],
            ["Nuevo lead", "Contactado", "Propuesta enviada", "Negociación", "Ganado", "Perdido"],
        )
        self.assertEqual(self.ganado.tipo, "ganada")

    def test_tablero_responde_y_arma_las_columnas(self):
        self._deal()
        resp = self.client.get(f"/dashboard/kanban/{self.pipeline.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["columnas"]), 6)
        self.assertEqual(resp.context["columnas"][0]["resumen"]["cantidad"], 1)

    def test_tablero_sin_pipelines_no_rompe(self):
        Pipeline.objects.all().delete()
        resp = self.client.get("/dashboard/kanban/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["pipeline"])

    def test_mover_a_otra_etapa(self):
        d = self._deal()
        destino = self.etapas[2]
        self.client.post(f"/dashboard/deals/{d.pk}/mover/", {"etapa": destino.pk})
        d.refresh_from_db()
        self.assertEqual(d.etapa, destino)

    def test_mover_por_ajax_reordena_la_columna(self):
        a, b, c = self._deal(titulo="A"), self._deal(titulo="B"), self._deal(titulo="C")
        destino = self.etapas[1]

        resp = self.client.post(
            f"/dashboard/deals/{c.pk}/mover/",
            {"etapa": destino.pk, "orden[]": [c.pk, a.pk, b.pk]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertTrue(resp.json()["ok"])
        c.refresh_from_db(); a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(c.etapa, destino)
        self.assertEqual([c.orden, a.orden, b.orden], [0, 1, 2])

    def test_no_deja_mover_a_una_etapa_de_otro_pipeline(self):
        otro = Pipeline.objects.create(nombre="Otro")
        ajena = Etapa.objects.create(pipeline=otro, nombre="Ajena", orden=0)
        d = self._deal()

        resp = self.client.post(f"/dashboard/deals/{d.pk}/mover/", {"etapa": ajena.pk},
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 400)
        d.refresh_from_db()
        self.assertEqual(d.etapa, self.nuevo)

    def test_mover_por_get_no_hace_nada(self):
        d = self._deal()
        self.assertEqual(self.client.get(f"/dashboard/deals/{d.pk}/mover/").status_code, 405)

    def test_entrar_a_una_etapa_de_cierre_pone_la_fecha(self):
        d = self._deal()
        self.assertIsNone(d.cerrado_en)
        d.etapa = self.ganado
        d.save()
        self.assertEqual(d.cerrado_en, self.hoy)

    def test_salir_de_una_etapa_de_cierre_borra_la_fecha(self):
        d = self._deal(etapa=self.ganado)
        self.assertIsNotNone(d.cerrado_en)
        d.etapa = self.nuevo
        d.save()
        self.assertIsNone(d.cerrado_en)

    def test_convertir_deal_ganado_en_proyecto(self):
        d = self._deal(etapa=self.ganado, titulo="Rediseño ACME", valor=Decimal("500000"))
        self.client.post(f"/dashboard/deals/{d.pk}/convertir/")

        d.refresh_from_db()
        self.assertIsNotNone(d.proyecto)
        self.assertEqual(d.proyecto.nombre, "Rediseño ACME")
        self.assertEqual(d.proyecto.precio, Decimal("500000"))
        self.assertEqual(d.proyecto.cliente, self.cliente)

    def test_no_convierte_dos_veces(self):
        d = self._deal(etapa=self.ganado)
        self.client.post(f"/dashboard/deals/{d.pk}/convertir/")
        self.client.post(f"/dashboard/deals/{d.pk}/convertir/")
        self.assertEqual(Proyecto.objects.filter(nombre=d.titulo).count(), 1)

    def test_sin_cliente_no_se_puede_convertir(self):
        d = self._deal(etapa=self.ganado, cliente=None)
        self.client.post(f"/dashboard/deals/{d.pk}/convertir/")
        d.refresh_from_db()
        self.assertIsNone(d.proyecto)

    def test_tasa_de_conversion(self):
        self._deal(titulo="g1", etapa=self.ganado)
        self._deal(titulo="g2", etapa=self.ganado)
        self._deal(titulo="p1", etapa=self.perdido)
        self._deal(titulo="abierto", etapa=self.nuevo)

        ctx = self.client.get(f"/dashboard/kanban/{self.pipeline.pk}/").context
        self.assertEqual(ctx["conversion"], 66)  # 2 de 3 cerrados
        self.assertEqual(ctx["abiertos"], 1)

    def test_sin_deals_cerrados_no_inventa_conversion(self):
        self._deal()
        self.assertIsNone(self.client.get(f"/dashboard/kanban/{self.pipeline.pk}/").context["conversion"])

    def test_valor_del_pipeline_en_dolares(self):
        TipoCambio.objects.create(fecha=self.hoy, casa="bolsa", venta=Decimal("1000"), fuente="api")
        self._deal(valor=Decimal("100000"), moneda="ARS")
        self._deal(titulo="usd", valor=Decimal("250"), moneda="USD")

        ctx = self.client.get(f"/dashboard/kanban/{self.pipeline.pk}/").context
        self.assertEqual(ctx["valor_abierto"], Decimal("350.00"))

    def test_no_borra_una_etapa_con_deals(self):
        self._deal()
        self.client.post(f"/dashboard/kanban/etapa/{self.nuevo.pk}/eliminar/")
        self.assertTrue(Etapa.objects.filter(pk=self.nuevo.pk).exists())

    def test_borra_una_etapa_vacia(self):
        vacia = self.etapas[3]
        self.client.post(f"/dashboard/kanban/etapa/{vacia.pk}/eliminar/")
        self.assertFalse(Etapa.objects.filter(pk=vacia.pk).exists())

    def test_reordenar_etapas(self):
        primera, segunda = self.etapas[0], self.etapas[1]
        self.client.post(f"/dashboard/kanban/etapa/{segunda.pk}/mover/", {"direccion": "izquierda"})
        primera.refresh_from_db(); segunda.refresh_from_db()
        self.assertLess(segunda.orden, primera.orden)

    def test_no_se_puede_mover_mas_alla_del_borde(self):
        primera = self.etapas[0]
        self.client.post(f"/dashboard/kanban/etapa/{primera.pk}/mover/", {"direccion": "izquierda"})
        primera.refresh_from_db()
        self.assertEqual(primera.orden, 0)

    def test_crear_pipeline_con_etapas_por_defecto(self):
        self.client.post("/dashboard/kanban/pipeline/nuevo/",
                         {"nombre": "Onboarding", "descripcion": "", "activo": "on",
                          "etapas_por_defecto": "1"})
        nuevo = Pipeline.objects.get(nombre="Onboarding")
        self.assertEqual(nuevo.etapas.count(), 6)

    def test_crear_pipeline_vacio(self):
        self.client.post("/dashboard/kanban/pipeline/nuevo/",
                         {"nombre": "Vacío", "descripcion": "", "activo": "on"})
        self.assertEqual(Pipeline.objects.get(nombre="Vacío").etapas.count(), 0)

    def test_crear_deal_desde_el_tablero(self):
        self.client.post(f"/dashboard/kanban/{self.pipeline.pk}/deal/nuevo/", {
            "titulo": "Landing nueva", "cliente": self.cliente.id, "etapa": self.nuevo.pk,
            "valor": "80000", "moneda": "ARS", "cierre_estimado": "", "descripcion": "", "motivo_cierre": "",
        })
        d = Deal.objects.get()
        self.assertEqual(d.pipeline, self.pipeline)
        self.assertEqual(d.etapa, self.nuevo)

    def test_no_acepta_valor_negativo(self):
        resp = self.client.post(f"/dashboard/kanban/{self.pipeline.pk}/deal/nuevo/", {
            "titulo": "X", "cliente": self.cliente.id, "etapa": self.nuevo.pk,
            "valor": "-5", "moneda": "ARS", "cierre_estimado": "", "descripcion": "", "motivo_cierre": "",
        })
        self.assertIn("valor", resp.context["form"].errors)
        self.assertEqual(Deal.objects.count(), 0)

    def test_deal_demorado(self):
        d = self._deal(cierre_estimado=self.hoy - timedelta(days=5))
        self.assertTrue(d.esta_demorado)
        self.assertEqual(d.dias_para_cierre, -5)

    def test_un_deal_cerrado_no_figura_demorado(self):
        d = self._deal(etapa=self.ganado, cierre_estimado=self.hoy - timedelta(days=5))
        self.assertFalse(d.esta_demorado)

    def test_busqueda_en_el_tablero(self):
        self._deal(titulo="Landing")
        self._deal(titulo="Ecommerce")
        ctx = self.client.get(f"/dashboard/kanban/{self.pipeline.pk}/?q=Landing").context
        titulos = [d.titulo for col in ctx["columnas"] for d in col["deals"]]
        self.assertEqual(titulos, ["Landing"])

    def test_paginas_del_kanban_responden(self):
        d = self._deal()
        rutas = [
            "/dashboard/kanban/", f"/dashboard/kanban/{self.pipeline.pk}/",
            "/dashboard/kanban/pipeline/nuevo/",
            f"/dashboard/kanban/pipeline/{self.pipeline.pk}/config/",
            f"/dashboard/kanban/pipeline/{self.pipeline.pk}/eliminar/",
            "/dashboard/deals/nuevo/", f"/dashboard/kanban/{self.pipeline.pk}/deal/nuevo/",
            f"/dashboard/deals/{d.pk}/", f"/dashboard/deals/{d.pk}/editar/",
            f"/dashboard/deals/{d.pk}/eliminar/",
            "/dashboard/tareas/", "/dashboard/tareas/nueva/", "/dashboard/tareas/?vista=completadas",
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_el_kanban_pide_login(self):
        anonimo = Client()
        for ruta in ["/dashboard/kanban/", "/dashboard/tareas/"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(anonimo.get(ruta).status_code, 302)
