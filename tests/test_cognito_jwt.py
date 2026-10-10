import base64
import json
from types import SimpleNamespace

import jwt
import pytest

from f1board import cognito_jwt


def token(kid="good-key", parts=3):
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    pieces = [enc({"alg": "RS256", "kid": kid}), enc({"sub": "x"}), "sig"]
    return ".".join(pieces[:parts] if parts <= 3 else pieces + ["extra"] * (parts - 3))


class FakeKeys:
    def __init__(self, kids=("good-key",)):
        self.kids = list(kids)
        self.fetches = 0

    def get_jwk_set(self, refresh=False):
        if refresh:
            self.fetches += 1
        return SimpleNamespace(keys=[SimpleNamespace(key_id=kid) for kid in self.kids])


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("COGNITO_ISSUER", "https://issuer.example")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client")
    monkeypatch.setattr(cognito_jwt, "_refreshed", 0.0)


def test_oversized_and_malformed_tokens_are_rejected_before_any_key_work(monkeypatch):
    monkeypatch.setattr(cognito_jwt, "_keys", None)
    for bad in ("x" * 5000, "a.b", "a.b.c.d", "", None):
        with pytest.raises(jwt.InvalidTokenError):
            cognito_jwt.verify_id_token(bad)
    assert cognito_jwt._keys is None  # never even built the key client


def test_a_fake_key_id_triggers_at_most_one_key_refetch_per_window(monkeypatch):
    fake = FakeKeys()
    monkeypatch.setattr(cognito_jwt, "_keys", fake)
    clock = {"now": 1000.0}
    monkeypatch.setattr(cognito_jwt.time, "monotonic", lambda: clock["now"])
    for _ in range(5):
        with pytest.raises(jwt.InvalidTokenError):
            cognito_jwt.verify_id_token(token(kid="made-up"))
    assert fake.fetches == 1
    clock["now"] += 301
    with pytest.raises(jwt.InvalidTokenError):
        cognito_jwt.verify_id_token(token(kid="made-up"))
    assert fake.fetches == 2


def test_a_token_without_a_key_id_is_rejected():
    with pytest.raises(jwt.InvalidTokenError):
        cognito_jwt._keys = FakeKeys()
        enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
        cognito_jwt.verify_id_token(".".join([enc({"alg": "RS256"}), enc({"sub": "x"}), "sig"]))


def test_a_known_key_id_goes_on_to_signature_checking(monkeypatch):
    class Keys(FakeKeys):
        def get_signing_key(self, kid):
            return SimpleNamespace(key="public-key")

    monkeypatch.setattr(cognito_jwt, "_keys", Keys())
    seen = {}

    def fake_decode(tok, key, algorithms, audience, issuer):
        seen.update(key=key, algorithms=algorithms, audience=audience, issuer=issuer)
        return {"sub": "abc", "token_use": "id"}

    monkeypatch.setattr(jwt, "decode", fake_decode)
    assert cognito_jwt.verify_id_token(token())["sub"] == "abc"
    assert seen == {"key": "public-key", "algorithms": ["RS256"], "audience": "client", "issuer": "https://issuer.example"}
