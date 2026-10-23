"""Security headers: Content-Security-Policy and Permissions-Policy.

Referrer-Policy is NOT handled here — django.middleware.security.
SecurityMiddleware already emits it from SECURE_REFERRER_POLICY.

The policy is built once from settings.CONTENT_SECURITY_POLICY at process
start, not per-request. The admin gets two extra allowances the public site
doesn't need, both verified against what's actually shipped, not assumed:
- style-src 'unsafe-inline' — django-unfold's own templates use inline
  style="..." attributes (the public site's templates do too, for the same
  reason, so this is actually already on the base policy — see settings.py).
- script-src 'unsafe-eval' — django-unfold bundles the standard Alpine.js
  build (not the CSP-safe @alpinejs/csp variant), confirmed by inspecting
  unfold/static/unfold/js/alpine/alpine.js: it evaluates x-data/x-on
  expressions via `new Function(...)`, which a strict script-src blocks.
  Verified live: the admin's sidebar/theme-toggle interactivity (both
  Alpine-driven) silently breaks without this, with CSP violations in the
  browser console, even though every page still returns 200.
"""

from django.conf import settings


def build_policy(directives):
    return "; ".join(f"{name} {' '.join(values)}".strip() for name, values in directives.items())


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        directives = settings.CONTENT_SECURITY_POLICY
        self.site_policy = build_policy(directives)

        admin_directives = dict(directives)
        admin_directives["script-src"] = [*directives.get("script-src", []), "'unsafe-eval'"]
        self.admin_policy = build_policy(admin_directives)

        self.header = (
            "Content-Security-Policy-Report-Only"
            if settings.CONTENT_SECURITY_POLICY_REPORT_ONLY
            else "Content-Security-Policy"
        )
        self.admin_prefix = f"/{settings.ADMIN_URL}"

    def __call__(self, request):
        response = self.get_response(request)
        policy = self.admin_policy if request.path.startswith(self.admin_prefix) else self.site_policy
        response.headers.setdefault(self.header, policy)
        response.headers.setdefault("Permissions-Policy", settings.PERMISSIONS_POLICY)
        return response
