from datetime import date

from django.conf import settings
from django.views.generic import TemplateView

# Bumped by hand whenever privacy.html/terms.html actually change — not
# auto-generated from file mtime, so an unrelated edit (fixing a typo in a
# comment, a template reformat) doesn't silently claim a new "Last updated"
# date for content that didn't change.
PRIVACY_LAST_UPDATED = date(2026, 11, 10)
TERMS_LAST_UPDATED = date(2026, 11, 10)


class PrivacyView(TemplateView):
    template_name = "legal/privacy.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["last_updated"] = PRIVACY_LAST_UPDATED
        context["support_email"] = settings.SUPPORT_EMAIL
        return context


class TermsView(TemplateView):
    template_name = "legal/terms.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["last_updated"] = TERMS_LAST_UPDATED
        context["support_email"] = settings.SUPPORT_EMAIL
        return context
