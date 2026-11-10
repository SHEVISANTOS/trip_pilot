"""robots.txt and sitemap.xml — project-level concerns, not any one app's.

The sitemap deliberately does NOT rely on django.contrib.sites' Site model
for its domain (Sitemap.get_domain()'s default behaviour) — that row's
factory default is "example.com", which already caused a real bug once in
this project (password-reset emails linking to example.com before that was
fixed). get_domain() below returns settings.SITE_DOMAIN directly instead,
so the sitemap is never silently wrong regardless of whether that DB row
is ever updated.
"""

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    protocol = "https"
    changefreq = "monthly"

    def get_domain(self, site=None):
        return settings.SITE_DOMAIN

    def items(self):
        # Only the evergreen, publicly indexable pages — not login/signup
        # (no unique content, see their noindex meta tag), not the
        # dashboard or a specific trip's results (private/per-user), not
        # the admin.
        return [
            ("trips:planner", 1.0),
            ("legal:privacy", 0.3),
            ("legal:terms", 0.3),
        ]

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]


def robots_txt(request):
    """Allows every crawler, including the named AI ones that respect
    robots.txt (GPTBot, Google-Extended, PerplexityBot, ClaudeBot,
    anthropic-ai, CCBot) — explicit, not just the implicit "User-agent: *"
    allow, so TripPilot AI is findable and citable by AI answer engines,
    not only traditional search. Disallows the admin and the
    private/per-user pages that have nothing for a crawler to usefully
    index anyway (they 302 to login for an anonymous request).
    """
    private_paths = ["/" + settings.ADMIN_URL, "/accounts/dashboard/", "/pdf/"]
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in private_paths]
    lines.append("")
    for bot in ("GPTBot", "ChatGPT-User", "Google-Extended", "PerplexityBot", "ClaudeBot", "anthropic-ai", "CCBot"):
        lines.append(f"User-agent: {bot}")
        lines += [f"Disallow: {path}" for path in private_paths]
        lines.append("")
    lines.append(f"Sitemap: {settings.SITE_URL}/sitemap.xml")
    return HttpResponse("\n".join(lines), content_type="text/plain")
