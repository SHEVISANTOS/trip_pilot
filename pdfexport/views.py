import io
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

from trips.models import SavedTrip

logger = logging.getLogger(__name__)


def _render_pdf_bytes(html_string: str) -> bytes:
    """WeasyPrint first (pixel-accurate, used whenever its Pango/Cairo/
    GDK-Pixbuf system libraries are present — true on a normal server, not
    on Vercel's Python runtime, which has no apt-get/custom-container step
    to install them: confirmed no supported fix exists there short of a
    hosted rendering API). xhtml2pdf is the fallback for exactly that case —
    pure Python, no system libraries, but a much more limited CSS 2.1-ish
    renderer with no emoji font. templates/pdfexport/itinerary_pdf.html is
    written to render correctly under both (literal colors, <table> layout,
    no CSS variables/grid/flexbox, no emoji) rather than targeting
    WeasyPrint's fuller CSS support and leaving xhtml2pdf a broken mess —
    verified live: reusing the main site's grid/flexbox/var()-based
    style.css here rendered as an unstyled, colorless, stacked-box document
    with blank boxes where every icon should be.
    """
    try:
        from weasyprint import HTML

        return HTML(string=html_string).write_pdf()
    except OSError:
        from xhtml2pdf import pisa

        buffer = io.BytesIO()
        result = pisa.CreatePDF(html_string, dest=buffer)
        if result.err:
            raise RuntimeError(f"xhtml2pdf reported {result.err} error(s) rendering the itinerary")
        return buffer.getvalue()


@login_required
def export_pdf(request, pk):
    saved_trip = get_object_or_404(SavedTrip, pk=pk, user=request.user)
    trip_request = saved_trip.trip_request
    breakdown = trip_request.budget_breakdown
    is_multi_city = len(breakdown.legs) > 1
    route = " → ".join([trip_request.departure] + [leg["city"] for leg in breakdown.legs] + [trip_request.departure])
    html_string = render_to_string(
        "pdfexport/itinerary_pdf.html",
        {
            "trip_request": trip_request,
            "breakdown": breakdown,
            "itinerary": trip_request.itinerary,
            "saved_trip": saved_trip,
            "is_multi_city": is_multi_city,
            "route": route,
        },
    )
    try:
        pdf_bytes = _render_pdf_bytes(html_string)
    except Exception as exc:  # noqa: BLE001 - PDF export degrading gracefully beats a 500
        # Both renderers imported lazily inside _render_pdf_bytes, not at
        # module load — a missing WeasyPrint .so raises OSError on import;
        # doing that at module load would take the whole site down (every
        # view imports this module's urls), not just this one. This is the
        # last-resort guard for the (now rare) case where xhtml2pdf itself
        # also fails on some malformed trip data.
        logger.warning("PDF export failed for SavedTrip %s: %s", pk, exc)
        return HttpResponse(
            "PDF export isn't available right now. Your trip is still saved — try again shortly.",
            status=503,
            content_type="text/plain",
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"trippilot-{trip_request.destination}.pdf".replace(" ", "-")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
