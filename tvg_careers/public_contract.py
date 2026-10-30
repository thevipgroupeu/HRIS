"""Versioned constants shared by the private API and public HTTP client."""

API_VERSION = "v1"
PUBLIC_FILTER_KEYS = frozenset(
    {
        "venture",
        "department",
        "category",
        "location",
        "work_mode",
        "erasmus",
        "q",
        "status",
    }
)
