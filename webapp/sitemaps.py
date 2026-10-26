from types import SimpleNamespace

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

# Sin django.contrib.sites el sitemap usaría el host del request (www o no);
# lo fijamos al dominio público para que coincida con el canonical.
SITIO = SimpleNamespace(domain=settings.SITIO_URL.split("://", 1)[1], name="SupReg Solutions")


class PaginasPublicasSitemap(Sitemap):
    protocol = "https"
    changefreq = "monthly"

    # (nombre de la url, prioridad)
    paginas = [
        ("home", 1.0),
        ("servicios", 0.9),
        ("crm", 0.8),
        ("comanda", 0.8),
        ("conector_whatsapp", 0.8),
        ("analytics", 0.8),
        ("about", 0.6),
        ("privacidad", 0.2),
        ("terminos", 0.2),
    ]

    def items(self):
        return self.paginas

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]

    def get_urls(self, page=1, site=None, protocol=None):
        return super().get_urls(page=page, site=SITIO, protocol=self.protocol)
