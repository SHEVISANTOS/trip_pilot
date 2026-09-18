from datetime import timedelta

from django.db.models import Avg, Count
from django.utils import timezone

from trips.models import TripRequest


def dashboard_callback(request, context):
    """UNFOLD["DASHBOARD_CALLBACK"] — feeds the analytics cards rendered by
    templates/admin/index.html (Section 4 of the build spec).
    """
    week_ago = timezone.now() - timedelta(days=7)
    avg_budget = TripRequest.objects.aggregate(avg=Avg("budget"))["avg"] or 0
    context.update(
        {
            "trips_this_week": TripRequest.objects.filter(created_at__gte=week_ago).count(),
            "avg_budget": round(avg_budget, 2),
            "top_destinations": list(
                TripRequest.objects.values("destination").annotate(count=Count("id")).order_by("-count")[:5]
            ),
        }
    )
    return context
