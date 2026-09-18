from django.urls import path

from pdfexport import views

app_name = "pdfexport"

urlpatterns = [
    path("<int:pk>/", views.export_pdf, name="export"),
]
