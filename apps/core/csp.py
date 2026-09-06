"""A baseline Content-Security-Policy header — defense-in-depth, not a
response to any known XSS sink (the security review found none: no
|safe/mark_safe/innerHTML-with-untrusted-content anywhere in this app).
Every static asset (JS/CSS/fonts/vendor libraries) is already served from
this app's own origin (static/js/vendor, static/css/vendor — no external
CDN), so 'self' covers all of it; 'unsafe-inline' is needed for the small
number of inline <script>/<style> blocks already in base.html and the
movement-form templates (a nonce-based policy would be stricter but is a
larger template-wide change than this precautionary header calls for).
"""

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response
