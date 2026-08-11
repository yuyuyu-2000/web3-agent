from chaincloud_agent_service.config import load_settings


MEMORY_ENV_KEYS = [
    "MEMORY_STORE_BACKEND",
    "MEMORY_DATABASE_URL",
    "MEMORY_POSTGRES_TABLE",
    "MEMORY_POSTGRES_AUTO_CREATE",
]


def test_load_settings_defaults_memory_backend(monkeypatch):
    for key in MEMORY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.memory_store_backend == "memory"
    assert settings.memory_database_url is None
    assert settings.memory_postgres_table == "agent_memories"
    assert settings.memory_postgres_auto_create is False


def test_load_settings_reads_postgres_memory_backend(monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "postgres")
    monkeypatch.setenv(
        "MEMORY_DATABASE_URL",
        "postgresql://chaincloud:chaincloud@localhost:5432/chaincloud_memory_dev",
    )
    monkeypatch.setenv("MEMORY_POSTGRES_TABLE", "agent_memories_test")
    monkeypatch.setenv("MEMORY_POSTGRES_AUTO_CREATE", "1")

    settings = load_settings()

    assert settings.memory_store_backend == "postgres"
    assert (
        settings.memory_database_url
        == "postgresql://chaincloud:chaincloud@localhost:5432/chaincloud_memory_dev"
    )
    assert settings.memory_postgres_table == "agent_memories_test"
    assert settings.memory_postgres_auto_create is True
