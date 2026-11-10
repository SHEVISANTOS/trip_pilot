from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from config.seo import StaticViewSitemap, robots_txt

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": {"static": StaticViewSitemap}}, name="sitemap"),
    # Only the Google OAuth + socialaccount error/connection views — not the
    # full allauth.urls, which bundles its own login/signup/password-reset
    # at this same "accounts/" prefix and would collide with accounts.urls
    # below (our own, already-built, site-styled versions of those).
    path("accounts/", include("allauth.socialaccount.providers.google.urls")),
    path("accounts/social/", include("allauth.socialaccount.urls")),
    path("accounts/", include("accounts.urls")),
    path("pdf/", include("pdfexport.urls")),
    # Before trips.urls below — its "" include has a catch-all-ish
    # "<int:pk>/" pattern, and legal's own URLs must resolve first.
    path("", include("legal.urls")),
    path("", include("trips.urls")),
]
