from unittest import mock

import pytest
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions

from .. import mocking


@pytest.fixture()
def browser():
    options = webdriver.FirefoxOptions()
    options.headless = True
    driver = webdriver.Firefox(options=options)
    driver = webdriver.Firefox()
    yield driver
    driver.close()
    driver.quit()


@pytest.fixture
def no_patience(app):
    """Set slow-spawning timeouts to zero"""
    with mock.patch.dict(
        app.tornado_settings, {'slow_spawn_timeout': 500, 'slow_stop_timeout': 0.1}
    ):
        yield


@pytest.fixture
def slow_spawn(app):
    """Fixture enabling SlowSpawner"""
    with mock.patch.dict(app.tornado_settings, {'spawner_class': mocking.SlowSpawner}):
        yield
