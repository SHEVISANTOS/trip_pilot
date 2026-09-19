import csv
import io

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DATASET_URL = "https://raw.githubusercontent.com/imorte/passport-index-data/main/passport-index-tidy-iso2.csv"


class Command(BaseCommand):
    help = (
        "Re-download the open Passport Index visa-requirements dataset "
        "(imorte/passport-index-data) into integrations/data/passport_index_visa.csv. "
        "Run periodically to pick up upstream updates — no API key needed."
    )

    def handle(self, *args, **options):
        self.stdout.write(f"Fetching {DATASET_URL} ...")
        try:
            response = requests.get(DATASET_URL, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Could not download the dataset: {exc}") from exc

        reader = csv.DictReader(io.StringIO(response.text))
        if reader.fieldnames != ["Passport", "Destination", "Requirement"]:
            raise CommandError(
                f"Unexpected CSV columns {reader.fieldnames!r} — upstream format may have changed, "
                "check integrations/visa.py before overwriting the vendored file."
            )
        row_count = sum(1 for _ in reader)

        dataset_path = getattr(settings, "VISA_DATASET_PATH", None)
        if not dataset_path:
            from integrations.visa import DEFAULT_DATASET_PATH

            dataset_path = DEFAULT_DATASET_PATH
        with open(dataset_path, "w", encoding="utf-8", newline="") as f:
            f.write(response.text)

        self.stdout.write(self.style.SUCCESS(f"Wrote {row_count} rows to {dataset_path}"))
