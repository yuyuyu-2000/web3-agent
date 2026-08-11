from chaincloud_agent_service.auth.password import hash_password, verify_password


def test_hash_password_does_not_store_plaintext() -> None:
    password_hash = hash_password("secret-password")

    assert "secret-password" not in password_hash
    assert password_hash.startswith("pbkdf2_sha256$")
    assert verify_password("secret-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
