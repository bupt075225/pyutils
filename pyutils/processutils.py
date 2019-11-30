"""
System-level utilities and helper functions.
"""

import functools
import logging
import os
import random
import retrying
import signal
import subprocess
import sys
import threading
import time

from pyutils.utils import timeutils

LOG = logging.getLogger(__name__)

class UnknownArgumentError(Exception):
    def __init__(self, message=None):
        super(UnknownArgumentError, self).__init__(message)

class ProcessExecutionError(Exception):
    def __init__(self, stdout=None, stderr=None, exit_code=None, cmd=None,
                 description=None):
        super(ProcessExecutionError, self).__init__(
            stdout, stderr, exit_code, cmd, description)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        self.cmd = cmd
        self.description = description

    def __str__(self):
        description = self.description
        if description is None:
            description = "Unexpected error while running command."

        exit_code = self.exit_code
        if exit_code is None:
            exit_code = '-'

        message = ('%(description)s\n'
                    'Command: %(cmd)s\n'
                    'Exit code: %(exit_code)s\n'
                    'Stdout: %(stdout)r\n'
                    'Stderr: %(stderr)r') % {'description': description,
                                             'cmd': self.cmd,
                                             'exit_code': exit_code,
                                             'stdout': self.stdout,
                                             'stderr': self.stderr}
        return message

def _subprocess_setup(on_preexec_fn):
    # Python installs a SIGPIPE handler by default. This is usually not what
    # non-Python subprocesses expect.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    if on_preexec_fn:
        on_preexec_fn()

def execute(*cmd, **kwargs):
    """Helper method to shell out and execute a command through subprocess.

    Allows optional retry.

    :param cmd:             Passed to subprocess.Popen.
    :type cmd:              string
    :param cwd:             Set the current working directory
    :type cwd:              string
    :param process_input:   Send to opened process.
    :type process_input:    string or bytes
    :param env_variables:   Environment variables and their values that
                            will be set for the process.
    :type env_variables:    dict
    :param check_exit_code: Single bool, int, or list of allowed exit
                            codes.  Defaults to [0].  Raise
                            :class:`ProcessExecutionError` unless
                            program exits with one of these code.
    :type check_exit_code:  boolean, int, or [int]
    :param delay_on_retry:  True | False. Defaults to True. If set to True,
                            wait a short amount of time before retrying.
    :type delay_on_retry:   boolean
    :param attempts:        How many times to retry cmd.
    :type attempts:         int
    :param shell:           whether or not there should be a shell used to
                            execute this command. Defaults to false.
    :type shell:            boolean
    :param loglevel:        log level for execute commands.
    :type loglevel:         int.  (Should be logging.DEBUG or logging.INFO)
    :param preexec_fn:      This function will be called
                            in the child process just before the child
                            is executed. WARNING: On windows, we silently
                            drop this preexec_fn as it is not supported by
                            subprocess.Popen on windows (throws a
                            ValueError)
    :type preexec_fn:       function()
    :param interval: The multiplier
    :param backoff_rate: Base used for the exponential backoff
    :param timeout: Timeout defined in seconds
    :returns:               (stdout, stderr) from process execution
    :raises:                :class:`UnknownArgumentError` on
                            receiving unknown arguments
    :raises:                :class:`ProcessExecutionError`
    :raises:                :class:`OSError`
    """

    # Since python 2 doesn't have nonlocal we use a mutable variable to store
    # the previous attempt number, the timeout handler, and the process that
    # timed out
    shared_data = [0, None, None]

    def on_timeout(proc):
        LOG.warning('Stopping %(cmd)s with signal %(signal)s after %(time)ss.',
                    {'signal': sig_end, 'cmd': cmd, 'time': timeout})
        shared_data[2] = proc
        proc.send_signal(sig_end)

    def on_execute(proc):
        # This function will be called upon process creation with the object
        # as a argument.The Purpose of this is to allow the caller of
        # `processutils.execute` to track process creation asynchronously.
        # Sleep if this is not the first try and we have a timeout interval
        if shared_data[0] and interval:
            exp = backoff_rate ** shared_data[0]
            wait_for = max(0, interval * exp)
            LOG.debug('Sleeping for %s seconds', wait_for)
            time.sleep(wait_for)
        # Increase the number of tries and start the timeout timer
        shared_data[0] += 1
        if timeout:
            shared_data[2] = None
            shared_data[1] = threading.Timer(timeout, on_timeout, (proc,))
            shared_data[1].start()

    def on_completion(proc):
        # This function will be called upon process completion with the
        # object as a argument. The Purpose of this is to allow the caller of
        # `processutils.execute` to track process completion asynchronously.
        # This is always called regardless of success or failure
        # Cancel the timeout timer
        if shared_data[1]:
            shared_data[1].cancel()

    # We will be doing the wait ourselves in on_execute
    if 'delay_on_retry' in kwargs:
        interval = None
    else:
        kwargs['delay_on_retry'] = False
        interval = kwargs.pop('interval', 1)
        backoff_rate = kwargs.pop('backoff_rate', 2)

    cwd = kwargs.pop('cwd', None)
    process_input = kwargs.pop('process_input', None)
    env_variables = kwargs.pop('env_variables', None)
    check_exit_code = kwargs.pop('check_exit_code', [0])
    ignore_exit_code = False
    delay_on_retry = kwargs.pop('delay_on_retry', True)
    attempts = kwargs.pop('attempts', 1)
    shell = kwargs.pop('shell', False)
    loglevel = kwargs.pop('loglevel', logging.DEBUG)
    preexec_fn = kwargs.pop('preexec_fn', None)
    timeout = kwargs.pop('timeout', None)
    default_raise_timeout = kwargs.get('check_exit_code', True)

    if isinstance(check_exit_code, bool):
        ignore_exit_code = not check_exit_code
        check_exit_code = [0]
    elif isinstance(check_exit_code, int):
        check_exit_code = [check_exit_code]

    if kwargs:
        raise UnknownArgumentError('Got unknown keyword args: %r' % kwargs)

    cmd = [str(c) for c in cmd]

    watch = timeutils.StopWatch()
    while attempts > 0:
        attempts -= 1
        watch.restart()

        try:
            LOG.log(loglevel, 'Running cmd (subprocess): %s', cmd)
            _PIPE = subprocess.PIPE  # pylint: disable=E1101

            on_preexec_fn = functools.partial(_subprocess_setup,
                                              preexec_fn)
            close_fds = True

            obj = subprocess.Popen(cmd,
                                   stdin=_PIPE,
                                   stdout=_PIPE,
                                   stderr=_PIPE,
                                   close_fds=close_fds,
                                   preexec_fn=on_preexec_fn,
                                   shell=shell,  # nosec:B604
                                   cwd=cwd,
                                   env=env_variables)

            on_execute(obj)

            try:
                result = obj.communicate(process_input)
                obj.stdin.close()  # pylint: disable=E1101
                _returncode = obj.returncode  # pylint: disable=E1101
                LOG.log(loglevel, 'CMD "%s" returned: %s in %0.3fs',
                        cmd, _returncode, watch.elapsed())
            finally:
                on_completion(obj)

            if not ignore_exit_code and _returncode not in check_exit_code:
                (stdout, stderr) = result
                raise ProcessExecutionError(exit_code=_returncode,
                                            stdout=stdout,
                                            stderr=stderr,
                                            cmd=cmd)
            return result
        except (ProcessExecutionError, OSError) as err:
            # if we want to always log the errors or if this is
            # the final attempt that failed and we want to log that.
            if isinstance(err, ProcessExecutionError):
                format = ('%(desc)r\ncommand: %(cmd)r\n'
                          'exit code: %(code)r\nstdout: %(stdout)r\n'
                          'stderr: %(stderr)r')
                LOG.log(loglevel, format, {"desc": err.description,
                                           "cmd": err.cmd,
                                           "code": err.exit_code,
                                           "stdout": err.stdout,
                                           "stderr": err.stderr})
            else:
                format = ('Got an OSError\ncommand: %(cmd)r\n'
                          'errno: %(errno)r')
                LOG.log(loglevel, format, {"cmd": cmd, "errno": err.errno})

            if not attempts:
                LOG.log(loglevel, '%r failed. Not Retrying.', cmd)
                raise
            else:
                LOG.log(loglevel, '%r failed. Retrying.', cmd)
                if delay_on_retry:
                    time.sleep(random.randint(20, 200) / 100.0)
        finally:
            # NOTE(termie): this appears to be necessary to let the subprocess
            #               call clean something up in between calls, without
            #               it two execute calls in a row hangs the second one
            # NOTE(bnemec): termie's comment above is probably specific to the
            #               eventlet subprocess module, but since we still
            #               have to support that we're leaving the sleep.  It
            #               won't hurt anything in the stdlib case anyway.
            time.sleep(0)

