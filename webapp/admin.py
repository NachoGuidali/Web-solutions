from django.contrib import admin
from .models import Cliente, Proyecto, Factura

admin.site.register(Cliente)
admin.site.register(Proyecto)
admin.site.register(Factura)
