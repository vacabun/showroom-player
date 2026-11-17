import base64
import json
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QPushButton,
    QLineEdit,
    QFormLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from shiboken6 import isValid
from PySide6.QtWebEngineWidgets import QWebEngineView

from .api import save_session_cookies, set_session_cookie


class LoginDialog(QDialog):
    """Embedded browser sign-in dialog that syncs cookies into requests.Session."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Showroom Player Sign In')
        self.setMinimumSize(520, 660)
        self._cookies = {}  # name -> (value, domain, path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)

        self._username = QLineEdit()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow('SHOWROOM ID', self._username)
        form.addRow('Password', self._password)
        self._captcha_image = QLabel()
        self._captcha_answer = QLineEdit()
        self._captcha_answer.setPlaceholderText('Enter the text in the image')
        form.addRow(self._captcha_image)
        form.addRow(self._captcha_answer)
        self._captcha_image.hide()
        self._captcha_answer.hide()
        self._captcha_answer.returnPressed.connect(self._submit_credentials)
        layout.addLayout(form)
        self._sign_in = QPushButton('Sign In')
        self._sign_in.setEnabled(False)
        self._sign_in.clicked.connect(self._submit_credentials)
        self._password.returnPressed.connect(self._submit_credentials)
        layout.addWidget(self._sign_in)
        web_login = QPushButton('Use Web Sign-In')
        web_login.clicked.connect(self._show_web_login)
        layout.addWidget(web_login)
        self._hint = QLabel('Loading SHOWROOM sign-in…')
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._web = QWebEngineView()
        self._web.hide()
        layout.addWidget(self._web, stretch=1)
        self._web.loadFinished.connect(self._on_login_page_loaded)
        self._web.urlChanged.connect(self._on_url_changed)

        self._captcha_timer = QTimer(self)
        self._captcha_timer.setInterval(500)
        self._captcha_timer.timeout.connect(self._check_captcha)
        self._captcha_timer.start()

        store = self._web.page().profile().cookieStore()
        store.cookieAdded.connect(self._on_cookie_added)

        self._web.load(QUrl('https://www.showroom-live.com/account/login'))

    def _on_login_page_loaded(self, ok):
        self._sign_in.setEnabled(ok and self._is_login_page())
        if not ok:
            self._hint.setText('Could not load SHOWROOM sign-in.')
            self._show_web_login()
        elif self._is_login_page():
            self._hint.setText('Enter your SHOWROOM account details.')

    def _is_login_page(self):
        url = self._web.url()
        return (
            url.scheme() == 'https'
            and url.host() == 'www.showroom-live.com'
            and url.port(443) == 443
            and url.path().rstrip('/') == '/account/login'
        )

    def _show_web_login(self):
        self._password.clear()
        self._captcha_image.hide()
        self._captcha_answer.hide()
        self._web.show()

    def _check_captcha(self):
        if not self.isVisible() or self._web.isVisible() or not self._is_login_page():
            return
        self._web.page().runJavaScript("""
            (() => {
                const fields = [...document.querySelectorAll('input')];
                const answer = fields.find(input => input.getClientRects().length &&
                    /captcha|words as they appear in the image/i.test(input.name + ' ' + input.placeholder));
                if (!answer || !answer.form) return '';
                const images = [...answer.form.querySelectorAll('img')];
                const image = images.find(img => img.getClientRects().length &&
                    /captcha/i.test(img.src + ' ' + img.alt + ' ' + img.id));
                if (!image || !image.naturalWidth) return '';
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                try {
                    canvas.getContext('2d').drawImage(image, 0, 0);
                    return canvas.toDataURL('image/png').split(',')[1];
                } catch {
                    return '';
                }
            })()
        """, self._update_captcha)

    def _update_captcha(self, image_data):
        if not self.isVisible() or self._web.isVisible():
            return
        image = QPixmap()
        if image_data:
            image.loadFromData(base64.b64decode(image_data))
        if not image.isNull():
            self._captcha_image.setPixmap(image)
            self._sign_in.setEnabled(True)
        elif not self._captcha_answer.isHidden():
            self._captcha_answer.clear()
        self._captcha_image.setVisible(not image.isNull())
        self._captcha_answer.setVisible(not image.isNull())

    def _submit_credentials(self):
        if not self._sign_in.isEnabled() or not self._is_login_page():
            return
        username = self._username.text().strip()
        password = self._password.text()
        if not username or not password:
            self._hint.setText('Enter your SHOWROOM ID and password.')
            return
        answer = self._captcha_answer.text().strip()
        if not self._captcha_answer.isHidden() and not answer:
            self._hint.setText('Enter the text shown in the verification image.')
            return
        credentials = json.dumps([username, password, answer])
        self._password.clear()
        self._sign_in.setEnabled(False)
        self._hint.setText('Submitting to SHOWROOM…')
        self._web.page().runJavaScript(r"""
            (([username, password, answer]) => {
                if (location.origin !== 'https://www.showroom-live.com' ||
                    location.pathname.replace(/\/$/, '') !== '/account/login') return;
                const passwords = [...document.querySelectorAll('input[type="password"]')]
                    .filter(input => input.getClientRects().length && !input.disabled);
                if (passwords.length !== 1) return;
                const secret = passwords[0];
                const form = secret.form;
                if (!form || form.method !== 'post' ||
                    new URL(form.action).origin !== location.origin) return;
                const captcha = [...form.querySelectorAll('input')].find(input =>
                    input.getClientRects().length &&
                    /captcha|words as they appear in the image/i.test(input.name + ' ' + input.placeholder));
                if (captcha && !answer) return true;
                const fields = [...form.querySelectorAll('input')]
                    .filter(input => input !== captcha && ['text', 'email', 'tel'].includes(input.type) &&
                        input.getClientRects().length && !input.disabled);
                if (fields.length !== 1) return;
                const setValue = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value').set;
                const values = [[fields[0], username], [secret, password]];
                if (captcha) values.push([captcha, answer]);
                for (const [field, value] of values) {
                    setValue.call(field, value);
                    field.dispatchEvent(new Event('input', { bubbles: true }));
                    field.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const submit = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
                if (!submit || submit.disabled) return false;
                setTimeout(() => submit.click(), 0);
                return true;
            })(""" + credentials + ');', self._on_credentials_submitted)

    def _on_credentials_submitted(self, submitted):
        if submitted or not self.isVisible():
            return
        self._hint.setText('Could not submit automatically. Continue in the SHOWROOM page.')
        self._show_web_login()

    def _on_cookie_added(self, cookie):
        name = bytes(cookie.name()).decode('utf-8', errors='replace')
        value = bytes(cookie.value()).decode('utf-8', errors='replace')
        self._cookies[name] = (value, cookie.domain(), cookie.path())

    def _on_url_changed(self, url):
        host = url.host().lower()
        if url.scheme() != 'https' or not (
            host == 'showroom-live.com' or host.endswith('.showroom-live.com')
        ):
            return

        trace_path = os.environ.get('SHOWROOM_LOGIN_TRACE')
        path = url.path()
        if trace_path:
            # Keep only predefined route names. URLs can carry secrets in any component.
            route = path if path in ('/account/login', '/', '/account') else '[other route]'
            try:
                with Path(trace_path).open('a', encoding='utf-8') as trace:
                    trace.write(f'navigation {route}\n')
            except OSError:
                pass

        if not path.startswith('/account/login'):
            self._sync_cookies()
            self.accept()

    def _sync_cookies(self):
        for name, (value, domain, path) in self._cookies.items():
            set_session_cookie(name, value, domain=domain, path=path or '/')
        save_session_cookies()


class UserInfoDialog(QDialog):
    refresh_requested = Signal()
    sign_out_requested = Signal()

    def __init__(self, user_info, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Profile')
        self.resize(520, 440)
        self._avatar_network = QNetworkAccessManager(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.hide()
        layout.addWidget(self._status)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._scroll, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._refresh = buttons.addButton('Refresh', QDialogButtonBox.ButtonRole.ActionRole)
        sign_out = buttons.addButton('Sign Out', QDialogButtonBox.ButtonRole.ActionRole)
        self._refresh.clicked.connect(self.refresh_requested)
        sign_out.clicked.connect(self.sign_out_requested)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.set_profile(user_info)
        self._apply_style()

    def set_profile(self, user_info):
        self._user_info = dict(user_info)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        if user_info:
            body.addWidget(self._build_header_card())
            body.addWidget(self._build_stat_cards())
            for section in self._build_detail_sections():
                body.addWidget(section)
        else:
            body.addWidget(QLabel('Profile unavailable. Select Refresh to try again.'))
        body.addStretch()
        self._scroll.setWidget(content)

    def set_refresh_state(self, loading=False, message=''):
        self._refresh.setEnabled(not loading)
        self._refresh.setText('Refreshing…' if loading else 'Refresh')
        self._status.setText(message)
        self._status.setVisible(bool(message))

    @Slot(dict, bool)
    def apply_refresh_result(self, data, ok):
        if ok:
            self.set_profile(data)
            self.set_refresh_state()
        else:
            self.set_refresh_state(message='Could not refresh the profile. Try again.')

    def _build_header_card(self):
        card = QFrame()
        card.setObjectName('userInfoHero')
        layout = QHBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(72, 72)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setText(self._avatar_fallback_text())
        fallback_font = QFont()
        fallback_font.setPointSize(18)
        fallback_font.setBold(True)
        avatar.setFont(fallback_font)
        self._load_avatar(avatar)
        avatar.setObjectName('userInfoAvatar')
        layout.addWidget(avatar)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        display_name = self._user_info.get('user_name') or self._user_info.get('account_id') or 'Account'
        name_label = QLabel(str(display_name))
        name_label.setTextFormat(Qt.TextFormat.PlainText)
        name_font = QFont()
        name_font.setPointSize(13)
        name_font.setBold(True)
        name_label.setFont(name_font)
        info_layout.addWidget(name_label)

        account_id = self._user_info.get('account_id') or 'Unknown'
        handle = QLabel(f'@{account_id}')
        handle.setTextFormat(Qt.TextFormat.PlainText)
        info_layout.addWidget(handle)
        copy_id = QPushButton('Copy ID')
        identifier = self._user_info.get('account_id') or self._user_info.get('user_id')
        copy_id.setEnabled(bool(identifier))
        copy_id.clicked.connect(lambda: self._copy_id(identifier, copy_id))

        user_id = self._user_info.get('user_id')
        room_key = self._user_info.get('own_room_url_key') or '-'
        summary_label = QLabel(f'User ID: {user_id or "-"}    Room Key: {room_key}')
        summary_label.setObjectName('userInfoSubtle')
        info_layout.addWidget(summary_label)

        room_id = self._user_info.get('own_room_id')
        room_text = f'Own Room ID: {room_id}' if room_id else 'No room linked'
        room_label = QLabel(room_text)
        room_label.setObjectName('userInfoSubtle')
        info_layout.addWidget(room_label)

        info_layout.addStretch()
        layout.addLayout(info_layout, stretch=1)
        layout.addWidget(copy_id, alignment=Qt.AlignmentFlag.AlignTop)
        return card

    def _build_stat_cards(self):
        group = QGroupBox('Highlights')
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(2)

        stats = [
            ('Fan Level', self._format_value(self._user_info.get('user_fan_level'))),
            ('Gold', self._format_value(self._user_info.get('user_gold'))),
            ('Expiring Gold', self._format_value(self._user_info.get('user_expiring_gold'))),
            ('Contribution', self._format_value(self._user_info.get('contribution_point'))),
        ]
        for index, (label, value) in enumerate(stats):
            row = index // 2
            col = (index % 2) * 2
            layout.addWidget(self._create_label_value_label(label, is_caption=True), row, col)
            layout.addWidget(self._create_label_value_label(value), row, col + 1)
        return group

    def _build_detail_sections(self):
        sections = []
        used_keys = set()

        definitions = [
            (
                'Profile',
                [
                    ('Display Name', 'user_name', self._format_value),
                    ('Account ID', 'account_id', self._format_value),
                    ('User ID', 'user_id', self._format_value),
                    ('Birthday', 'birthday', self._format_birthday),
                    ('Gender', 'gender', self._format_gender),
                    ('Signed In', 'is_login', self._format_bool),
                ],
            ),
            (
                'Room',
                [
                    ('Own Room ID', 'own_room_id', self._format_value),
                    ('Own Room Key', 'own_room_url_key', self._format_value),
                    ('Fan Level', 'user_fan_level', self._format_value),
                    ('Contribution', 'contribution_point', self._format_value),
                ],
            ),
            (
                'Balance',
                [
                    ('Gold', 'user_gold', self._format_value),
                    ('Expiring Gold', 'user_expiring_gold', self._format_value),
                ],
            ),
            (
                'Organizer',
                [
                    ('Organizer', 'is_organizer', self._format_bool),
                    ('Organizer ID', 'organizer_id', self._format_value),
                    ('Organizer Name', 'organizer_name', self._format_value),
                    ('Organizer Account ID', 'organizer_account_id', self._format_value),
                    ('Event Organizer ID', 'event_organizer_id', self._format_value),
                    ('Event Organizer Pending', 'is_evnet_org_pending', self._format_bool),
                    ('Has Payment Organizer', 'has_payment_organaizer', self._format_bool),
                ],
            ),
            (
                'Notices',
                [
                    ('Unread User Notices', 'has_unread_user_notice', self._format_value),
                    ('Unread Notices', 'has_user_unread_notice', self._format_value),
                    ('Event Notice', 'has_event_notice', self._format_bool),
                ],
            ),
            (
                'Verification',
                [
                    ('SMS Verified', 'sms_auth', self._format_bool),
                    ('Legal Representative Agreed', 'is_legal_representative_agreed', self._format_value),
                ],
            ),
        ]

        for title, fields in definitions:
            rows = []
            for label, key, formatter in fields:
                used_keys.add(key)
                rows.append((label, formatter(self._user_info.get(key))))
            sections.append(self._build_detail_group(title, rows))

        extra_rows = []
        for key in sorted(self._user_info.keys()):
            if key in used_keys:
                continue
            extra_rows.append((self._humanize_key(key), self._format_value(self._user_info.get(key))))
        if extra_rows:
            sections.append(self._build_detail_group('Additional Fields', extra_rows))
        return sections

    def _build_detail_group(self, title, rows):
        group = QGroupBox(title)
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(1)

        for index, (label, value) in enumerate(rows):
            row = index // 2
            col = (index % 2) * 2
            layout.addWidget(self._create_label_value_label(label, is_caption=True), row, col)
            layout.addWidget(self._create_label_value_label(value), row, col + 1)
        return group

    def _create_label_value_label(self, text, is_caption=False):
        label = QLabel(self._format_value(text))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if is_caption:
            label.setObjectName('userInfoCaption')
        return label

    @staticmethod
    def _copy_id(identifier, button):
        QGuiApplication.clipboard().setText(str(identifier))
        button.setText('Copied')

    def _load_avatar(self, avatar):
        url = QUrl(str(self._user_info.get('image_url') or ''))
        if url.scheme() != 'https' or not url.host():
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(5000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        reply = self._avatar_network.get(request)

        def update_avatar():
            if not isValid(avatar):
                return
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll().data()):
                avatar.setPixmap(pixmap.scaled(
                    72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))

        reply.finished.connect(update_avatar)
        reply.finished.connect(reply.deleteLater)

    def _avatar_fallback_text(self):
        text = (
            self._user_info.get('account_id')
            or self._user_info.get('user_name')
            or 'U'
        )
        return str(text).strip()[:1].upper() or 'U'

    @staticmethod
    def _format_value(value, empty=''):
        if value is None or value == '':
            return empty
        if isinstance(value, bool):
            return 'Yes' if value else 'No'
        if isinstance(value, int):
            return str(value)
        return str(value)

    @staticmethod
    def _format_bool(value):
        if value is None or value == '':
            return ''
        return 'Yes' if value else 'No'

    @staticmethod
    def _format_gender(value):
        if value in (None, ''):
            return ''
        return {1: 'Male', 2: 'Female'}.get(value, 'Unknown')

    @staticmethod
    def _format_birthday(value):
        text = str(value or '').strip()
        if len(text) == 8 and text.isdigit():
            return f'{text[:4]}-{text[4:6]}-{text[6:8]}'
        return text or ''

    @staticmethod
    def _humanize_key(key):
        words = str(key or '').replace('_', ' ').strip().split()
        return ' '.join(word.capitalize() for word in words) or 'Unknown'

    def _apply_style(self):
        self.setStyleSheet(
            'QFrame#userInfoHero {'
            ' background: transparent;'
            ' border: none;'
            '}'
            'QLabel#userInfoSubtle {'
            ' color: #6c665d;'
            '}'
            'QLabel#userInfoCaption {'
            ' color: #756f67;'
            ' font-size: 10px;'
            ' font-weight: 700;'
            '}'
            'QLabel#userInfoAvatar {'
            ' background: #e7dfd2;'
            ' border-radius: 36px;'
            ' border: 1px solid rgba(40, 40, 40, 0.10);'
            '}'
            'QGroupBox {'
             ' font-weight: 700;'
            ' margin-top: 8px;'
            '}'
            'QGroupBox::title {'
            ' subcontrol-origin: margin;'
            ' left: 4px;'
            ' padding: 0 2px;'
            '}'
        )


class UpdateDialog(QDialog):
    def __init__(self, current_version, latest_version, release_url, parent=None):
        super().__init__(parent)
        self._release_url = str(release_url or '')
        self.setWindowTitle('Update Available')
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel('A new version is available.')
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        details = QLabel(
            f'Current version: {current_version}\n'
            f'Latest version: {latest_version}\n'
            f'Release page: {self._release_url}'
        )
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.setWordWrap(True)
        layout.addWidget(details)

        buttons = QDialogButtonBox(self)
        self._open_button = buttons.addButton('Open Release Page', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Later', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def should_open_release_page(self):
        return self.result() == QDialog.DialogCode.Accepted and bool(self._release_url)
