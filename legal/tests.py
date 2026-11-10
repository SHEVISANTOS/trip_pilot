from django.test import TestCase
from django.urls import reverse


class LegalPagesViewTests(TestCase):
    def test_privacy_page_returns_200_for_anonymous_user(self):
        response = self.client.get(reverse("legal:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Policy")

    def test_terms_page_returns_200_for_anonymous_user(self):
        response = self.client.get(reverse("legal:terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of Service")


class FooterLinksTests(TestCase):
    def test_home_page_footer_links_to_privacy_and_terms(self):
        response = self.client.get(reverse("trips:planner"))
        self.assertContains(response, reverse("legal:privacy"))
        self.assertContains(response, reverse("legal:terms"))
