from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from mock_hynix import CALLS, create_mock  # noqa: E402
from util import ThreadServer  # noqa: E402

CHROME = os.environ.get("NIXIE_CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")


@pytest.fixture(scope="session")
def mock_hynix():
    with ThreadServer(create_mock()) as s:
        yield s


@pytest.fixture()
def calls():
    CALLS.clear()
    return CALLS


@pytest.fixture(scope="session")
def users_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("users")


@pytest.fixture(scope="session")
def browser_pool(users_root):
    if not CHROME:
        pytest.skip("no Chrome/Chromium available")
    from nixie.browser_pool import BrowserPool, create_pool_app

    pool = BrowserPool(users_root, size=1, allow_domains=["127.0.0.1"], executable_path=CHROME)
    with ThreadServer(create_pool_app(pool, token="pool-secret")) as s:
        yield s, pool
