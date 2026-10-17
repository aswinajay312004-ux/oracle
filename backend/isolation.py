"""Run one untrusted parse in a short-lived worker process with a time limit and a memory cap.

A malformed or hostile upload (a zip bomb, a parser bug, a file that takes minutes to read) then stops
only the worker, never the web server or the Oracle session it holds.

* The worker is started with multiprocessing's 'spawn' method on every platform, so it behaves the
  same on the Windows laptop as on Linux: a fresh interpreter that imports only what the parse needs.
  The application must therefore be started from a guarded or module entry point (``python -m uvicorn
  backend.main:app`` is); multiprocessing re-imports the main module in the worker. The worker's first
  message says it started; if it dies or stalls before that (for example when the main module is a
  script read from stdin), ``WorkerFailed('spawn')`` is raised - a problem of the environment, never of
  the uploaded file - and backend/readers.py then parses in-process and logs an error.
* Wall-clock limit: the parent waits ``timeout`` seconds for an answer and then terminates the worker.
* Memory limit, applied by the worker to itself before it reads anything:
    - Linux and other POSIX systems: ``resource.setrlimit(RLIMIT_AS)`` (address space) plus
      ``RLIMIT_CPU`` (CPU seconds) as a second guard. macOS does not enforce RLIMIT_AS; the time
      limit still applies there.
    - Windows: a Job Object created through ctypes with ``ProcessMemoryLimit`` (committed memory) and
      ``PerProcessUserTimeLimit``, plus ``DIE_ON_UNHANDLED_EXCEPTION`` and ``SetErrorMode`` so a crash
      never leaves a "python.exe has stopped working" dialog holding the worker open. This is best
      effort: if Windows refuses (for example an old Windows 7 parent job that forbids nesting), the
      worker continues without the cap, the time limit still applies, and the refusal is logged.
* The result comes back as JSON bytes over a one-way Pipe. The parent never unpickles anything the
  worker produced, so even a worker taken over by a parser exploit cannot run code in the server.
"""
from __future__ import annotations
import json
import logging
import multiprocessing
import os
import sys
import threading
import time

log = logging.getLogger('poppys.isolation')

TIMEOUT = 60.0                       # seconds of wall-clock time per parse
MEMORY = 1536 * 1024 * 1024          # bytes: address space (POSIX) / committed memory (Windows)
MAX_RESULT = 384 * 1024 * 1024       # bytes of JSON the parent accepts: 1M cells x ~160 B + 32M characters of text
WORKERS = max(1, int(os.environ.get('POPPYS_PARSE_WORKERS', '2') or 2))
_slots = threading.BoundedSemaphore(WORKERS)
_job = None                          # the worker's Windows Job Object handle, kept open for its life
STARTED = b'started'
stats = {'started': 0, 'timeouts': 0, 'crashes': 0, 'last_failure': None}


class WorkerFailed(Exception):
    """The worker gave no usable answer. ``reason`` is one of: busy, spawn, timeout, memory, crashed,
    error, too_large."""

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason; self.detail = detail


def run(target, args=(), *, timeout=TIMEOUT, memory=MEMORY, passthrough=(ValueError,)):
    """``target(*args)`` in a spawned worker; returns its JSON-serialisable result.

    ``target`` must be a module-level function (it is imported by name in the worker). An exception
    raised by the worker that is an instance of one of ``passthrough`` is re-raised here with the same
    class and message; every other failure raises WorkerFailed."""
    if not _slots.acquire(timeout=timeout):
        raise WorkerFailed('busy', f'{WORKERS} uploads are already being read')
    try:
        return _run(target, args, timeout, memory, tuple(passthrough))
    finally:
        _slots.release()


