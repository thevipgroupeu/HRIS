from unittest.mock import Mock

import requests
from django.test import SimpleTestCase, override_settings

from tvg_careers.public_client import (
    CareerHubAPIError,
    CareerHubJobNotFound,
    PeopleOpsJobClient,
)


@override_settings(
    CAREERHUB_PEOPLEOPS_API_URL="http://peopleops.test/internal/careerhub/v1",
    CAREERHUB_INTERNAL_API_TOKEN="synthetic-client-token",
    CAREERHUB_API_TIMEOUT=(1, 2),
)
class PeopleOpsJobClientTests(SimpleTestCase):
    def test_list_forwards_only_allow_listed_filters_and_authenticates(self):
        response = Mock(status_code=200)
        response.json.return_value = {"results": [], "facets": {}, "count": 0}
        session = Mock()
        session.get.return_value = response
        client = PeopleOpsJobClient(session=session)

        client.list_jobs({"venture": "fluxops", "private": "do-not-forward"})

        _url, kwargs = session.get.call_args
        self.assertEqual(kwargs["params"], {"venture": "fluxops"})
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Bearer synthetic-client-token"
        )
        self.assertEqual(kwargs["timeout"], (1, 2))

    def test_detail_maps_404_without_leaking_response_body(self):
        response = Mock(status_code=404)
        session = Mock()
        session.get.return_value = response

        with self.assertRaises(CareerHubJobNotFound):
            PeopleOpsJobClient(session=session).get_job("fluxops", "missing")

    def test_network_failure_is_a_controlled_service_error(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("synthetic outage")

        with self.assertRaises(CareerHubAPIError):
            PeopleOpsJobClient(session=session).list_jobs()
