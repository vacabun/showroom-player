import time
import json
from pathlib import Path
import requests
import m3u8
from urllib.parse import urlparse

fake_headers = {
    'Accept': '*/*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7,ja;q=0.6',
    'Accept-Charset': 'UTF-8,*;q=0.5',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/114.0.0.0 Safari/537.36'
    ),
}

session = requests.Session()
session.headers.update(fake_headers)
COOKIE_CACHE_PATH = Path.home() / '.showroom-player' / 'session-cookies.json'
DEFAULT_COOKIE_DOMAIN = 'www.showroom-live.com'


def save_session_cookies():
    COOKIE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for cookie in session.cookies:
        payload.append({
            'name': cookie.name,
            'value': cookie.value,
            'domain': cookie.domain or '',
            'path': cookie.path or '/',
            'secure': bool(cookie.secure),
            'expires': cookie.expires,
        })
    COOKIE_CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def load_session_cookies():
    if not COOKIE_CACHE_PATH.exists():
        return False
    try:
        payload = json.loads(COOKIE_CACHE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return False

    session.cookies.clear()
    for item in payload:
        set_session_cookie(
            item.get('name', ''),
            item.get('value', ''),
            item.get('domain', ''),
            item.get('path') or '/',
            secure=bool(item.get('secure', False)),
            expires=item.get('expires'),
        )
    return True


def clear_session_cookies():
    session.cookies.clear()
    try:
        COOKIE_CACHE_PATH.unlink()
    except FileNotFoundError:
        pass


def fetch_current_user():
    resp = session.get('https://www.showroom-live.com/api/current_user', timeout=10)
    data = resp.json()
    if not isinstance(data, dict):
        return {}
    return data


def fetch_current_user_name():
    data = fetch_current_user()
    if not data.get('is_login'):
        return ''
    return (
        data.get('account_id')
        or data.get('name')
        or data.get('user_name')
        or data.get('user', {}).get('account_id', '')
    )


def set_session_cookie(name, value, domain='', path='/', secure=False, expires=None):
    cookie_kwargs = {
        'path': path or '/',
        'secure': bool(secure),
        'expires': expires,
    }
    normalized_domain = (domain or '').strip()
    if normalized_domain:
        cookie_kwargs['domain'] = normalized_domain
    else:
        cookie_kwargs['domain'] = DEFAULT_COOKIE_DOMAIN
    session.cookies.set(name, value, **cookie_kwargs)


def parse_room_url_key(input_text):
    input_text = input_text.strip().rstrip('/')
    if 'showroom-live.com' in input_text:
        return input_text.split('/')[-1]
    return input_text


def get_roomid_by_room_url_key(room_url_key):
    url = 'https://www.showroom-live.com/api/room/status'
    response = session.get(url, params={'room_url_key': room_url_key}, timeout=10)
    data = response.json()
    return (
        data['room_id'],
        data.get('room_name', room_url_key),
        data.get('is_live', False),
        data.get('live_id', 0),
    )


def get_raw_stream_list(room_id):
    endpoint = (
        'https://www.showroom-live.com/api/live/streaming_url'
        '?room_id={room_id}&_={ts}&abr_available=1'
    ).format(room_id=room_id, ts=str(int(time.time() * 1000)))
    response = session.get(url=endpoint, timeout=10)
    data = json.loads(response.text)
    url_list = data.get('streaming_url_list', [])
    if not url_list:
        raise Exception('No streaming URLs available (room may be offline)')
    return url_list


def expand_hls_all(m3u8_url):
    try:
        playlist = m3u8.load(m3u8_url)
        if not playlist.playlists:
            return [(0, 'hls_all  |  Default', m3u8_url)]
        results = []
        for p in playlist.playlists:
            bw = p.stream_info.bandwidth
            url = p.uri
            if not url.startswith('http'):
                if url.startswith('/'):
                    parsed = urlparse(m3u8_url)
                    url = f'{parsed.scheme}://{parsed.netloc}{url}'
                else:
                    url = m3u8_url.rsplit('/', 1)[0] + '/' + url
            parts = ['hls_all', f'{bw // 1000} kbps']
            res = p.stream_info.resolution
            if res:
                parts.append(f'{res[0]}x{res[1]}')
            fps = p.stream_info.frame_rate
            if fps:
                parts.append(f'{fps:.0f}fps')
            results.append((bw, '  |  '.join(parts), url))
        results.sort(key=lambda x: x[0], reverse=True)
        return results
    except Exception:
        return [(0, 'hls_all  |  Default', m3u8_url)]


def get_all_streams(url_list):
    """Returns [(label, url), ...], HLS only, sorted by quality descending."""
    streams = []
    for item in url_list:
        stream_type = item.get('type', 'unknown')
        url = item['url']
        quality = item.get('quality', '')
        label_part = item.get('label', '')

        if stream_type == 'hls_all':
            streams.extend(expand_hls_all(url))
        elif stream_type == 'hls':
            parts = [stream_type]
            if quality:
                parts.append(str(quality))
            if label_part:
                parts.append(str(label_part))
            streams.append((-1, '  |  '.join(parts), url))
        # webrtc and other non-HLS types are skipped

    streams.sort(key=lambda x: x[0], reverse=True)
    return [(label, url) for _, label, url in streams]
