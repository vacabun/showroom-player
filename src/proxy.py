import os
from urllib.request import getproxies

from PySide6.QtNetwork import QNetworkProxyFactory


_PROXY_ENV_MAP = {
    'http': ('http_proxy', 'HTTP_PROXY'),
    'https': ('https_proxy', 'HTTPS_PROXY'),
    'all': ('all_proxy', 'ALL_PROXY'),
    'no': ('no_proxy', 'NO_PROXY'),
}


def configure_qt_system_proxy():
    QNetworkProxyFactory.setUseSystemConfiguration(True)


def configure_requests_system_proxy(session):
    # Backfill requests proxy env vars from macOS/Windows system settings
    # without overriding explicit user configuration.
    for key, value in getproxies().items():
        if not value:
            continue
        env_names = _PROXY_ENV_MAP.get(key.lower())
        if not env_names:
            continue
        if any(os.environ.get(name) for name in env_names):
            continue
        os.environ[env_names[0]] = value
    session.trust_env = True
