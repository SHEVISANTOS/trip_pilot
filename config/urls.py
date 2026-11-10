from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import RedirectView

from config.seo import StaticViewSitemap, robots_txt

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": {"static": StaticViewSitemap}}, name="sitemap"),
    # A URL-name-only alias, not a real page of its own: allauth's socialaccount
    # views (LoginCancelledView/LoginErrorView/SignupView — reached whenever
    # Google sign-in is cancelled or errors out) call the Python-level
    # reverse("account_login") directly, unguarded, inside their own
    # get_context_data()/dispatch() — not a template {% url %} tag, which
    # Django's `as var` form would have swallowed safely. Since we mount only
    # allauth.socialaccount.urls, not the full allauth.urls that would define
    # "account_login" itself, that reverse() call raised NoReverseMatch and
    # crashed with a 500 (confirmed live via a DEBUG=False test). Giving the
    # name a target — even one that just redirects straight back to our own
    # login page — is enough; query_string=True preserves a ?next= allauth
    # may have appended.
    path(
        "accounts/continue/",
        RedirectView.as_view(pattern_name="accounts:login", query_string=True),
        name="account_login",
    ),
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
