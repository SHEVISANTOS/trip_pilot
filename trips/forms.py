from datetime import date

from django import forms

from trips.models import ACCOMMODATION_CHOICES, TripRequest


class TripRequestForm(forms.ModelForm):
    """Trip-level fields only — destination/dates/room type now live on
    LegForm (one per destination) instead, via LegFormSet below.
    """

    class Meta:
        model = TripRequest
        fields = [
            "departure",
            "nationality",
            "purpose",
            "adults",
            "children",
            "travel_style",
            "interests",
            "currency",
            "budget",
        ]
        widgets = {
            "departure": forms.TextInput(attrs={"placeholder": "e.g. Dar es Salaam"}),
            "nationality": forms.TextInput(attrs={"placeholder": "e.g. Tanzanian"}),
            "adults": forms.NumberInput(attrs={"min": 1, "placeholder": "e.g. 2"}),
            "children": forms.NumberInput(attrs={"min": 0, "placeholder": "e.g. 1"}),
            "budget": forms.NumberInput(attrs={"min": 100, "placeholder": "e.g. 5000"}),
            "interests": forms.TextInput(attrs={"placeholder": "History, food, shopping, nature"}),
        }


class LegForm(forms.Form):
    city = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"placeholder": "e.g. Istanbul, Türkiye"}))
    arrival_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    departure_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hotel_preference = forms.ChoiceField(choices=ACCOMMODATION_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Same UI-hint-only purpose as TripRequestForm's old min= attrs —
        # real enforcement is in BaseLegFormSet.clean() below.
        today = date.today().isoformat()
        self.fields["arrival_date"].widget.attrs["min"] = today
        self.fields["departure_date"].widget.attrs["min"] = today

    def clean(self):
        cleaned = super().clean()
        arrival, departure = cleaned.get("arrival_date"), cleaned.get("departure_date")
        if arrival and departure and departure <= arrival:
            raise forms.ValidationError("The departure date from this destination must be after you arrive.")
        return cleaned


class BaseLegFormSet(forms.BaseFormSet):
    """Cross-leg validation formset-level `clean()` can't express on a
    single LegForm: no past dates, and each destination's arrival can't be
    before the previous one's departure (the trip is a single continuous
    route, not independent unrelated bookings).
    """

    def clean(self):
        if any(self.errors):
            return
        legs = [form.cleaned_data for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if not legs:
            raise forms.ValidationError("Add at least one destination.")

        today = date.today()
        if legs[0]["arrival_date"] < today:
            raise forms.ValidationError("The first destination's arrival date can't be in the past.")
        for i in range(1, len(legs)):
            if legs[i]["arrival_date"] < legs[i - 1]["departure_date"]:
                raise forms.ValidationError(
                    f"Destination {i + 1}'s arrival date can't be before you leave destination {i}."
                )


LegFormSet = forms.formset_factory(LegForm, formset=BaseLegFormSet, extra=0, min_num=1, validate_min=True)
