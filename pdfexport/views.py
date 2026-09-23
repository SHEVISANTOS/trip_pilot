from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

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
    try:
        # Imported here, not at module load: WeasyPrint needs the Pango/
        # Cairo/GDK-Pixbuf system libraries, which some hosts — serverless
        # platforms especially — don't ship. A missing .so raises OSError
        # on import; doing that at module load would take the whole site
        # down (every view imports this module's urls), not just this one.
        from weasyprint import HTML

        pdf_bytes = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    except OSError:
        return HttpResponse(
            "PDF export isn't available on this deployment (missing system libraries for PDF "
            "rendering). Your trip is still saved — try again from a deployment with those "
            "libraries installed.",
            status=503,
            content_type="text/plain",
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"trippilot-{trip_request.destination}.pdf".replace(" ", "-")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
