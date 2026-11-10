from django.conf import settings


def site(request):
    """SITE_URL in every template — canonical/Open Graph tags always point
    at the one real domain (settings.SITE_URL), not request.get_host(),
    which would vary depending on whether the request came in through the
    temporary *.vercel.app alias or the real domain.
    """
    return {"SITE_URL": settings.SITE_URL}
