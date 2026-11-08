"""Small macOS collectors. Layouts follow the Apple SDK headers listed in docs/TOP.md.

No shell per process, no sudo, and every owned Mach/IOKit/CF object is released.
"""

import ctypes as C
import errno
import os
import pwd
import socket
import sys
import time
from dataclasses import dataclass


U32, U64, I32, PTR = C.c_uint32, C.c_uint64, C.c_int32, C.c_void_p


class BsdInfo(C.Structure):
    _fields_ = [(name, U32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')] + [
        ('comm', C.c_char * 16), ('name', C.c_char * 32)] + [
        (name, U32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')] + [
        ('nice', I32), ('start_sec', U64), ('start_usec', U64)]


class TaskInfo(C.Structure):
    _fields_ = [(name, U64) for name in (
        'virtual', 'resident', 'user', 'system', 'threads_user', 'threads_system')] + [
        (name, I32) for name in ('policy', 'faults', 'pageins', 'cow', 'sent',
                                 'received', 'mach_calls', 'unix_calls', 'switches',
                                 'threads', 'running', 'priority')]


class TaskAllInfo(C.Structure):
    _fields_ = [('bsd', BsdInfo), ('task', TaskInfo)]


class VmInfo(C.Structure):
    _fields_ = [(name, U32) for name in ('free', 'active', 'inactive', 'wired')] + [
        (name, U64) for name in ('zero_fill', 'reactivations', 'pageins', 'pageouts',
                                 'faults', 'cow', 'lookups', 'hits', 'purges')] + [
        ('purgeable', U32), ('speculative', U32)] + [
        (name, U64) for name in ('decompressions', 'compressions', 'swapins', 'swapouts')] + [
        (name, U32) for name in ('compressed', 'throttled', 'external', 'internal')] + [
        ('uncompressed', U64), ('swapped', U64), ('future', U64 * 32)]


class SwapInfo(C.Structure):
    _fields_ = [('total', U64), ('available', U64), ('used', U64),
                ('page_size', C.c_int), ('encrypted', C.c_int)]


class IfData(C.Structure):
    _pack_ = 4
    _fields_ = [('prefix', C.c_ubyte * 8), ('mtu', U32), ('metric', U32)] + [
        (name, U64) for name in ('baudrate', 'ipackets', 'ierrors', 'opackets',
                                 'oerrors', 'collisions', 'ibytes', 'obytes',
                                 'imcasts', 'omcasts', 'iqdrops', 'noproto')] + [
        ('recvtiming', U32), ('xmittiming', U32), ('lastchange', I32 * 2)]


class IfMessage(C.Structure):
    _pack_ = 4
    _fields_ = [('length', C.c_ushort), ('version', C.c_ubyte),
                ('type', C.c_ubyte), ('addrs', I32), ('flags', I32),
                ('index', C.c_ushort), ('padding', C.c_ushort),
                ('snd_len', I32), ('snd_max', I32), ('drops', I32),
                ('timer', I32), ('data', IfData)]


class StatFs(C.Structure):
    _fields_ = [('bsize', U32), ('iosize', I32)] + [
        (name, U64) for name in ('blocks', 'bfree', 'bavail', 'files', 'ffree')] + [
        ('fsid', I32 * 2)] + [(name, U32) for name in (
        'owner', 'type', 'flags', 'subtype')] + [
        ('fstype', C.c_char * 16), ('mount', C.c_char * 1024),
        ('source', C.c_char * 1024), ('flags_ext', U32), ('reserved', U32 * 7)]


def bind(lib, name, result, *arguments):
    function = getattr(lib, name)
    function.restype, function.argtypes = result, arguments
    return function


def decoded(value):
    return bytes(value).split(b'\0', 1)[0].decode('utf-8', 'replace')


@dataclass(frozen=True)
class Process:
    pid: int
    started: tuple
    ppid: int
    uid: int
    user: str
    name: str
    rss: int
    cpu: float
    threads: int
    status: int
    command: str = ''

    @property
    def identity(self):
        return (self.pid, self.started)


class Registry:
    """Read only selected registry properties, rather than materializing its tree."""

    def __init__(self):
        cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.release = bind(cf, 'CFRelease', None, PTR)
        self.string = bind(cf, 'CFStringCreateWithCString', PTR, PTR, C.c_char_p, U32)
        self.get = bind(cf, 'CFDictionaryGetValue', PTR, PTR, PTR)
        self.type = bind(cf, 'CFGetTypeID', C.c_ulong, PTR)
        self.number_type = bind(cf, 'CFNumberGetTypeID', C.c_ulong)()
        self.string_type = bind(cf, 'CFStringGetTypeID', C.c_ulong)()
        self.data_type = bind(cf, 'CFDataGetTypeID', C.c_ulong)()
        self.number = bind(cf, 'CFNumberGetValue', C.c_bool, PTR, C.c_int, PTR)
        self.cstring = bind(cf, 'CFStringGetCString', C.c_bool, PTR, PTR, C.c_long, U32)
        self.data_length = bind(cf, 'CFDataGetLength', C.c_long, PTR)
        self.data_pointer = bind(cf, 'CFDataGetBytePtr', PTR, PTR)
        self.match = bind(io, 'IOServiceMatching', PTR, C.c_char_p)
        self.services = bind(io, 'IOServiceGetMatchingServices', C.c_int, U32, PTR, C.POINTER(U32))
        self.next = bind(io, 'IOIteratorNext', U32, U32)
        self.drop = bind(io, 'IOObjectRelease', C.c_int, U32)
        self.property = bind(io, 'IORegistryEntryCreateCFProperty', PTR, U32, PTR, PTR, U32)
        self.search = bind(io, 'IORegistryEntrySearchCFProperty', PTR, U32, C.c_char_p, PTR, PTR, U32)
        self.keys = {}

    def key(self, name):
        if name not in self.keys:
            self.keys[name] = self.string(None, name.encode(), 0x08000100)
        return self.keys[name]

    def value(self, pointer):
        if not pointer:
            return None
        kind = self.type(pointer)
        if kind == self.number_type:
            result = C.c_double()
            return result.value if self.number(pointer, 13, C.byref(result)) else None
        if kind == self.string_type:
            result = C.create_string_buffer(4096)
            return decoded(result) if self.cstring(pointer, result, len(result), 0x08000100) else None
        if kind == self.data_type:
            size = self.data_length(pointer)
            return C.string_at(self.data_pointer(pointer), min(size, 4096))
        return None

    def entries(self, class_name):
        iterator = U32()
        matching = self.match(class_name.encode())
        if not matching or self.services(0, matching, C.byref(iterator)):
            return
        try:
            while True:
                entry = self.next(iterator)
                if not entry:
                    break
                try:
                    yield entry
                finally:
                    self.drop(entry)
        finally:
            self.drop(iterator)

    def read(self, entry, name, children=False):
        if children:
            return self.search(entry, b'IOService', self.key(name), None, 1)
        return self.property(entry, self.key(name), None, 0)

    def close(self):
        for value in self.keys.values():
            self.release(value)
        self.keys.clear()


class MacCollector:
    """One sampling owner; snapshots contain plain Python values only."""

    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('ma top supports macOS only')
        lib = C.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        proc = C.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.sysctl = bind(lib, 'sysctl', C.c_int, PTR, U32, PTR, C.POINTER(C.c_size_t), PTR, C.c_size_t)
        self.byname = bind(lib, 'sysctlbyname', C.c_int, C.c_char_p, PTR, C.POINTER(C.c_size_t), PTR, C.c_size_t)
        self.pidinfo = bind(proc, 'proc_pidinfo', C.c_int, C.c_int, C.c_int, U64, PTR, C.c_int)
        self.listpids = bind(proc, 'proc_listallpids', C.c_int, PTR, C.c_int)
        self.host = bind(lib, 'mach_host_self', U32)()
        self.task_self = bind(lib, 'mach_task_self', U32)()
        self.host_cpu = bind(lib, 'host_processor_info', C.c_int, U32, C.c_int,
                             C.POINTER(U32), C.POINTER(C.POINTER(U32)), C.POINTER(U32))
        self.host_vm = bind(lib, 'host_statistics64', C.c_int, U32, C.c_int, PTR, C.POINTER(U32))
        self.deallocate = bind(lib, 'vm_deallocate', C.c_int, U32, C.c_ulong, C.c_ulong)
        self.port_drop = bind(lib, 'mach_port_deallocate', C.c_int, U32, U32)
        timebase = (U32 * 2)()
        if bind(lib, 'mach_timebase_info', C.c_int, PTR)(C.byref(timebase)):
            raise OSError('mach_timebase_info failed')
        self.tick_ns = timebase[0] / timebase[1]
        # x86_64 exports both the legacy statfs ABI and the 64-bit inode ABI.
        mount_symbol = 'getmntinfo$INODE64' if os.uname().machine == 'x86_64' else 'getmntinfo'
        self.mounts = bind(lib, mount_symbol, C.c_int, C.POINTER(C.POINTER(StatFs)), C.c_int)
        self.registry = Registry()
        self.total = self.sysnumber('hw.memsize', U64)
        self.argmax = self.sysnumber('kern.argmax')
        self.page_size = os.sysconf('SC_PAGE_SIZE')
        self.brand = decoded(self.sysbytes('machdep.cpu.brand_string'))
        self.previous_cpu = None
        self.previous_processes = {}
        self.commands, self.users = {}, {}
        self.command_times = {}
        self.command_bytes = 0
        self.previous_time = None
        self.previous_net, self.previous_disk = {}, {}
        self.capacities, self.capacity_time = [], float('-inf')

    def sysbytes(self, name):
        size = C.c_size_t()
        if self.byname(name.encode(), None, C.byref(size), None, 0):
            raise OSError(C.get_errno(), name)
        result = C.create_string_buffer(size.value)
        if self.byname(name.encode(), result, C.byref(size), None, 0):
            raise OSError(C.get_errno(), name)
        return result.raw[:size.value]

    def sysnumber(self, name, kind=U32):
        return kind.from_buffer_copy(self.sysbytes(name)).value

    def process(self, pid):
        result = TaskAllInfo()
        size = self.pidinfo(pid, 2, 0, C.byref(result), C.sizeof(result))
        if size != C.sizeof(result):
            error = C.get_errno() or errno.ESRCH
            raise OSError(error, os.strerror(error))
        return result

    def command(self, identity, refresh=False):
        """KERN_PROCARGS2 is read only on demand, bounded by the current PID set."""
        if not refresh and identity in self.commands and time.monotonic() - self.command_times[identity] < 5:
            return self.commands[identity]
        pid, started = identity
        if self.identity(pid) != identity:
            raise ProcessLookupError('Process identity changed')
        size = C.c_size_t(self.argmax)
        result = C.create_string_buffer(size.value)
        mib = (C.c_int * 3)(1, 49, pid)
        if self.sysctl(mib, 3, result, C.byref(size), None, 0):
            self.command_bytes -= sys.getsizeof(self.commands[identity]) if identity in self.commands else 0
            self.commands[identity] = ''
            self.command_times[identity] = time.monotonic()
            self.command_bytes += sys.getsizeof('')
            return ''  # Other users' arguments are permission restricted.
        argc = I32.from_buffer_copy(result.raw).value
        raw = result.raw[4:size.value]
        executable, separator, raw = raw.partition(b'\0')
        args = raw.lstrip(b'\0').split(b'\0', max(argc, 0))[:max(argc, 0)]
        value = b' '.join(args).decode('utf-8', 'replace') if separator else decoded(executable)
        if self.identity(pid) != identity:
            raise ProcessLookupError('Process identity changed')
        # Cache byte budget includes worst-case Python Unicode storage, not just UTF-8.
        cost = sys.getsizeof(value)
        old_cost = sys.getsizeof(self.commands[identity]) if identity in self.commands else 0
        if self.command_bytes - old_cost + cost <= 4 * 1024 * 1024:
            self.commands[identity] = value
            self.command_times[identity] = time.monotonic()
            self.command_bytes += cost - old_cost
        elif identity in self.commands:
            del self.commands[identity]
            self.command_times.pop(identity, None)
            self.command_bytes -= old_cost
        return value

    def identity(self, pid):
        value = self.process(pid).bsd
        return (pid, (value.start_sec, value.start_usec))

    def processes(self, elapsed, search_commands=False):
        capacity = max(1024, self.listpids(None, 0) + 128)
        # proc_listallpids returns a PID count (not a byte count).
        buffer = (C.c_int * capacity)()
        count = self.listpids(buffer, C.sizeof(buffer))
        if count < 0:
            raise OSError(C.get_errno(), 'proc_listallpids')
        rows, current = [], {}
        for pid in buffer[:min(count, capacity)]:
            if pid <= 0:
                continue
            try:
                info = self.process(pid)
            except OSError as error:
                if error.errno in (errno.ESRCH, errno.EPERM, errno.EACCES, errno.EINVAL):
                    continue
                raise
            bsd, task = info.bsd, info.task
            identity = (pid, (bsd.start_sec, bsd.start_usec))
            ticks = (task.user + task.system) * self.tick_ns
            old = self.previous_processes.get(identity)
            cpu = max(0, ticks - old) / (elapsed * 1e9) * 100 if old is not None and elapsed else 0.0
            current[identity] = ticks
            if bsd.uid not in self.users:
                try:
                    self.users[bsd.uid] = pwd.getpwuid(bsd.uid).pw_name
                except KeyError:
                    self.users[bsd.uid] = str(bsd.uid)
            command = self.commands.get(identity, '')
            if search_commands and (identity not in self.commands or
                                    time.monotonic() - self.command_times[identity] >= 5):
                try:
                    command = self.command(identity)
                except OSError:
                    pass  # Process exited between enumeration and argument lookup.
            rows.append(Process(pid, identity[1], bsd.ppid, bsd.uid, self.users[bsd.uid],
                                decoded(bsd.name or bsd.comm), task.resident, cpu,
                                task.threads, bsd.status, command))
        self.previous_processes = current
        self.commands = {key: value for key, value in self.commands.items() if key in current}
        self.command_times = {key: value for key, value in self.command_times.items() if key in current}
        self.command_bytes = sum(sys.getsizeof(value) for value in self.commands.values())
        live_users = {row.uid for row in rows}
        self.users = {key: value for key, value in self.users.items() if key in live_users}
        return rows

    def cpu(self):
        number, count = U32(), U32()
        pointer = C.POINTER(U32)()
        if self.host_cpu(self.host, 2, C.byref(number), C.byref(pointer), C.byref(count)):
            raise OSError('host_processor_info failed')
        try:
            ticks = list(pointer[:count.value])
        finally:
            self.deallocate(self.task_self, C.cast(pointer, PTR).value, count.value * C.sizeof(U32))
        old, self.previous_cpu = self.previous_cpu, ticks
        if old is None or len(old) != len(ticks):
            return None, [None] * number.value
        cores = []
        for index in range(number.value):
            delta = [(ticks[index * 4 + k] - old[index * 4 + k]) & 0xffffffff for k in range(4)]
            cores.append(100 * (1 - delta[2] / sum(delta)) if sum(delta) else 0.0)
        return sum(cores) / len(cores), cores

    def memory(self):
        value = VmInfo()
        count = U32(C.sizeof(value) // 4)
        if self.host_vm(self.host, 4, C.byref(value), C.byref(count)):
            raise OSError('host_statistics64 failed')
        # Reclaimable file-backed and purgeable pages are not application pressure.
        available = min(self.total, (value.free + value.inactive) * self.page_size)
        used = self.total - available
        swap = SwapInfo.from_buffer_copy(self.sysbytes('vm.swapusage'))
        return dict(total=self.total, used=used, available=available,
                    compressed=value.compressed * self.page_size,
                    wired=value.wired * self.page_size, swap_used=swap.used, swap_total=swap.total)

    def network(self):
        mib = (C.c_int * 6)(4, 17, 0, 18, 6, 0)  # CTL_NET/PF_ROUTE/AF_LINK/NET_RT_IFLIST2
        size = C.c_size_t()
        if self.sysctl(mib, 6, None, C.byref(size), None, 0):
            raise OSError(C.get_errno(), 'network counters')
        buffer = C.create_string_buffer(size.value)
        if self.sysctl(mib, 6, buffer, C.byref(size), None, 0):
            raise OSError(C.get_errno(), 'network counters')
        offset, counters = 0, {}
        while offset + C.sizeof(IfMessage) <= size.value:
            message = IfMessage.from_buffer_copy(buffer, offset)
            if message.length < 4 or offset + message.length > size.value:
                raise ValueError('Invalid routing interface message')
            if message.type == 0x12 and message.length >= C.sizeof(IfMessage):
                try:
                    name = socket.if_indextoname(message.index)
                except OSError:
                    name = str(message.index)
                if message.flags & 1 and not name.startswith('lo'):
                    counters[name] = (message.data.ibytes, message.data.obytes)
            offset += message.length
        return counters

    def disks(self):
        counters = {}
        registry = self.registry
        for entry in registry.entries('IOBlockStorageDriver'):
            stats = registry.read(entry, 'Statistics')
            name = registry.read(entry, 'BSD Name', children=True)
            try:
                if stats and name:
                    read = registry.value(registry.get(stats, registry.key('Bytes (Read)')))
                    write = registry.value(registry.get(stats, registry.key('Bytes (Write)')))
                    if read is not None and write is not None:
                        counters[registry.value(name)] = (int(read), int(write))
            finally:
                if stats:
                    registry.release(stats)
                if name:
                    registry.release(name)
        return counters

    def volumes(self):
        pointer = C.POINTER(StatFs)()
        count = self.mounts(C.byref(pointer), 2)  # MNT_NOWAIT: never wait for remote disks.
        if count <= 0:
            raise OSError(C.get_errno(), 'getmntinfo')
        result = []
        for index in range(count):
            volume = pointer[index]
            mount, source = decoded(volume.mount), decoded(volume.source)
            if source.startswith('/dev/') and (mount == '/' or mount == '/System/Volumes/Data'
                                               or mount.startswith('/Volumes/')):
                result.append(dict(mount=mount, source=source, total=volume.blocks * volume.bsize,
                                   used=(volume.blocks - volume.bfree) * volume.bsize,
                                   free=volume.bavail * volume.bsize))
        # Data has the meaningful APFS shared-space usage on modern macOS.
        return sorted(result, key=lambda item: (item['mount'] != '/System/Volumes/Data', item['mount']))

    def gpu(self):
        registry = self.registry
        for entry in registry.entries('IOAccelerator'):
            stats = registry.read(entry, 'PerformanceStatistics')
            name = registry.read(entry, 'model')
            try:
                if not stats:
                    continue
                utilization = registry.value(registry.get(stats, registry.key('Device Utilization %')))
                if utilization is None:
                    continue
                model = registry.value(name)
                if isinstance(model, bytes):
                    model = decoded(model)
                return dict(name=model or 'GPU', utilization=max(0, min(100, utilization)),
                            source='IOKit', frequency=None, power=None, temperature=None)
            finally:
                if stats:
                    registry.release(stats)
                if name:
                    registry.release(name)
        return dict(name='GPU', utilization=None, source='Unavailable',
                    frequency=None, power=None, temperature=None)

    @staticmethod
    def rates(current, previous, elapsed):
        return {name: dict(received=value[0], sent=value[1],
                           read=(value[0] - previous[name][0]) / elapsed
                           if elapsed and name in previous and value[0] >= previous[name][0] else None,
                           write=(value[1] - previous[name][1]) / elapsed
                           if elapsed and name in previous and value[1] >= previous[name][1] else None)
                for name, value in current.items()}

    def sample(self, search_commands=False):
        started = time.monotonic()
        elapsed = started - self.previous_time if self.previous_time is not None else None
        errors = []

        def collect(name, method, missing):
            try:
                return method()
            except (OSError, ValueError) as error:
                errors.append(name + ': ' + str(error))
                return missing

        cpu, cores = collect('CPU', self.cpu, (None, []))
        memory = collect('Memory', self.memory, None)
        network = collect('Network', self.network, {})
        disks = collect('Disk', self.disks, {})
        if started - self.capacity_time >= 5:
            self.capacities = collect('Volumes', self.volumes, [])
            self.capacity_time = started
        rows = collect('Processes', lambda: self.processes(elapsed, search_commands), [])
        snapshot = dict(time=started, cpu=cpu, cores=cores, brand=self.brand,
                        load=os.getloadavg(), memory=memory, gpu=collect('GPU', self.gpu, None),
                        network=self.rates(network, self.previous_net, elapsed),
                        disks=self.rates(disks, self.previous_disk, elapsed),
                        volumes=self.capacities, processes=rows, errors=errors)
        self.previous_time, self.previous_net, self.previous_disk = started, network, disks
        snapshot['sample_ms'] = (time.monotonic() - started) * 1000
        return snapshot

    def close(self):
        self.registry.close()
        self.port_drop(self.task_self, self.host)


def signal_process(process, sig, collector=None):
    """Signal exactly one confirmed identity, never a PID list or process group."""
    import signal
    if sig not in (signal.SIGTERM, signal.SIGKILL):
        raise ValueError('Unsupported process signal')
    if process.pid <= 1 or process.pid == os.getpid():
        raise PermissionError('Refusing to terminate PID 1 or the monitor itself')
    owned = collector is None
    collector = collector or MacCollector()
    try:
        if collector.identity(process.pid) != process.identity:
            raise ProcessLookupError('Process identity changed; signal cancelled')
        os.kill(process.pid, sig)
    finally:
        if owned:
            collector.close()
