"""Federation HQ GitHub App Review Gate v0.2.

A mechanical, SHA-bound attestor that converts an already accepted canonical
Federation HQ review artifact into a GitHub Check Run named
``federation-hq/review`` on the exact reviewed head SHA, using a private
GitHub App with strictly limited permissions.

No runtime Python dependencies: the gate uses the repository's conventions of
``curl`` subprocess for HTTP and ``openssl`` for JWT RS256 signing.
"""

__version__ = "0.2.0"

CHECK_RUN_NAME = "federation-hq/review"

# Runtime Gate App permissions — exactly these are requested and accepted.
REQUIRED_PERMISSIONS = {
    "metadata": "read",
    "contents": "read",
    "pull_requests": "read",
    "checks": "write",
}
OPTIONAL_PERMISSIONS = {
    "issues": "write",  # only when an audit comment is strictly required
}
FORBIDDEN_PERMISSIONS = {
    "administration": "write",
    "contents": "write",
    "actions": "write",
    "workflows": "write",
    "secrets": "read",
    "members": "write",
}

ACCOUNT_LOGIN = "kimeisele"
