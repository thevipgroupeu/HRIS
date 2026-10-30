import secrets


class PublicSecurityHeadersMiddleware:
    """Headers specific to the deliberately script-free public site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; "
            "font-src 'self'; style-src 'self'; "
            f"script-src 'self' 'nonce-{request.csp_nonce}'",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        candidate_route = request.path.startswith("/candidate/")
        if candidate_route:
            response.headers["Cache-Control"] = "private, no-store"
        response.headers.setdefault(
            "X-Robots-Tag",
            (
                "noindex, nofollow"
                if candidate_route or response.status_code >= 400
                else "index, follow"
            ),
        )
        return response
