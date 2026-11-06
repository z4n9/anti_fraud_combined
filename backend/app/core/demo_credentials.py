"""Public credentials for the explicitly enumerated local demonstration accounts."""

DEMO_PASSWORDS = {
    "TEST0001": "alikhan",
    "TEST0002": "aidana",
    "TEST0003": "marat",
    "TEST0004": "gulmira",
    "TEST0005": "daniyar",
    "TEST0006": "amina",
    "TEST0007": "saule",
    "TEST0099": "analyst",
}


def demo_password(test_iin: str) -> str:
    """Return a demo password; unknown accounts have no implicit default."""
    return DEMO_PASSWORDS[test_iin]
