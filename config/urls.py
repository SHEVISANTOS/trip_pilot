from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Only the Google OAuth + socialaccount error/connection views — not the
    # full allauth.urls, which bundles its own login/signup/password-reset
    # at this same "accounts/" prefix and would collide with accounts.urls
    # below (our own, already-built, site-styled versions of those).
    path("accounts/", include("allauth.socialaccount.providers.google.urls")),
    path("accounts/social/", include("allauth.socialaccount.urls")),
    path("accounts/", include("accounts.urls")),
    path("pdf/", include("pdfexport.urls")),
    path("", include("trips.urls")),
]
