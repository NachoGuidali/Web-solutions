from django.shortcuts import render

# Create your views here.

def home(request):
    context = {}
    return render(request, 'home.html', context)

def servicios(request):
    return render(request, 'servicios.html')

def about(request):
    return render(request, 'about.html')