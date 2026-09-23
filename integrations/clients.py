from dataclasses import dataclass, field

from integrations.activities import OpenTripMapClient
from integrations.esim import EsimGoClient
from integrations.exchange import ExchangeRateClient
from integrations.hotels import LiteApiHotelClient
from integrations.maps import MapsClient
from integrations.serpapi import (
    SerpApiAttractionsClient,
    SerpApiFlightsClient,
    SerpApiHotelsClient,
    SerpApiSearchClient,
)
from integrations.stayapi import StayApiHotelClient
from integrations.travelpayouts import TravelpayoutsClient
from integrations.visa import PassportIndexVisaClient


@dataclass
class IntegrationClients:
    """Bundle passed into pricing.budget.build_plan() so the pricing package
    stays framework-agnostic and only depends on this bundle's interface,
    not on Django or any specific provider.
    """

    # Travelpayouts is the fallback now — SerpApi (flights_backup, despite
    # the name) is tried first for real Google Flights prices; Travelpayouts'
    # cached fare only kicks in when SerpApi is unconfigured, out of its
    # 100/month quota, or has nothing for the route. See pricing.budget.
    flights: TravelpayoutsClient = field(default_factory=TravelpayoutsClient)
    flights_backup: SerpApiFlightsClient = field(default_factory=SerpApiFlightsClient)
    # Same story for attractions: activities_serp (Google's "Top sights") is
    # tried first — real names, ratings and entry prices in one call.
    # OpenTripMap (its own wikidata/osm link, category-estimated prices) is
    # the fallback for whenever SerpApi is unconfigured, out of quota, or has
    # nothing for the destination.
    activities: OpenTripMapClient = field(default_factory=OpenTripMapClient)
    activities_serp: SerpApiAttractionsClient = field(default_factory=SerpApiAttractionsClient)
    visa: PassportIndexVisaClient = field(default_factory=PassportIndexVisaClient)
    exchange: ExchangeRateClient = field(default_factory=ExchangeRateClient)
    maps: MapsClient = field(default_factory=MapsClient)
    esim: EsimGoClient = field(default_factory=EsimGoClient)
    # Same story for hotels: hotels_serp (real Google Hotels) is tried
    # first; LiteAPI and StayAPI are the fallbacks, StayAPI last since its
    # free tier is a scarcer 50 lifetime requests, not per-month.
    hotels: LiteApiHotelClient = field(default_factory=LiteApiHotelClient)
    hotels_serp: SerpApiHotelsClient = field(default_factory=SerpApiHotelsClient)
    hotels_backup: StayApiHotelClient = field(default_factory=StayApiHotelClient)
    # Gap-filler only: activities_serp already gives most attractions a real
    # link in the same call that fetches their name/price, so this is only
    # reached for an attraction that came from the OpenTripMap fallback
    # *and* had neither a wikidata nor an osm tag (see
    # pricing.budget.fill_missing_booking_urls) — not a per-attraction call
    # every time, which the monthly quota can't sustain.
    attraction_links: SerpApiSearchClient = field(default_factory=SerpApiSearchClient)
