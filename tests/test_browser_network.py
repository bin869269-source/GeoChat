"""Conservative browser evidence classification; never ignore generic aborts."""
from copy import deepcopy

import pytest

from tests.phase4a_acceptance import classify_network_failures


def events():
    url = "https://tile.openstreetmap.org/15/17203/10968.png"
    def event(method, **params):
        return {"method": method, "params": params, "sessionId": "a"}
    return [
        event("Page.frameNavigated", frame={"id": "map", "url":
              "http://127.0.0.1:1234/component/streamlit_folium.st_folium/index.html"}),
        event("Network.requestWillBeSent", requestId="original", frameId="map", type="Image",
              request={"url": url}, timestamp=1),
        event("Network.responseReceived", requestId="original", response={"status": 200, "mimeType": "image/png"}),
        event("Network.loadingFinished", requestId="original", timestamp=1.01),
        event("Network.requestWillBeSent", requestId="duplicate", frameId="map", type="Other",
              request={"url": url}, timestamp=1.03),
        event("Network.loadingFailed", requestId="duplicate", type="Other", errorText="net::ERR_ABORTED",
              canceled=True, timestamp=1.05),
    ]


def test_only_proven_delivered_duplicate_is_expected():
    evidence = events()
    before = deepcopy(evidence)
    expected, unexpected = classify_network_failures(evidence)
    assert not unexpected and len(expected) == 1
    assert expected[0]["delivered_request_ids"] == ["original"]
    assert expected[0]["failure"]["params"]["requestId"] == "duplicate"
    assert evidence == before


@pytest.mark.parametrize("kind", ["timeout", "not_canceled", "image", "wrong_host", "script", "geojson",
                                  "missing_finished", "wrong_frame", "wrong_session", "wrong_mime",
                                  "http_error", "slow", "stale", "not_map", "missing_request", "error_response"])
def test_other_network_failures_are_still_unexpected(kind):
    evidence = events()
    failure = evidence[-1]
    if kind == "timeout":
        failure["params"]["errorText"] = "net::ERR_TIMED_OUT"
    elif kind == "not_canceled":
        failure["params"]["canceled"] = False
    elif kind == "image":
        failure["params"]["type"] = "Image"
    elif kind in ("wrong_host", "script", "geojson"):
        value = {"wrong_host": "https://not-osm.example/15/17203/10968.png",
                 "script": "https://tile.openstreetmap.org/leaflet.js",
                 "geojson": "http://127.0.0.1:1234/data.geojson"}[kind]
        evidence[1]["params"]["request"]["url"] = value
        evidence[4]["params"]["request"]["url"] = value
    elif kind == "missing_finished":
        evidence.pop(3)
    elif kind == "wrong_frame":
        evidence[4]["params"]["frameId"] = "other"
    elif kind == "wrong_session":
        for item in evidence[:4]:
            item["sessionId"] = "other"
    elif kind == "wrong_mime":
        evidence[2]["params"]["response"]["mimeType"] = "text/html"
    elif kind == "http_error":
        evidence[2]["params"]["response"]["status"] = 500
    elif kind == "slow":
        failure["params"]["timestamp"] = 2
    elif kind == "stale":
        evidence[3]["params"]["timestamp"] = .5
    elif kind == "not_map":
        evidence[0]["params"]["frame"]["url"] = "http://127.0.0.1:1234/other"
    elif kind == "missing_request":
        evidence.pop(4)
    elif kind == "error_response":
        evidence.insert(-1, {"method": "Network.responseReceived", "sessionId": "a", "params": {
            "requestId": "duplicate", "response": {"status": 500, "mimeType": "text/html"}}})
    expected, unexpected = classify_network_failures(evidence)
    assert not expected and unexpected == [failure]
