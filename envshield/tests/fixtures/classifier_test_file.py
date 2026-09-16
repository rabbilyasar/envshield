# Test fixture for classifier integration tests
# This file contains patterns that should and should not be suppressed
# ruff: noqa: F821

# SHOULD BE SUPPRESSED: Function keyword argument (site FP)
response.set_cookie(key=SESSION_COOKIE_NAME, secure=True)


# SHOULD BE SUPPRESSED: Type annotation
def authenticate(api_key: str) -> bool:
    pass


# SHOULD BE SUPPRESSED: Identifier reference
token = EXISTING_TOKEN

# SHOULD REMAIN FINDING: String literal
api_key = "sk_live_abc123_this_looks_like_a_real_key_value"

# SHOULD REMAIN FINDING: String literal in function call
response.set_cookie(key="abc123def456ghi789", secure=True)

# SHOULD REMAIN FINDING: Multi-line function call with string literal
response.set_cookie(key="xyz789mno012pqr345", secure=True)
