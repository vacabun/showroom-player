import time
import json
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
