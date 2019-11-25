"""
Handles the REST calls and responses.
"""

import json
import logging

import requests

LOG = logging.getLogger(__name__)

class ClientException(Exception):
    """
    The base exception class for all exceptions this library raises.
    """
    def __init__(self, code, message=None, details=None, response=None):
        self.code = code
        # NOTE(mriedem): Use getattr on self.__class__.message since
        # BaseException.message was dropped in python 3, see PEP 0352.
        self.message = message or getattr(self.__class__, 'message', None)
        self.details = details

    def __str__(self):
        formatted_string = "%s" % self.message
        if self.code >= 100:
            # HTTP codes start at 100.
            formatted_string += " (HTTP %s)" % self.code

        return formatted_string

class BadRequest(ClientException):
    """
    HTTP 400 - Bad request: you sent some malformed data.
    """
    http_status = 400
    message = "Bad request"


class Unauthorized(ClientException):
    """
    HTTP 401 - Unauthorized: bad credentials.
    """
    http_status = 401
    message = "Unauthorized"


class Forbidden(ClientException):
    """
    HTTP 403 - Forbidden: your credentials don't give you access to this
    resource.
    """
    http_status = 403
    message = "Forbidden"


class NotFound(ClientException):
    """
    HTTP 404 - Not found
    """
    http_status = 404
    message = "Not found"

_code_map = dict((c.http_status, c) for c in [BadRequest, Unauthorized,
                                              Forbidden, NotFound])

def from_response(response, body):
    """
    Return an instance of a ClientException or subclass
    based on a requests response.

    Usage::

        resp, body = requests.request(...)
        if resp.status_code != 200:
            raise exceptions.from_response(resp, resp.text)
    """
    cls = _code_map.get(response.status_code, ClientException)
    if body:
        message = "n/a"
        details = "n/a"
        if hasattr(body, 'error'):
            error = body['error']
            message = error.get('message', message)
            details = error.get('details', details)
        return cls(code=response.status_code, message=message, details=details,
                   response=response)
    else:
        return cls(code=response.status_code, message=response.reason,
                   response=response)

class HTTPClient(object):

    SENSITIVE_HEADERS = ('X-Auth-Token', 'X-Subject-Token',)
    USER_AGENT = 'python-httpclient'

    def __init__(self, username=None, password=None, timeout=None):
        self.username = username
        self.password = password
        self.timeout = timeout

    def request(self, url, method, **kwargs):
        kwargs.setdefault('headers', kwargs.get('headers', {}))
        kwargs['headers']['User-Agent'] = self.USER_AGENT
        kwargs['headers']['Accept'] = 'application/json'

        if 'body' in kwargs:
            kwargs['headers']['Content-Type'] = 'application/json'
            kwargs['data'] = json.dumps(kwargs.pop('body'))

        if self.timeout:
            kwargs.setdefault('timeout', self.timeout)

        resp = requests.request(method, url, **kwargs)

        body = None
        if resp.text:
            try:
                body = json.loads(resp.text)
            except ValueError as exc:
                LOG.error("Load http response text error: %s", exc)

        if resp.status_code >= 400:
            raise from_response(resp, body)

        return resp, body

    def send_request(self, url, method, **kwargs):
        try:
            resp, body = self.request(url, method, **kwargs)
            return resp, body
        except Exception as exc:
            LOG.error("Request error: %s" % exc)
            raise

    def get(self, url, **kwargs):
        return self.send_request(url, 'GET', **kwargs)

    def post(self, url, **kwargs):
        return self.send_request(url, 'POST', **kwargs)

    def put(self, url, **kwargs):
        return self.send_request(url, 'PUT', **kwargs)

    def delete(self, url, **kwargs):
        return self.send_request(url, 'DELETE', **kwargs)
