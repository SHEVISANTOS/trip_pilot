from dataclasses import dataclass, field

from integrations.activities import OpenTripMapClient
from integrations.amadeus import AmadeusClient
from integrations.exchange import ExchangeRateClient
from integrations.maps import MapsClient
from integrations.visa import SherpaVisaClient


@dataclass
class IntegrationClients:
    """Bundle passed into pricing.budget.build_plan() so the pricing package
    stays framework-agnostic and only depends on this bundle's interface,
    not on Django or any specific provider.
    """

    amadeus: AmadeusClient = field(default_factory=AmadeusClient)
    activities: OpenTripMapClient = field(default_factory=OpenTripMapClient)
    visa: SherpaVisaClient = field(default_factory=SherpaVisaClient)
    exchange: ExchangeRateClient = field(default_factory=ExchangeRateClient)
    maps: MapsClient = field(default_factory=MapsClient)
