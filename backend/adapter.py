"""Selected GLPI 11.0.11 official API protocol from Trusted POC adapter.

No database business writes, no arbitrary path/field passthrough, no raw payload logs.
"""
import html
import logging
import re
import threading
import time
import httpx
from backend.config import settings

logger = logging.getLogger('serviceops.upstream')
ROOT = '/api.php/v2.3.0/Assistance/Ticket'


class Upstream(Exception):
    def __init__(self, code='upstream_failure', status=503):
        self.code, self.status = code, status


def plain(value):
    return html.unescape(re.sub('<[^>]*>', '', value or ''))


class Adapter:
    def __init__(self, client=None):
        self.client = client or httpx.Client(base_url=settings.glpi_url, timeout=15, trust_env=False)
        self.bearer, self.expires = None, 0
        self.lock = threading.Lock()

    def call(self, method, path, body=None):
        with self.lock:
            if not self.bearer or time.time() >= self.expires:
                try:
                    response = self.client.post('/api.php/token', data={
                        'grant_type': 'password', 'scope': 'api',
                        'client_id': settings.glpi_client_id, 'client_secret': settings.glpi_client_secret,
                        'username': settings.glpi_username, 'password': settings.glpi_password})
                    data = response.json()
                except (httpx.HTTPError, ValueError):
                    raise Upstream('executor_auth_unavailable') from None
                if response.status_code != 200 or not data.get('access_token'):
                    raise Upstream('executor_auth_denied')
                self.bearer = data['access_token']
                self.expires = time.time() + int(data.get('expires_in', 300)) - 30
        start = time.perf_counter()
        try:
            response = self.client.request(method, path, json=body,
                headers={'Authorization': 'Bearer ' + self.bearer})
            logger.info('upstream', extra={'safe': {'method': method, 'path': path,
                        'status': response.status_code, 'elapsed_ms': int((time.perf_counter()-start)*1000)}})
            if response.status_code == 401:
                self.expires = 0  # Retry only through a new business invocation, never blind retry a write.
            try:
                data = response.json()
            except ValueError:
                # GLPI TeamMember POST can acknowledge with an empty 201 body.
                # Status alone never proves ownership: bind is followed by read().
                data = {}
            return response.status_code, data
        except httpx.HTTPError:
            raise Upstream('upstream_result_unknown') from None

    @staticmethod
    def members(data):
        if isinstance(data, dict) and all(str(k).isdigit() and isinstance(v, dict) for k, v in data.items()):
            return list(data.values())
        return data

    def create(self, body, operation_id):
        status, data = self.call('POST', ROOT, {
            'name': 'SO_' + operation_id + ' ' + body['title'],
            'content': html.escape(body['description']), 'entity': settings.entity_id,
            'status': 1, 'urgency': 3, 'impact': 3})
        if status != 201 or not isinstance(data, dict) or not isinstance(data.get('id'), int):
            raise Upstream('create_not_verified')
        return data['id']

    def ticket(self, tid):
        status, data = self.call('GET', f'{ROOT}/{tid}')
        if status != 200 or not isinstance(data, dict):
            raise Upstream('ticket_read_failed')
        return data

    def requesters(self, tid):
        status, data = self.call('GET', f'{ROOT}/{tid}/TeamMember/requester')
        data = self.members(data)
        if status != 200 or not isinstance(data, list):
            raise Upstream('requester_read_failed')
        # Groups and unknown actor types cannot silently count as the intended sole requester.
        return data

    def bind(self, tid, uid):
        members = self.requesters(tid)
        if members == []:
            status, _ = self.call('POST', f'{ROOT}/{tid}/TeamMember',
                                 {'type': 'User', 'id': uid, 'role': 'requester'})
            if status != 201:
                raise Upstream('requester_pending')
        elif len(members) != 1 or members[0].get('type') != 'User' or members[0].get('id') != uid:
            raise Upstream('ownership_mismatch')

    def read(self, tid, uid):
        data, members = self.ticket(tid), self.requesters(tid)
        if (len(members) != 1 or members[0].get('type') != 'User' or members[0].get('id') != uid
                or data.get('entity', {}).get('id') != settings.entity_id):
            raise Upstream('ownership_mismatch')
        states = {1: 'new', 2: 'assigned', 3: 'assigned', 4: 'pending', 5: 'resolved', 6: 'closed', 10: 'pending'}
        state = states.get(data.get('status', {}).get('id'))
        if not state:
            raise Upstream('invalid_upstream_state')
        return {'native_id': tid, 'title': data['name'].split(' ', 1)[-1],
                'description': plain(data.get('content')), 'status': state}

    def replies(self, tid):
        status, data = self.call('GET', f'{ROOT}/{tid}/Timeline/Followup')
        if status != 200 or not isinstance(data, list):
            raise Upstream('replies_read_failed')
        result = []
        for entry in data:
            item = entry.get('item', {})
            if item.get('is_private') is not False:
                continue
            if item.get('itemtype') != 'Ticket' or item.get('items_id') != tid:
                raise Upstream('reply_parent_mismatch')
            result.append({'id': item['id'], 'content': plain(item.get('content')),
                           'author': 'IT 服务台', 'created_at': item.get('date_creation')})
        return result
