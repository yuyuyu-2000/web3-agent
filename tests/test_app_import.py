def test_main_app_imports_successfully():
    from chaincloud_agent_service.main import app

    assert app is not None


def test_memory_package_exports_store_factory():
    from chaincloud_agent_service.memory import create_memory_store

    assert callable(create_memory_store)