def _run(target, args, timeout, memory, passthrough):
    ctx = multiprocessing.get_context('spawn')
    try:
        reader, writer = ctx.Pipe(duplex=False)
    except OSError as exc:            # out of handles / file descriptors
        raise WorkerFailed('spawn', repr(exc)) from exc
    cpu = int(timeout) + 30
    worker = ctx.Process(target=_child, args=(writer, target, args, memory, cpu, passthrough),
                         name='poppys-parse', daemon=True)
    started = time.monotonic(); stuck = False
    try:
        try:
            worker.start()
        except Exception as exc:      # cannot create a process at all (resources, frozen app, policy)
            raise WorkerFailed('spawn', repr(exc)) from exc
        finally:
            writer.close()            # the worker holds its own end; EOF here means it has gone
        stats['started'] += 1
        # The worker first says it is running our code. Dying or stalling before that is a problem of the
        # environment (for example a __main__ module that cannot be re-imported), never of the file.
        try:
            started_ok = reader.poll(timeout) and reader.recv_bytes(16) == STARTED
        except (EOFError, OSError):
            started_ok = False
        if not started_ok:
            stuck = worker.is_alive(); worker.join(0 if stuck else 2)
            stats['last_failure'] = f'worker did not start (exit code {worker.exitcode})'
            raise WorkerFailed('spawn', stats['last_failure'])
        if not reader.poll(max(0.0, timeout - (time.monotonic() - started))):
            stats['timeouts'] += 1; stats['last_failure'] = 'timeout'; stuck = True
            raise WorkerFailed('timeout', f'no answer after {timeout:.0f} s')
        try:
            data = reader.recv_bytes(MAX_RESULT)
        except EOFError:              # the worker died before answering: killed, out of memory, crashed
            worker.join(5)
            stats['crashes'] += 1; stats['last_failure'] = f'exit code {worker.exitcode}'
            raise WorkerFailed('crashed', f'exit code {worker.exitcode}') from None
        except OSError as exc:        # longer than MAX_RESULT
            raise WorkerFailed('too_large', str(exc)) from None
    finally:
        reader.close()
        _reap(worker, wait=0 if stuck else 2)
    try:
        message = json.loads(data)
    except ValueError as exc:
        raise WorkerFailed('error', f'unreadable answer from worker: {exc}') from None
    if not isinstance(message, dict):
        raise WorkerFailed('error', 'worker answer is not an object')
    for note in message.get('notes') or ():
        log.warning('parse worker: %s', note)
    log.debug('parse worker answered in %.2f s', time.monotonic() - started)
    if 'ok' in message:
        return message['ok']
    if 'raise' in message:
        index = message['raise']
        if isinstance(index, int) and 0 <= index < len(passthrough):
            raise passthrough[index](str(message.get('message') or ''))
    reason = message.get('failed') if message.get('failed') in ('memory', 'error') else 'error'
    stats['last_failure'] = reason
    raise WorkerFailed(reason, str(message.get('detail') or ''))


def _reap(worker, wait=2):
    """Make sure the worker is gone: after answering it gets `wait` seconds to exit, then is ended."""
    if worker.pid is None:
        return
    worker.join(wait)
    if worker.is_alive():
        worker.terminate(); worker.join(3)
    if worker.is_alive():
        worker.kill(); worker.join(3)
    try:
        worker.close()
    except ValueError:
        pass


# --- the worker -------------------------------------------------------------------------------------
def _child(conn, target, args, memory, cpu, passthrough):
    notes = []
    try:
        conn.send_bytes(STARTED)
        notes.extend(limit_self(memory, cpu))
        try:
            payload = {'ok': target(*args)}
        except MemoryError:
            payload = {'failed': 'memory'}
        except BaseException as exc:   # report everything; nothing may escape to multiprocessing's printer
            index = next((i for i, cls in enumerate(passthrough) if isinstance(exc, cls)), None)
            if index is not None:
                payload = {'raise': index, 'message': str(exc)}
            else:
                payload = {'failed': 'error', 'detail': f'{type(exc).__name__}: {str(exc)[:300]}'}
        payload['notes'] = notes
        try:
            # UTF-8, not \uXXXX escapes: a third of the size for Indic text. 'surrogatepass' keeps a lone
            # surrogate from a damaged file intact; json.loads() on bytes decodes it the same way.
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8', 'surrogatepass')
        except MemoryError:
            data = b'{"failed":"memory"}'
        except (TypeError, ValueError) as exc:
            data = json.dumps({'failed': 'error', 'detail': f'result is not JSON: {exc}'[:300]}).encode('ascii')
        conn.send_bytes(data)
    finally:
        conn.close()


