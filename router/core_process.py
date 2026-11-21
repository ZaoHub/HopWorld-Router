"""Run a managed core with its own files and process."""

import os
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class CoreProcess:
    def __init__(self, data_dir, binary, *, program, stem, env, command, config_flag='-c'):
        self.program = program
        self.env = env
        self.command = command
        self.config_flag = config_flag
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.data_dir / f'{stem}.json'
        self.meta_path = self.data_dir / f'{stem}.meta.json'
        self.pid_path = self.data_dir / f'{stem}.pid'
        self.log_path = self.data_dir / f'{stem}.log'
        self.binary = binary
        self._lock = threading.Lock()
        self._process = None
        self._error = ''
        if binary is None:
            self.locate()

    def locate(self):
        bundled = self.data_dir / 'bin' / self.program
        found = os.environ.get(self.env) or (str(bundled) if os.access(bundled, os.X_OK) else None) or shutil.which(self.program)
        if not found and os.access(f'/usr/local/bin/{self.program}', os.X_OK):
            found = f'/usr/local/bin/{self.program}'
        if not found and os.access(f'/usr/bin/{self.program}', os.X_OK):
            found = f'/usr/bin/{self.program}'
        if found or not self.binary or not os.access(self.binary, os.X_OK):
            self.binary = found
        return self.binary

    def stop(self):
        if os.name == 'nt':
            return
        with self._lock:
            self._stop_locked()

    def restart(self):
        with self._lock:
            if not self._alive_locked():
                raise ValueError('这个内核没有在运行，请先启动')
            self._restart_locked()

    def log_text(self):
        return _read_log(self.log_path)

    def running(self):
        if os.name == 'nt':
            return False
        with self._lock:
            return self._alive_locked()

    def _matches(self, payload, meta):
        try:
            return self.config_path.read_bytes() == payload and self.meta_path.read_bytes() == meta
        except OSError:
            return False

    def _write(self, path, payload):
        handle = tempfile.NamedTemporaryFile(dir=self.data_dir, delete=False)
        staged = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)

    def _alive_locked(self):
        if self._process is not None:
            return self._process.poll() is None
        return self._adopted_pid() is not None

    def _restart_locked(self):
        self._stop_locked()
        try:
            log = _capture_log(self.log_path)
        except OSError as exc:
            raise ValueError(f'无法写入 {self.program} 日志') from exc
        try:
            process = subprocess.Popen(
                [self.binary, *self.command, self.config_flag, str(self.config_path)],
                stdout=log, stderr=log, start_new_session=True,
            )
        except OSError as exc:
            raise ValueError(f'无法启动 {self.program}，请检查安装和执行权限') from exc
        finally:
            log.close()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise ValueError(f'{self.program} 启动后立即退出，请检查配置、证书和 TCP / UDP 端口占用')
        self._process = process
        self.pid_path.write_text(str(process.pid), encoding='utf-8')
        os.chmod(self.pid_path, 0o600)

    def _stop_locked(self):
        if self._process is not None and self._process.poll() is None:
            _signal(self._process.pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal(self._process.pid, signal.SIGKILL)
                self._process.wait(timeout=5)
        elif self._process is None:
            pid = self._adopted_pid()
            if pid is not None:
                _signal(pid, signal.SIGTERM)
                for _ in range(20):
                    if not self._owns_pid(pid):
                        break
                    time.sleep(0.05)
                else:
                    _signal(pid, signal.SIGKILL)
        self._process = None
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    def _read_pid(self):
        try:
            return int(self.pid_path.read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            return None

    def _adopted_pid(self):
        pid = self._read_pid()
        return pid if pid is not None and self._owns_pid(pid) else None

    def _owns_pid(self, pid):
        if pid <= 1 or not self.binary:
            return False
        try:
            result = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'command='],
                                    capture_output=True, text=True, errors='replace', timeout=1)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == ' '.join(
            [self.binary, *self.command, self.config_flag, str(self.config_path)]
        )


def _capture_log(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError('日志文件无效')
        if info.st_size > 200_000:
            os.ftruncate(fd, 0)
        return os.fdopen(fd, 'ab', closefd=True)
    except OSError:
        os.close(fd)
        raise


def _read_log(path):
    try:
        if path.is_symlink() or not path.is_file():
            return ''
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 8000))
            return handle.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def _signal(pid, sig):
    try:
        os.kill(pid, sig)
    except OSError:
        pass
