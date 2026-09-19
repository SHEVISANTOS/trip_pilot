import csv
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from integrations.base import BaseClient
from integrations.countries import NAMES_BY_ISO2, resolve_country, resolve_nationality
from integrations.dataclasses import VisaInfo
from integrations.fixtures import sample_data_for

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "data" / "passport_index_visa.csv"

# The dataset (github.com/imorte/passport-index-data, MIT licensed, itself
# derived from github.com/ilyankou/passport-index-dataset) gives a visa
# *category* per (passport, destination) pair, not a price or exact
# processing time — those fields don't have a reliable free source, so they
# stay as flat, clearly-labelled estimates here rather than invented per-pair
# numbers.
REQUIREMENT_LABELS = {
    "-1": ("Same country", "Not applicable — this is your own passport's country", 0, "N/A"),
    "visa free": ("Visa-free entry", "Visa-free (freedom of movement or unlimited stay)", 0, "VISA-FREE"),
    "visa on arrival": ("Visa on arrival", "Confirm permitted stay on arrival", 50, "VISA ON ARRIVAL"),
    "eta": ("Electronic Travel Authorization (ETA)", "Confirm permitted stay", 20, "ETA"),
    "e-visa": ("e-Visa", "Confirm permitted stay", 40, "E-VISA"),
    "visa required": ("Visa required", "Confirm permitted stay with a full visa application", 60, "VISA REQUIRED"),
    "no admission": ("Entry not permitted", "Not applicable", 0, "NO ADMISSION"),
}


@lru_cache(maxsize=1)
def _load_dataset(path: str) -> dict[tuple[str, str], str]:
    dataset = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dataset[(row["Passport"], row["Destination"])] = row["Requirement"]
    return dataset


def _requirement_to_visa_info(requirement: str, nationality: str, destination_name: str) -> VisaInfo:
    if requirement.lstrip("-").isdigit() and requirement != "-1":
        days = int(requirement)
        type_, stay, cost, tag = ("Visa-free entry", f"Up to {days} days visa-free", 0, "VISA-FREE")
    else:
        type_, stay, cost, tag = REQUIREMENT_LABELS.get(
            requirement, ("Destination entry requirements", "Verify before booking", 60, "VERIFY")
        )
    method = (
        f"Open-data lookup ({nationality} passport → {destination_name}): {type_.lower()}. "
        "This dataset covers visa category only, not cost or processing time — the amount above "
        "is a generic estimate. Confirm exact cost, validity and processing time with the "
        "destination's official immigration authority before booking."
    )
    return VisaInfo(type=type_, method=method, stay=stay, cost=cost, tag=tag)


class PassportIndexVisaClient(BaseClient):
    """Visa guidance backed by the open, MIT-licensed Passport Index dataset
    (vendored at integrations/data/passport_index_visa.csv — refresh with
    `python manage.py refresh_visa_data`) instead of a paid provider. No API
    key required; falls back to the illustrative fixture when either the
    nationality or destination can't be resolved to a country, or the pair
    isn't in the dataset.
    """

    def get_visa_info(self, nationality: str, destination: str) -> VisaInfo:
        def fetch():
            passport_code = resolve_nationality(nationality)
            destination_code = resolve_country(destination)
            if not passport_code or not destination_code:
                raise ValueError(
                    f"could not resolve nationality={nationality!r} destination={destination!r} to country codes"
                )
            dataset_path = getattr(settings, "VISA_DATASET_PATH", DEFAULT_DATASET_PATH)
            dataset = _load_dataset(str(dataset_path))
            requirement = dataset.get((passport_code, destination_code))
            if requirement is None:
                raise KeyError(f"no dataset entry for ({passport_code}, {destination_code})")
            destination_name = NAMES_BY_ISO2.get(destination_code, destination)
            return _requirement_to_visa_info(requirement, nationality, destination_name)

        fallback = sample_data_for(destination).visa
        result = self.call("visa", fetch, None)
        return result or fallback
