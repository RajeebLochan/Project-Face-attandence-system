"""Security headers that Django does not set by itself."""

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
# The Django admin uses some inline styles.
ADMIN_CONTENT_SECURITY_POLICY = CONTENT_SECURITY_POLICY.replace(
    "style-src 'self'", "style-src 'self' 'unsafe-inline'"
)
PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=(), payment=(), usb=()"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = (
            ADMIN_CONTENT_SECURITY_POLICY
            if request.path.startswith("/admin/")
            else CONTENT_SECURITY_POLICY
        )
        response.headers.setdefault("Content-Security-Policy", policy)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response
