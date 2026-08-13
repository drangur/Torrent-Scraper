"""Minimal bencode encoder/decoder (BitTorrent's serialization format).

Pure stdlib, no dependencies. Supports int, bytes, list, dict — enough for
tracker scrape responses and DHT KRPC messages.
"""


def encode(obj):
    out = []
    _encode(obj, out)
    return b''.join(out)


def _encode(obj, out):
    if isinstance(obj, int):
        out.append(b'i%de' % obj)
    elif isinstance(obj, (bytes, bytearray)):
        out.append(b'%d:' % len(obj))
        out.append(bytes(obj))
    elif isinstance(obj, str):
        data = obj.encode('utf-8')
        out.append(b'%d:' % len(data))
        out.append(data)
    elif isinstance(obj, list):
        out.append(b'l')
        for item in obj:
            _encode(item, out)
        out.append(b'e')
    elif isinstance(obj, dict):
        out.append(b'd')
        for key in sorted(obj.keys(), key=lambda k: k if isinstance(k, bytes) else k.encode()):
            _encode(key, out)
            _encode(obj[key], out)
        out.append(b'e')
    else:
        raise TypeError(f'Cannot bencode type {type(obj)}')


class DecodeError(Exception):
    pass


def decode(data):
    value, offset = _decode(data, 0)
    return value


def _decode(data, offset):
    ch = data[offset:offset + 1]
    if ch == b'i':
        end = data.index(b'e', offset)
        return int(data[offset + 1:end]), end + 1
    if ch == b'l':
        offset += 1
        items = []
        while data[offset:offset + 1] != b'e':
            item, offset = _decode(data, offset)
            items.append(item)
        return items, offset + 1
    if ch == b'd':
        offset += 1
        result = {}
        while data[offset:offset + 1] != b'e':
            key, offset = _decode(data, offset)
            value, offset = _decode(data, offset)
            result[key] = value
        return result, offset + 1
    if ch.isdigit():
        colon = data.index(b':', offset)
        length = int(data[offset:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise DecodeError(f'Unexpected token at offset {offset}: {ch!r}')
