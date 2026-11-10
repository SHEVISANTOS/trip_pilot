from django.urls import path

from legal import views

app_name = "legal"

urlpatterns = [
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),
    path("terms/", views.TermsView.as_view(), name="terms"),
]
