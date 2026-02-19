"""
URL configuration for rifa_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import TemplateView

from rifas.media_views import media_serve
from rifas import views as rifas_views
from rifas.sitemaps import sitemaps as rifas_sitemaps

urlpatterns = [
    # Admin password recovery (must be BEFORE admin.site.urls)
    path("admin/password-reset/", rifas_views.admin_password_reset, name="admin_password_reset"),
    path("admin/boleto-ganador/", rifas_views.admin_winner_search, name="admin_winner_search"),
    path("admin/calculadora-rifa/", rifas_views.admin_raffle_calculator, name="admin_raffle_calculator"),
    path("admin/rendimiento-rifa/", rifas_views.admin_raffle_performance, name="admin_raffle_performance"),
    path('admin/', admin.site.urls),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    path("sitemap.xml", sitemap, {"sitemaps": rifas_sitemaps}),
    path('', include('rifas.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Production: serve ONLY public media + staff-only private media.
    urlpatterns += [path("media/<path:path>", media_serve, name="media_serve")]