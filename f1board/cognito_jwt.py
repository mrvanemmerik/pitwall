import os
import time

MAX_TOKEN_CHARS = 4096
REFRESH_EVERY = 300.0

_keys = None
_refreshed = 0.0


def _known_key(kid):
    """Is this key id in the cached signing keys? A fake kid may trigger a refetch only once per window."""
    global _refreshed
    if any(key.key_id == kid for key in _keys.get_jwk_set().keys):
        return True
    if time.monotonic() - _refreshed < REFRESH_EVERY:
        return False
    _refreshed = time.monotonic()
    return any(key.key_id == kid for key in _keys.get_jwk_set(refresh=True).keys)


def verify_id_token(token):
    import jwt
    from jwt import PyJWKClient

    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise jwt.InvalidTokenError("malformed token")
    issuer = os.environ["COGNITO_ISSUER"].rstrip("/")
    audience = os.environ["COGNITO_CLIENT_ID"]
    global _keys
    if _keys is None:
        _keys = PyJWKClient(issuer + "/.well-known/jwks.json", cache_jwk_set=True, lifespan=3600)
    kid = jwt.get_unverified_header(token).get("kid")
    if not kid or not _known_key(kid):
        raise jwt.InvalidTokenError("unknown signing key")
    key = _keys.get_signing_key(kid)
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=audience, issuer=issuer)
    if claims.get("token_use") != "id" or not claims.get("sub"):
        raise jwt.InvalidTokenError("id token required")
    return claims
