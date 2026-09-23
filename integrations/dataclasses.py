from dataclasses import dataclass, field


@dataclass
class FlightOffer:
    airline: str
    route: str
    stops: str
    duration: str
    price: float
    label: str = "Alternative"
    # Real deep link when the provider gives one (Travelpayouts always does);
    # pricing.budget fills in a search-engine fallback otherwise, so this is
    # never empty by the time a plan is persisted — "View / Book" always
    # goes somewhere real, never a dead modal.
    booking_url: str = ""


@dataclass
class HotelOffer:
    name: str
    area: str
    rating: str
    night: float
    total: float
    desc: str
    booking_url: str = ""


@dataclass
class Attraction:
    name: str
    cost: float
    desc: str
    optional: bool = False
    booking_url: str = ""


@dataclass
class VisaInfo:
    type: str
    method: str
    stay: str
    cost: float
    tag: str = "VERIFY"


@dataclass
class ExchangeRate:
    base: str
    target: str
    rate: float


@dataclass
class EsimBundle:
    name: str
    description: str
    data_mb: float
    unlimited: bool
    duration_days: int
    price: float


@dataclass
class TravelDataBundle:
    visa: VisaInfo
    flights: list[FlightOffer]
    hotels: list[HotelOffer]
    attractions: list[Attraction] = field(default_factory=list)
