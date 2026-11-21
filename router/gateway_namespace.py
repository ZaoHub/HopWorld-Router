"""Linux namespace ownership for the TUN worker and independent firewall."""

from contextlib import contextmanager
import os
from pathlib import Path
import select
import subprocess
import sys

from router.egress_guard import IP


NETNS = Path('/run/netns')
NS_GET_USERNS = 0xB701
NSENTER = '/usr/bin/nsenter'
SETPRIV = '/usr/bin/setpriv'

# Hold the new namespaces while the privileged parent installs one-ID maps.
_NAMESPACE_HOLDER = """
import ctypes
import os
libc = ctypes.CDLL(None, use_errno=True)
if libc.unshare(0x10000000 | 0x40000000) != 0:  # CLONE_NEWUSER | CLONE_NEWNET
    raise OSError(ctypes.get_errno(), 'Unable to create private user and network namespaces')
os.write(1, b'1')
os.read(0, 1)
"""


@contextmanager
def mapped_namespace(uid, gid):
    with subprocess.Popen([sys.executable, '-I', '-c', _NAMESPACE_HOLDER],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as holder:
        try:
            if not select.select([holder.stdout], [], [], 4)[0] or holder.stdout.read(1) != b'1':
                raise ValueError('无法创建独立用户命名空间，请检查 Linux user namespace 支持')
            process = Path('/proc') / str(holder.pid)
            (process / 'uid_map').write_text(f'0 {uid} 1\n')
            (process / 'gid_map').write_text(f'0 {gid} 1\n')
            yield holder.pid
        finally:
            holder.stdin.close()
            try:
                holder.wait(timeout=2)
            except subprocess.TimeoutExpired:
                holder.kill()


def create_worker(gateway):
    if sys.platform != 'linux' or not Path(IP).is_file() or os.geteuid() != 0:
        raise ValueError('请在装有 iproute2 的 Linux 主机上以 root 创建网关命名空间')
    if (NETNS / gateway.worker).exists():
        raise ValueError('网关工作命名空间已经存在')
    with mapped_namespace(gateway.worker_uid, gateway.worker_gid) as pid:
        subprocess.run([IP, 'netns', 'attach', gateway.worker, str(pid)], check=True, timeout=8)


@contextmanager
def worker_authority(gateway):
    import fcntl

    with (NETNS / gateway.worker).open('rb') as worker, (NETNS / gateway.edge).open('rb') as edge:
        if os.path.samestat(os.fstat(worker.fileno()), os.fstat(edge.fileno())):
            raise ValueError('网关与防火墙的命名空间不可指向同一网络')
        worker_owner = fcntl.ioctl(worker.fileno(), NS_GET_USERNS)
        try:
            edge_owner = fcntl.ioctl(edge.fileno(), NS_GET_USERNS)
            try:
                host_user = os.stat('/proc/1/ns/user')
                if (os.path.samestat(os.fstat(worker_owner), host_user)
                        or not os.path.samestat(os.fstat(edge_owner), host_user)):
                    raise ValueError('请用 create-worker 创建工作命名空间，外侧防火墙须由系统用户命名空间拥有')
                yield worker_owner
            finally:
                os.close(edge_owner)
        finally:
            os.close(worker_owner)


def worker_privileges(net_admin=False):
    capabilities = '-all,+net_admin' if net_admin else '-all'
    parent_death = ['--pdeathsig=SIGKILL'] if net_admin else []
    return [
        SETPRIV, '--reuid=0', '--regid=0', '--clear-groups', '--no-new-privs',
        f'--bounding-set={capabilities}', f'--inh-caps={capabilities}', f'--ambient-caps={capabilities}',
        *parent_death,
    ]


def worker_command(gateway, authority, command, *, net_admin=False):
    return [
        NSENTER, f'--user=/proc/self/fd/{authority}', f'--net={NETNS / gateway.worker}',
        *worker_privileges(net_admin), *command,
    ]


def verify_worker_identity(gateway, authority):
    for resource, expected in (('uid_map', gateway.worker_uid), ('gid_map', gateway.worker_gid)):
        command = worker_command(gateway, authority, ['/usr/bin/cat', f'/proc/self/{resource}'])
        result = subprocess.run(command, pass_fds=(authority,), capture_output=True, text=True, check=True, timeout=5)
        if result.stdout.split() != ['0', str(expected), '1']:
            raise ValueError('网关的用户或组映射与配置的专用身份不符')
