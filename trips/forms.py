from datetime import date

from django import forms

from trips.models import TripRequest


class TripRequestForm(forms.ModelForm):
    class Meta:
        model = TripRequest
        fields = [
            "departure",
            "destination",
            "nationality",
            "purpose",
            "start_date",
            "end_date",
            "adults",
            "children",
            "travel_style",
            "hotel_preference",
            "interests",
            "currency",
            "budget",
        ]
        widgets = {
            "departure": forms.TextInput(attrs={"placeholder": "e.g. Dar es Salaam"}),
            "destination": forms.TextInput(attrs={"placeholder": "e.g. Istanbul, Türkiye"}),
            "nationality": forms.TextInput(attrs={"placeholder": "e.g. Tanzanian"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "adults": forms.NumberInput(attrs={"min": 1, "placeholder": "e.g. 2"}),
            "children": forms.NumberInput(attrs={"min": 0, "placeholder": "e.g. 1"}),
            "budget": forms.NumberInput(attrs={"min": 100, "placeholder": "e.g. 5000"}),
            "interests": forms.TextInput(attrs={"placeholder": "History, food, shopping, nature"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # HTML5 min= is only a UI hint (browsers still let a past date be
        # typed/pasted in), so the real enforcement is in clean() below —
        # this just stops the date picker from offering past days at all.
        today = date.today().isoformat()
        self.fields["start_date"].widget.attrs["min"] = today
        self.fields["end_date"].widget.attrs["min"] = today

    def clean(self):
        cleaned = super().clean()
        start_date, end_date = cleaned.get("start_date"), cleaned.get("end_date")
        today = date.today()
        errors = []
        if start_date and start_date < today:
            errors.append("Departure date can't be in the past.")
        if end_date and end_date < today:
            errors.append("Return date can't be in the past.")
        if start_date and end_date and end_date <= start_date:
            errors.append("Return date must be after the departure date.")
        if errors:
            raise forms.ValidationError(errors)
        return cleaned
