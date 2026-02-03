from django.urls import path

from . import views


app_name = "rifas"

urlpatterns = [
    path("", views.home, name="home"),
    path("mis-boletos/", views.my_tickets, name="my_tickets"),
    path("historial/", views.raffle_history, name="raffle_history"),
    path("rifa/<slug:slug>/", views.raffle_detail, name="raffle_detail"),
    path("rifa/<slug:slug>/comprar/", views.buy_ticket, name="buy_ticket"),
    path("gracias/<int:purchase_id>/", views.thanks, name="thanks"),
]

