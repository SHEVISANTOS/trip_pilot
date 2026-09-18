from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from unfold.admin import ModelAdmin, StackedInline

from accounts.models import Profile


class ProfileInline(StackedInline):
    model = Profile
    can_delete = False


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    inlines = [ProfileInline]
