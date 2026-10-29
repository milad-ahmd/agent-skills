from django.shortcuts import render
from rest_framework.views import APIView


def home(request):
    return render(request, "shop/home.html")


def checkout(request):
    if request.method == "POST":
        pass
    return render(request, "shop/checkout.html")


class ItemDelete:
    template_name = "shop/item_confirm_delete.html"


def archive(request, year):
    return render(request, "shop/home.html")


class ItemList(APIView):
    pass
