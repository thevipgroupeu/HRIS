from django.http import HttpResponse


class AnalyticsEmployeeBoundaryMiddleware:
    """Fail closed before Horilla's company middleware logs out staff-less users.

    Horilla's native company middleware redirects accounts without an Employee
    link to login.  Analytics permissions are staff permissions, so for this
    route an already-authenticated account without that link must receive an
    authorization denial instead of being treated as anonymous.
    """

    route_prefix = "/peopleops/analytics/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            request.path.startswith(self.route_prefix)
            and user is not None
            and user.is_authenticated
            and getattr(user, "employee_get", None) is None
        ):
            return HttpResponse("Forbidden", status=403, content_type="text/plain")
        return self.get_response(request)
