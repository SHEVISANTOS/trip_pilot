from django.urls import path

from trips import views

app_name = "trips"

urlpatterns = [
    path("", views.planner, name="planner"),
    path("<int:pk>/", views.results, name="results"),
    path("<int:pk>/optimize/", views.optimize, name="optimize"),
    path("<int:pk>/save/", views.save_trip, name="save_trip"),
    path("saved/<int:pk>/checklist/<int:index>/", views.toggle_checklist, name="toggle_checklist"),
]
