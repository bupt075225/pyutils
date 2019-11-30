# -*- coding: utf-8 -*-

import errno
import logging
import logging.config
import logging.handlers
import os

class MakeDirFileHandler(logging.handlers.RotatingFileHandler):
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0,
                 encoding=None, delay=0):
        self._make_dir(os.path.dirname(filename))
        logging.handlers.RotatingFileHandler.__init__(
            self, filename, mode, maxBytes, backupCount, encoding, delay)

    @staticmethod
    def _make_dir(path):
        try:
            os.makedirs(path)
        except OSError as exc:
            if exc.errno == errno.EEXIST and os.path.isdir(path):
                pass
            else:
                raise

# To define a dictionary of logging settings. These settings describles the
# loggers, handlers, formatters and filters that you want in your logging setup.
CUSTOM_LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'require_debug_true': {
            '()': 'ssgw.utils.log.DebugLogFilter',
        },
    },
    'formatters': {
        'log_file': {
            'format': '%(asctime)s %(module)s:%(lineno)s %(levelname)s '
                      '%(message)s',
        },
    },
    'handlers': {
        'ssgw_log_file': {
            'level': 'DEBUG',
            'filters': ['require_debug_true'],
            'class': 'ssgw.utils.log.MakeDirFileHandler',
            'filename': '/var/log/ssgw/ssgw.log',
            'formatter': 'log_file',
            'maxBytes': 10485760,
            'backupCount': 10,
            'encoding': 'utf8'
        },
    },
    'loggers': {
        'ssgw': {
            'handlers': ['ssgw_log_file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

class DebugLogFilter(logging.Filter):
    def filter(self, record):
        ret = True
        #if record.levelno == logging.DEBUG:
        #    ret = True if settings.DEBUG else False
        return ret

def config_logging():
    logging.config.dictConfig(CUSTOM_LOGGING)
