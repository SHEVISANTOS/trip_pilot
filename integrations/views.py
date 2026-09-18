from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import TemplateView

from integrations.models import IntegrationCallLog


class IntegrationHealthView(PermissionRequiredMixin, TemplateView):
    """Custom Unfold admin page (registered via UNFOLD["SITE_VIEWS"]) showing
    last successful call time and error count per external API client —
    the health dashboard called for in the build spec, section 4.
    """

    template_name = "admin/integration_health.html"
    permission_required = "integrations.view_integrationcalllog"
    admin_site = None  # injected by UnfoldAdminSite.get_urls() via as_view(admin_site=...)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.admin_site is not None:
            context.update(self.admin_site.each_context(self.request))
        context["title"] = "Integration health"
        rows = []
        for provider, label in IntegrationCallLog.PROVIDER_CHOICES:
            logs = IntegrationCallLog.objects.filter(provider=provider)
            last_success = logs.filter(success=True).first()
            rows.append(
                {
                    "provider": label,
                    "last_success": last_success.called_at if last_success else None,
                    "error_count": logs.filter(success=False).count(),
                    "total_calls": logs.count(),
                }
            )
        context["rows"] = rows
        return context
