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
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "adults": forms.NumberInput(attrs={"min": 1}),
            "children": forms.NumberInput(attrs={"min": 0}),
            "budget": forms.NumberInput(attrs={"min": 100}),
            "interests": forms.TextInput(attrs={"placeholder": "History, food, shopping, nature"}),
        }

    def clean(self):
        cleaned = super().clean()
        start_date, end_date = cleaned.get("start_date"), cleaned.get("end_date")
        if start_date and end_date and end_date <= start_date:
            raise forms.ValidationError("Return date must be after the departure date.")
        return cleaned
