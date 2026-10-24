from container_services import start_services


def pytest_configure(config):
    start_services(config)
