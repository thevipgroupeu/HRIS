"""HTTP client used by the public CareerHub process to read safe job projections."""

from __future__ import annotations

from urllib.parse import quote

import requests
from django.conf import settings

from tvg_careers.public_contract import PUBLIC_FILTER_KEYS


class CareerHubAPIError(RuntimeError):
    pass


class CareerHubJobNotFound(CareerHubAPIError):
    pass


class PeopleOpsJobClient:
    def __init__(self, *, session=None):
        self.session = session or requests.Session()
        self.base_url = getattr(settings, "CAREERHUB_PEOPLEOPS_API_URL", "").rstrip("/")
        self.token = getattr(settings, "CAREERHUB_INTERNAL_API_TOKEN", "")
        self.timeout = getattr(settings, "CAREERHUB_API_TIMEOUT", (2, 5))

    def _request(self, path, *, params=None, not_found_is_job=False):
        if not self.base_url or not self.token:
            raise CareerHubAPIError("CareerHub job service is not configured.")
        try:
            response = self.session.get(
                f"{self.base_url}/{path.lstrip('/')}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "THEVIPGROUP-CareerHub/1.0",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CareerHubAPIError("PeopleOps job service is unavailable.") from exc

        if response.status_code == 404 and not_found_is_job:
            raise CareerHubJobNotFound
        if response.status_code != 200:
            raise CareerHubAPIError(
                f"PeopleOps job service returned HTTP {response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CareerHubAPIError(
                "PeopleOps job service returned invalid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise CareerHubAPIError(
                "PeopleOps job service returned an invalid payload."
            )
        return payload

    def list_jobs(self, filters=None):
        filters = filters or {}
        params = {
            key: str(filters[key]).strip()[:160]
            for key in PUBLIC_FILTER_KEYS
            if filters.get(key)
        }
        payload = self._request("jobs/", params=params)
        if not isinstance(payload.get("results"), list) or not isinstance(
            payload.get("facets"), dict
        ):
            raise CareerHubAPIError(
                "PeopleOps job list did not match the public contract."
            )
        return payload

    def get_job(self, venture_slug, opening_slug):
        venture_slug = quote(str(venture_slug), safe="")
        opening_slug = quote(str(opening_slug), safe="")
        payload = self._request(
            f"jobs/{venture_slug}/{opening_slug}/",
            not_found_is_job=True,
        )
        required = {
            "slug",
            "title",
            "venture",
            "application_status",
            "accepting_applications",
        }
        if not required.issubset(payload):
            raise CareerHubAPIError(
                "PeopleOps job detail did not match the public contract."
            )
        return payload


def get_public_job_client():
    return PeopleOpsJobClient()
