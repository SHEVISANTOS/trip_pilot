import json
import re

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


class RobotsTxtTests(TestCase):
    def test_returns_200_as_plain_text(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")

    def test_allows_named_ai_crawlers_and_disallows_the_admin(self):
        response = self.client.get("/robots.txt")
        body = response.content.decode()
        for bot in ("GPTBot", "Google-Extended", "PerplexityBot", "ClaudeBot", "anthropic-ai", "CCBot"):
            self.assertIn(f"User-agent: {bot}", body)
        from django.conf import settings

        self.assertIn(f"Disallow: /{settings.ADMIN_URL}", body)
        self.assertIn("Disallow: /accounts/dashboard/", body)
        self.assertIn("Sitemap: https://tripilotai.com/sitemap.xml", body)


class SitemapTests(TestCase):
    def test_returns_200_and_lists_the_public_pages_on_the_real_domain(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Not request.get_host() — always the real domain regardless of
        # which host actually served this request (see config/seo.py).
        self.assertIn("https://tripilotai.com/</loc>", body)
        self.assertIn(f"https://tripilotai.com{reverse('legal:privacy')}</loc>", body)
        self.assertIn(f"https://tripilotai.com{reverse('legal:terms')}</loc>", body)
        # Private/per-user pages have no business in a public sitemap.
        self.assertNotIn("dashboard", body)


class StructuredDataAndMetaTests(TestCase):
    def test_homepage_has_meta_description_and_valid_json_ld(self):
        response = self.client.get(reverse("trips:planner"))
        body = response.content.decode()
        self.assertIn('<meta name="description"', body)
        self.assertIn('<link rel="canonical" href="https://tripilotai.com/">', body)

        blocks = re.findall(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', body, re.DOTALL)
        types = {json.loads(b)["@type"] for b in blocks}
        self.assertEqual(types, {"Organization", "WebApplication", "FAQPage"})

    def test_login_and_signup_are_noindex(self):
        for url_name in ("accounts:login", "accounts:signup"):
            response = self.client.get(reverse(url_name))
            self.assertIn('<meta name="robots" content="noindex, follow">', response.content.decode())

    def test_dashboard_is_noindex(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="seocheck", password="pw12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertIn('<meta name="robots" content="noindex, nofollow">', response.content.decode())
