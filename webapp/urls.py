from django.urls import path
from .views import home, servicios, about

urlpatterns = [
    path('', home, name='home'),
    path('servicios', servicios, name='servicios'),
    path('about', about, name='about')
]