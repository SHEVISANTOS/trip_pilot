from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from weasyprint import HTML

from trips.models import SavedTrip


@login_required
def export_pdf(request, pk):
    saved_trip = get_object_or_404(SavedTrip, pk=pk, user=request.user)
    trip_request = saved_trip.trip_request
    html_string = render_to_string(
        "pdfexport/itinerary_pdf.html",
        {
            "trip_request": trip_request,
            "breakdown": trip_request.budget_breakdown,
            "itinerary": trip_request.itinerary,
            "saved_trip": saved_trip,
        },
    )
    pdf_bytes = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"trippilot-{trip_request.destination}.pdf".replace(" ", "-")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
