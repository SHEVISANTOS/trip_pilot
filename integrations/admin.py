from django.contrib import admin
from unfold.admin import ModelAdmin

from integrations.models import IntegrationCallLog


@admin.register(IntegrationCallLog)
class IntegrationCallLogAdmin(ModelAdmin):
    list_display = ("provider", "success", "called_at")
    list_filter = ("provider", "success")
    readonly_fields = ("provider", "called_at", "success", "detail")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
