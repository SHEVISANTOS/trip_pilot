"""Illustrative sample data, ported verbatim from the original app.js `demo` object.

Used as the fallback whenever a live provider client is unavailable, unconfigured,
or errors out. Always labelled "Estimated — live data unavailable" by the views
that render it.
"""
import re

from integrations.dataclasses import Attraction, FlightOffer, HotelOffer, TravelDataBundle, VisaInfo

DEMO_VISA = VisaInfo(
    type="Tourist entry requirement",
    method="Official immigration requirements must be verified for the traveller's nationality before departure.",
    stay="Check permitted length of stay",
    cost=60,
    tag="VERIFY",
)

DEMO_FLIGHTS = [
    FlightOffer(
        airline="Turkish Airlines",
        route="DAR → IST → DAR",
        stops="1 stop",
        duration="Approx. 11–15 hrs each way",
        price=1650,
        label="Recommended",
    ),
    FlightOffer(
        airline="Qatar Airways",
        route="DAR → DOH → IST → DAR",
        stops="1 stop",
        duration="Approx. 13–17 hrs each way",
        price=1480,
        label="Cheapest",
    ),
    FlightOffer(
        airline="Ethiopian Airlines",
        route="DAR → ADD → IST → DAR",
        stops="1 stop",
        duration="Approx. 12–16 hrs each way",
        price=1540,
        label="Alternative",
    ),
]

DEMO_HOTELS = [
    HotelOffer(
        name="Sultanahmet Comfort Hotel",
        area="Sultanahmet",
        rating="4★",
        night=135,
        total=1200,
        desc="Central location, breakfast included, family-friendly.",
    ),
    HotelOffer(
        name="Taksim City Residence",
        area="Taksim",
        rating="4★",
        night=120,
        total=1080,
        desc="Modern rooms with easy access to restaurants and transport.",
    ),
    HotelOffer(
        name="Old City Boutique Hotel",
        area="Fatih",
        rating="3★",
        night=85,
        total=760,
        desc="Value option near major historic attractions.",
    ),
]

DEMO_ATTRACTIONS = [
    Attraction(name="Hagia Sophia & Sultanahmet", cost=30, desc="Historic district and landmark visit."),
    Attraction(name="Topkapı Palace", cost=45, desc="Palace complex and museum experience."),
    Attraction(name="Bosphorus Cruise", cost=55, desc="Half-day sightseeing cruise."),
    Attraction(name="Grand Bazaar", cost=0, desc="Shopping and cultural experience."),
    Attraction(name="Galata Tower", cost=20, desc="City views and historic neighbourhood."),
    Attraction(
        name="Cappadocia Day Experience",
        cost=180,
        desc="Optional premium excursion; transport/tour varies.",
        optional=True,
    ),
]

_NAMED_ATTRACTIONS = re.compile(
    r"Hagia Sophia|Sultanahmet|Topkapı Palace|Bosphorus Cruise|Grand Bazaar|Galata Tower|Cappadocia Day Experience"
)


def sample_data_for(destination: str) -> TravelDataBundle:
    """Port of app.js's sampleNames(): Istanbul/Türkiye keeps the curated demo
    content verbatim, any other destination gets the same numbers with names
    genericized to that destination.
    """
    d = (destination or "").lower()
    if "istanbul" in d or "turk" in d:
        return TravelDataBundle(
            visa=DEMO_VISA, flights=DEMO_FLIGHTS, hotels=DEMO_HOTELS, attractions=DEMO_ATTRACTIONS
        )

    generic_visa = VisaInfo(
        type="Destination entry requirements",
        method="Check official immigration authority for nationality-specific requirements.",
        stay="Verify before booking",
        cost=60,
        tag="VERIFY",
    )
    generic_flights = [
        FlightOffer(
            airline=f.airline,
            route=f"Your departure → {destination} → Return",
            stops=f.stops,
            duration=f.duration,
            price=f.price,
            label=f.label,
        )
        for f in DEMO_FLIGHTS
    ]
    hotel_names = [
        f"{destination} Central Hotel",
        f"Grand {destination} Residence",
        f"{destination} Boutique Stay",
    ]
    generic_hotels = [
        HotelOffer(name=hotel_names[i], area=h.area, rating=h.rating, night=h.night, total=h.total, desc=h.desc)
        for i, h in enumerate(DEMO_HOTELS)
    ]
    generic_attractions = [
        Attraction(
            name=_NAMED_ATTRACTIONS.sub(f"{destination} sightseeing experience", a.name),
            cost=a.cost,
            desc=a.desc,
            optional=a.optional,
        )
        for a in DEMO_ATTRACTIONS
    ]
    return TravelDataBundle(
        visa=generic_visa, flights=generic_flights, hotels=generic_hotels, attractions=generic_attractions
    )
