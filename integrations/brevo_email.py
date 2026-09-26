"""Sends email via Brevo's transactional email HTTP API instead of SMTP.

Brevo's SMTP relay requires allowlisting the connecting IP address — fine
for a fixed dev machine, but Vercel Functions have no static outbound IP
(that's an Enterprise-only add-on), so every send from production would hit
"525 Unauthorized IP address" no matter what's allowlisted. The HTTP API
just needs the account's API key over HTTPS, so it works identically
locally and on Vercel. Verified live: this account's only authenticated
sender is the account owner's own address (checked via GET
https://api.brevo.com/v3/senders) — Brevo rejects sends from any other
`sender.email`, so DEFAULT_FROM_EMAIL must stay that address until a real
domain is verified in the Brevo dashboard.
"""
import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

API_URL = "https://api.brevo.com/v3/smtp/email"


def _split_from(from_email: str) -> tuple[str, str]:
    """"Name <email>" -> ("Name", "email"); "email" -> ("", "email")."""
    if "<" in from_email and from_email.endswith(">"):
        name, _, rest = from_email.partition("<")
        return name.strip().strip('"'), rest[:-1].strip()
    return "", from_email.strip()


class BrevoAPIEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        return sum(1 for message in email_messages if self._send_one(message))

    def _send_one(self, message) -> bool:
        sender_name, sender_email = _split_from(message.from_email)
        payload = {
            "sender": {"email": sender_email, **({"name": sender_name} if sender_name else {})},
            "to": [{"email": addr} for addr in message.to],
            "subject": message.subject,
            "textContent": message.body,
        }
        if message.cc:
            payload["cc"] = [{"email": addr} for addr in message.cc]
        if message.bcc:
            payload["bcc"] = [{"email": addr} for addr in message.bcc]

        try:
            resp = requests.post(
                API_URL,
                json=payload,
                headers={
                    "accept": "application/json",
                    "api-key": settings.BREVO_API_KEY,
                    "content-type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException:
            if not self.fail_silently:
                raise
            return False
        return True
