from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView

from .api import session


class LoginDialog(QDialog):
    """内嵌浏览器登录，自动处理验证码，登录成功后同步 cookie 到 requests.Session。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Showroom Player Sign In')
        self.setMinimumSize(520, 660)
        self._cookies = {}  # name -> (value, domain, path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)

        self._web = QWebEngineView()
        layout.addWidget(self._web)

        hint = QLabel('在上方完成登录，成功后窗口自动关闭')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet('color: gray; font-size: 11px;')
        layout.addWidget(hint)

        store = self._web.page().profile().cookieStore()
        store.cookieAdded.connect(self._on_cookie_added)

        self._web.load(QUrl('https://www.showroom-live.com/account/login'))
        self._web.urlChanged.connect(self._on_url_changed)

    def _on_cookie_added(self, cookie):
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        value = bytes(cookie.value()).decode('utf-8', errors='replace')
        self._cookies[name] = (value, cookie.domain(), cookie.path())

    def _on_url_changed(self, url):
        s = url.toString()
        if ('showroom-live.com' in s
                and '/account/login' not in s
                and s != 'about:blank'):
            self._sync_cookies()
            self.accept()

    def _sync_cookies(self):
        for name, (value, domain, path) in self._cookies.items():
            session.cookies.set(name, value,
                                domain=domain.lstrip('.'),
                                path=path or '/')
