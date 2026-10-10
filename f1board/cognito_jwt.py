import os

_keys = None


def verify_id_token(token):
    import jwt
    from jwt import PyJWKClient

    issuer = os.environ["COGNITO_ISSUER"].rstrip("/")
    audience = os.environ["COGNITO_CLIENT_ID"]
    global _keys
    if _keys is None:
        _keys = PyJWKClient(issuer + "/.well-known/jwks.json")
    key = _keys.get_signing_key_from_jwt(token)
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=audience, issuer=issuer)
    if claims.get("token_use") != "id" or not claims.get("sub"):
        raise jwt.InvalidTokenError("id token required")
    return claims
