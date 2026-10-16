"""Read-only observation of the user's disposable, unmodified SnapEdit client.

Captures remain encrypted; the matching private key is never sent to CI.
"""
import base64
import io
import json
from pathlib import Path
import os
import threading
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def seal(value, public_key_path, output):
    public_key = serialization.load_pem_public_key(Path(public_key_path).read_bytes())
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(value).encode(), b'snapedit-observation-v1')
    envelope = {'format': 'snapedit-observation-v1',
                'sealed_key': base64.b64encode(public_key.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode(),
                'nonce': base64.b64encode(nonce).decode(),
                'ciphertext': base64.b64encode(encrypted).decode()}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(envelope))
    temporary.replace(output)


def jwt_metadata(headers):
    authorization = next((v for k, v in headers if k.lower() == 'authorization'), '')
    if not authorization.startswith('Bearer '):
        return {'present': bool(authorization)}
    try:
        segment = authorization[7:].split('.')[1]
        payload = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
        allowed = {k: payload[k] for k in ('platform', 'version', 'is_premium', 'exp') if k in payload}
        return {'present': True, 'claims': allowed, 'other_claim_names': sorted(set(payload) - set(allowed))}
    except (ValueError, IndexError, UnicodeError):
        return {'present': True, 'decoded': False}


def summary(events):
    responses = {e['id']: e for e in events if e.get('kind') == 'response'}
    output = []
    for event in events:
        if event.get('kind') != 'request':
            continue
        url = urlsplit(event['url'])
        item = {'id': event['id'], 'host': url.hostname, 'path': url.path,
                'method': event['method'], 'at_ms': event['at_ms'],
                'header_names': sorted({k.lower() for k, _ in event['headers']}),
                'authorization': jwt_metadata(event['headers']),
                'body_length': event.get('body_length'),
                'body_captured': 'body_b64' in event,
                'status': responses.get(event['id'], {}).get('status')}
        output.append(item)
    return {'requests': output, 'observer_errors': [e.get('stage', e.get('kind')) for e in events if e.get('kind') in ('observer_error', 'script_error')],
            'ready': any(e.get('kind') == 'ready' for e in events)}


class Observer:
    def __init__(self, package, output, public_key, script_path):
        import frida
        self.events = []
        self.lock = threading.RLock()
        self.output = Path(output)
        self.public_key = public_key
        self.ready = threading.Event()
        self.device = frida.get_device_manager().add_remote_device('127.0.0.1:27042')
        pid = self.device.spawn([package])
        self.session = self.device.attach(pid)
        self.script = self.session.create_script(Path(script_path).read_text())
        self.script.on('message', self.message)
        self.script.load()
        self.device.resume(pid)
        if not self.ready.wait(25):
            self.checkpoint()
            raise RuntimeError('Read-only network observer did not initialize')

    def message(self, message, data):
        if message.get('type') == 'send' and isinstance(message.get('payload'), dict):
            event = message['payload']
        elif message.get('type') == 'error':
            # Raw script errors may include private request values: keep them sealed.
            event = {'kind': 'script_error', 'private_error': message}
        else:
            return
        with self.lock:
            self.events.append(event)
            if event.get('kind') == 'ready':
                self.ready.set()
            if event.get('kind') in ('request', 'response') and ('object_removal' in event.get('url', '') or event.get('kind') == 'response'):
                self.checkpoint()

    def checkpoint(self):
        with self.lock:
            seal({'events': self.events}, self.public_key, self.output / 'capture.sealed.json')
            (self.output / 'capture-summary.json').write_text(json.dumps(summary(self.events), indent=2))

    def close(self):
        try:
            self.checkpoint()
        finally:
            self.session.detach()