def retry(exceptions, interval=1, retries=3, backoff_rate=2):

    def _retry_on_exception(e):
        return isinstance(e, exceptions)

    def _backoff_sleep(previous_attempt_number, delay_since_first_attempt_ms):
        exp = backoff_rate ** previous_attempt_number
        wait_for = max(0, interval * exp)
        LOG.debug("Sleeping for %s seconds", wait_for)
        return wait_for * 1000.0

    def _print_stop(previous_attempt_number, delay_since_first_attempt_ms):
        delay_since_first_attempt = delay_since_first_attempt_ms / 1000.0
        LOG.debug("Failed attempt %s", previous_attempt_number)
        LOG.debug("Have been at this for %s seconds",
                  delay_since_first_attempt)
        return previous_attempt_number == retries

    if retries < 1:
        raise ValueError(_('Retries must be greater than or '
                         'equal to 1 (received: %s). ') % retries)

    def _decorator(f):

        def _wrapper(*args, **kwargs):
            r = retrying.Retrying(retry_on_exception=_retry_on_exception,
                                  wait_func=_backoff_sleep,
                                  stop_func=_print_stop)
            return r.call(f, *args, **kwargs)

        return _wrapper

    return _decorator

def unlink_root(*links, **kwargs):
    """Unlink system links with sys admin privileges.

    By default it will raise an exception if a link does not exist and stop
    unlinking remaining links.

    This behavior can be modified passing optional parameters `no_errors` and
    `raise_at_end`.

    :param no_errors: Don't raise an exception on error
    "param raise_at_end: Don't raise an exception on first error, try to
                         unlink all links and then raise a ChainedException
                         with all the errors that where found.
    """
    no_errors = kwargs.get('no_errors', False)
    raise_at_end = kwargs.get('raise_at_end', False)
    exc = exception.ExceptionChainer()
    catch_exception = no_errors or raise_at_end
    for link in links:
        with exc.context(catch_exception, 'Unlink failed for %s', link):
            os.unlink(link)
    if not no_errors and raise_at_end and exc:
        raise exc
