from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("cart/checkout/", views.checkout, name="checkout"),
    path("items/<int:pk>/delete/", views.ItemDelete.as_view(), name="item-delete"),
    re_path(r"^archive/(?P<year>[0-9]{4})/$", views.archive),
    path("api/items/", views.ItemList.as_view()),
    path("blog/", include("blog.urls")),
]
