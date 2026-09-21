from dataclasses import dataclass, field


@dataclass
class FlightOffer:
    airline: str
    route: str
    stops: str
    duration: str
    price: float
    label: str = "Alternative"


@dataclass
class HotelOffer:
    name: str
    area: str
    rating: str
    night: float
    total: float
    desc: str


@dataclass
class Attraction:
    name: str
    cost: float
    desc: str
    optional: bool = False


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
