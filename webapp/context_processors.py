from django.conf import settings
from django.utils import timezone

from .models import Tarea


def crm_badges(request):
    """Contadores que el sidebar muestra en todas las páginas del CRM."""
    usuario = getattr(request, "user", None)
    if not usuario or not usuario.is_authenticated:
        return {}

    pendientes = Tarea.objects.filter(completada=False)
    return {
        "tareas_pendientes_nav": pendientes.count(),
        "tareas_vencidas_nav": pendientes.filter(vence__lt=timezone.localdate()).count(),
    }


def sitio(request):
    """Dominio público para el canonical y las etiquetas Open Graph."""
    return {"sitio_url": settings.SITIO_URL}