def limit_self(memory, cpu):
    """Cap this process's memory and CPU time. Returns notes about limits that could not be applied."""
    if sys.platform == 'win32':
        try:
            _windows_job(memory, cpu)
            return []
        except Exception as exc:    # best effort on Windows; the parent's time limit still applies
            return [f'Windows Job Object limits not applied ({exc})']
    notes = []
    try:
        import resource
    except ImportError:
        return ['resource module unavailable; memory cap not applied']
    for name, value in (('RLIMIT_AS', memory), ('RLIMIT_CPU', cpu)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            _, hard = resource.getrlimit(limit)
            unlimited = hard == resource.RLIM_INFINITY
            soft = value if unlimited else min(value, hard)
            if name == 'RLIMIT_CPU' and unlimited:
                hard = soft + 5       # SIGXCPU at the soft limit, SIGKILL five seconds later
            resource.setrlimit(limit, (soft, hard))
        except (ValueError, OSError) as exc:
            if not (sys.platform == 'darwin' and name == 'RLIMIT_AS'):
                notes.append(f'{name} not applied ({exc})')
    return notes


# Windows Job Object structures (winnt.h). Sizes: 144 bytes on 64-bit Windows, 112 on 32-bit.
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002


def job_structures():
    """(JOBOBJECT_BASIC_LIMIT_INFORMATION, IO_COUNTERS, JOBOBJECT_EXTENDED_LIMIT_INFORMATION) ctypes types.
    DWORD is spelled c_uint32 (not wintypes.DWORD, which is 8 bytes on Linux) so the layout can be checked
    on any 64-bit platform: LARGE_INTEGER = int64, SIZE_T / ULONG_PTR = size_t."""
    import ctypes
    DWORD = ctypes.c_uint32

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', DWORD), ('SchedulingClass', DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    return JOBOBJECT_BASIC_LIMIT_INFORMATION, IO_COUNTERS, JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def _windows_job(memory, cpu):
    """Put this process in a new Job Object that caps its committed memory and CPU time."""
    global _job
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.SetErrorMode.argtypes = [wintypes.UINT]; kernel32.SetErrorMode.restype = wintypes.UINT
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]; kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]; kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []; kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
    _, _, Extended = job_structures()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_PROCESS_TIME |
                                             JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu) * 10_000_000     # 100-nanosecond units
    info.ProcessMemoryLimit = int(memory)
    if not kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    _job = job                        # never closed: the job ends with this process


# --- diagnostics (health page and tests) ------------------------------------------------------------
def diagnose(action='limits', amount=0):
    """Runs inside a worker. 'limits': the limits in force; 'sleep': sleep `amount` seconds;
    'allocate': allocate `amount` bytes; 'exit': end the process abruptly with code `amount`."""
    if action == 'sleep':
        time.sleep(amount)
    elif action == 'allocate':
        block = bytearray(int(amount)); block[-1] = 1
        return len(block)
    elif action == 'exit':
        os._exit(int(amount))
    out = {'pid': os.getpid(), 'platform': sys.platform}
    if sys.platform == 'win32':
        out['job_object'] = _job is not None
    else:
        try:
            import resource
            out['address_space'] = resource.getrlimit(resource.RLIMIT_AS)[0]
            out['cpu_seconds'] = resource.getrlimit(resource.RLIMIT_CPU)[0]
        except (ImportError, ValueError, OSError):
            pass
    return out
