#!/usr/bin/env python3
"""IPv4 over a Bluetooth LE L2CAP connection-oriented channel (CoC): the non-3GPP access link of nrlink.

One IP packet per L2CAP SDU, PSM 0x0081, link subnet 10.70.0.0/24:
  core PC (access point, 10.70.0.1)  <-- Bluetooth LE -->  UE PC board k (10.70.0.1k), k = 1, 2

  core PC:  sudo ble_ip_link.py --listen [--hci 0]
            turns on connectable LE advertising on hci<N> (btmgmt), accepts the UEs; each gets its own point-to-point tun "ble<k>" 10.70.0.1 -> 10.70.0.1k.
            Routing from ble+ to the N3IWF: core_routing.sh.
  UE PC:    sudo ble_ip_link.py --connect <core adapter address> --local <board address> --index <k>
                                [--netns <pid>] [--route 192.168.70.139/32]
            connects from the board controller with address <local>; tun "ble<k>" 10.70.0.1k -> 10.70.0.1,
            moved into the network namespace of <pid> (the n3iwue container) when given, with a route
            to the N3IWF through it. Reconnects when the link drops.

The first SDU each way is a hello, b"NRLK" + k, so the access point knows which UE is on the channel.
Needs root (tun, netns, and LE advertising on the core side: btmgmt --index N le on; advertising on).
"""
import argparse
import collections
import ctypes
import fcntl
import os
import select
import socket
import struct
import subprocess
import sys
import threading
import time

PSM = 0x0081
AF_BLUETOOTH, BTPROTO_L2CAP, SOL_BLUETOOTH, BT_RCVMTU = 31, 0, 274, 13
BDADDR_LE_PUBLIC, BDADDR_LE_RANDOM = 1, 2
L2CAP_MTU = 1500
# Shaping, tun -> Bluetooth on both ends. The hci_bb boards' 115200 baud UART has no flow control (MCU-Link VCOM):
# driven past its capacity (about 40 kbit/s of IP each way was measured) the controller stopped answering the host.
# Default pacing, per direction, sized for the 15 / 18.75 ms connection intervals the UEs ask for
# (conn_interval_ms): measured 10 Nov 2026, IP over IPsec, about 40 kbit/s up and 120 down clean (UDP, 500 B). The hci_bb v15
# boards (Pico, RTS) no longer need pacing to stay up, but it keeps the queues (and the round trip) short.
RATE_KBPS_DOWN = 150   # access point -> UE (the --listen side sends this way)
RATE_KBPS_UP = 50      # UE -> access point
RATE_KBPS = RATE_KBPS_UP   # set by main() from the role
RATE_BPS = RATE_KBPS * 1000 / 8
RATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ble_rate_kbps")   # optional override, kbit/s;
UNPACED_BPS = 250_000 / 8     # 0 = unpaced (L2CAP credits are the only limit); the bulk queue is then sized for this
_rate = [RATE_BPS, 0.0, None]  # current bytes/s, last check, file mtime


def rate_bps():
    """Pacing rate in bytes/s, from RATE_FILE if present (re-read at most once a second); 0 = unpaced."""
    now = time.monotonic()
    if now - _rate[1] >= 1.0:
        _rate[1] = now
        try:
            m = os.stat(RATE_FILE).st_mtime
            if m != _rate[2]:
                _rate[2] = m; _rate[0] = float(open(RATE_FILE).read().split()[0]) * 1000 / 8
                log(f"pacing {'off' if not _rate[0] else f'{_rate[0] * 8 / 1000:g} kbit/s'} ({RATE_FILE})")
        except (OSError, ValueError, IndexError):
            if _rate[2] is not None:
                log(f"pacing back to the default {RATE_KBPS} kbit/s")
            _rate[0], _rate[2] = RATE_BPS, None
    return _rate[0]
BURST = 1600          # bytes: one full packet at air speed. Larger bursts reach the hci_bb board faster than its
                      # UART passes them on; lost PDUs never return their L2CAP credits and the link stalls for good
CENTRAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ble_core_central")
CENTRAL_INTERVAL_MS = 15   # default per link when the core is the central
# Tried 11 Nov 2026 and NOT used: with the Intel 8265 as central of both links there is one clock and no drift, but
# once both links are busy it serves about one 27 B packet per link per event: 15 kbit/s down each (30 with the
# boards as centrals at 15 / 18.75 ms), 1000 B pings 3-8 s. Kept as an opt-in for other core adapters.


def central_ues():
    """Role switch, the same file on both PCs. If ble_core_central exists, the core's adapter is the LE central of
    every link: it connects to each UE board (lines "k ADDR [interval_ms [ce_ms]]", in connect order), sets the
    interval of both from its one clock, and the
    boards advertise and wait. Without it the boards are the centrals and connect to the advertising core (the
    original layout; each board then times its link by its own clock, see conn_interval_ms)."""
    try:
        ues = {}
        for line in open(CENTRAL_FILE):
            f = line.split("#")[0].split()
            if len(f) >= 2 and f[0].isdigit():
                ues[int(f[0])] = (f[1].upper(), float(f[2]) if len(f) > 2 else CENTRAL_INTERVAL_MS,
                                  float(f[3]) if len(f) > 3 else 0)
        return ues
    except OSError:
        return None


def conn_interval_ms(k):
    """UE k's LE connection interval, asked for after each connect (the link opens at 45 ms): 15 ms for UE 1,
    18.75 for UE 2, 3.75 ms more for each further UE. 1M PHY and 27 B link-layer packets are fixed by the core
    PC's Bluetooth 4.2 adapter, so more connection events per second is what raises throughput and cuts the RTT.
    The intervals must differ: each board is the central of its own link and times it by its own clock, and the
    Intel adapter (peripheral on both) serves one event at a time. With equal intervals the two links' events
    drift into each other and stay overlapped for minutes, and one link starves (10 Nov 2026: 0.3-1.3 s pings,
    1000 B pings 2-8 s, then the links swapped). With 15 / 18.75 ms they overlap briefly and often instead.
    7.5 ms is fastest for one link alone (143 kbit/s down) but leaves no room for a second."""
    return 15 + 3.75 * (k - 1)
SUPERVISION_MS = 420     # kept as negotiated at connect
MAX_WAIT = 1.0        # s of bulk data queued at the shaped rate (about 4 full packets); beyond that, drop
TUN_QLEN = 64         # packets in the tun's ring (the reader thread drains it at once)
SMALL = 300           # bytes: packets this small (pings, ACKs) are served before bulk
# (A small SO_SNDBUF on the L2CAP socket stalled every multi-PDU SDU for good: don't. The shaping above
# keeps the send queue short instead.)
TUN_MTU = 1500        # = L2CAP_MTU: the N3IWF IKE_AUTH reply (certificate) leaves its container as 1500 B packets with DF set; a smaller tun drops them
NET = "10.70.0"
HELLO = b"NRLK"

libc = ctypes.CDLL(None, use_errno=True)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def sockaddr_l2(addr, psm, addr_type):
    """struct sockaddr_l2 {family, psm (le16), bdaddr (reversed), cid, bdaddr_type} = 14 bytes."""
    ba = bytes(int(x, 16) for x in reversed(addr.split(":")))
    raw = struct.pack("<HH6sHBx", AF_BLUETOOTH, psm, ba, 0, addr_type)
    return ctypes.create_string_buffer(raw, len(raw)), len(raw)


def l2cap_socket(local, local_type, psm=0):
    """L2CAP SEQPACKET socket bound to a local LE controller address (psm: PSM for a listener, 0 for a client)."""
    fd = libc.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "socket(AF_BLUETOOTH, L2CAP): " + os.strerror(ctypes.get_errno()))
    s = socket.socket(fileno=fd)
    buf, ln = sockaddr_l2(local, psm, local_type)
    if libc.bind(fd, buf, ln) < 0:
        raise OSError(ctypes.get_errno(), f"bind {local}: " + os.strerror(ctypes.get_errno()))
    s.setsockopt(SOL_BLUETOOTH, BT_RCVMTU, struct.pack("<H", L2CAP_MTU))   # only valid once bound as LE
    return s


def sh(*cmd, netns=None):
    if netns:
        cmd = ("nsenter", "-t", str(netns), "-n") + cmd
    subprocess.run(cmd, check=True)


def tun_open(name):
    fd = os.open("/dev/net/tun", os.O_RDWR)
    IFF_TUN, IFF_NO_PI, TUNSETIFF = 0x0001, 0x1000, 0x400454CA
    fcntl.ioctl(fd, TUNSETIFF, struct.pack("16sH", name.encode(), IFF_TUN | IFF_NO_PI))
    return fd


def tun_up(fd, name, local, peer, netns=None, routes=()):
    if netns:
        sh("ip", "link", "set", name, "netns", str(netns))
    sh("ip", "addr", "add", local, "peer", peer + "/32", "dev", name, netns=netns)
    # short queue: the default 500 packets held minutes of traffic at the shaped rate (RTT under load 2-5 s)
    sh("ip", "link", "set", name, "mtu", str(TUN_MTU), "txqueuelen", str(TUN_QLEN), "up", netns=netns)
    for r in routes:
        sh("ip", "route", "replace", r, "dev", name, netns=netns)


def pump(sock, tun, name):
    """Move packets both ways until either side closes; returns when the link is gone. The tun stays open: the
    caller keeps it for the next connection of this UE, so a radio dropout does not destroy the interface (the
    n3iwue's IPsec runs over it and did not survive a new tun of the same name)."""
    stop = threading.Event()

    # Queueing happens here, not in the tun: the tun tail-drops once its ring is full, which with full-size
    # packets meant seconds of delay and lost pings. Two queues: small packets (pings, TCP ACKs, IKE) go first;
    # bulk keeps at most MAX_WAIT seconds at the shaped rate and tail-drops beyond that (head-drop kept
    # discarding the very segment TCP was waiting for: no TCP got through at all).
    prio, bulk = collections.deque(), collections.deque()
    qlock = threading.Condition()
    drops = [0, 0]                       # [bulk over MAX_WAIT, send buffer full]

    def reader():  # tun -> queues
        bulk_bytes = 0
        try:
            while not stop.is_set():
                # select with a timeout: a thread blocked in read() would keep the tun alive after close(),
                # and the next link of this UE would then fail with EBUSY on the same name
                if not select.select([tun], [], [], 0.5)[0]:
                    continue
                p = os.read(tun, 2048)
                # IKE is outer UDP (user data is ESP); dropped IKE keepalives made the n3iwue give up its session
                small = len(p) <= SMALL or (len(p) > 20 and p[0] >> 4 == 4 and p[9] == 17)
                with qlock:
                    if small:
                        if len(prio) < 256:
                            prio.append(p)
                    elif sum(len(x) for x in bulk) + len(p) <= int((rate_bps() or UNPACED_BPS) * MAX_WAIT) + TUN_MTU:
                        bulk.append(p)
                    else:
                        drops[0] += 1
                    qlock.notify()
        except OSError:
            pass
        stop.set()
        with qlock:
            qlock.notify()

    def up():  # queues -> Bluetooth, paced to RATE_KBPS
        n, size = 0, 0
        tokens, last = BURST, time.monotonic()
        try:
            while not stop.is_set():
                with qlock:
                    while not prio and not bulk and not stop.is_set():
                        qlock.wait(0.5)
                    if stop.is_set():
                        break
                    p = prio.popleft() if prio else bulk.popleft()
                size = len(p)
                now = time.monotonic()
                rb = rate_bps()
                tokens = min(BURST, tokens + (now - last) * (rb or UNPACED_BPS))
                last = now
                if rb and tokens < size:
                    time.sleep((size - tokens) / rb)
                    tokens, last = size, time.monotonic()
                elif not rb:
                    tokens = max(tokens, size)
                try:
                    sock.send(p)              # blocking: waits for L2CAP credits (MSG_DONTWAIT dropped every
                except BlockingIOError:       # multi-PDU SDU even on an idle link)
                    drops[1] += 1
                    continue
                tokens -= size
                n += 1
        except OSError as e:
            if not stop.is_set():
                log(f"{name}: send error after {n} packets (this one {size} B): {e}")
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)   # unblock the recv() below
        except OSError:
            pass

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()
    t = threading.Thread(target=up, daemon=True)
    t.start()
    rx = 0
    try:
        while not stop.is_set():
            d = sock.recv(L2CAP_MTU)
            if not d:
                break
            os.write(tun, d)
            rx += 1
    except OSError as e:
        if not stop.is_set():
            log(f"{name}: receive error after {rx} packets: {e}")
    else:
        if not stop.is_set():
            log(f"{name}: peer closed the channel")
    stop.set()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    t.join(2)
    rt.join(2)
    try:
        sock.close()
    except OSError:
        pass
    log(f"{name}: link down ({rx} packets received; dropped {drops[0]} bulk over the pacing rate (queue > {MAX_WAIT} s), {drops[1]} on a full send buffer)")


def btmgmt(hci, *cmd):
    """btmgmt hangs without a terminal: run it under script(1). Returns (ok, output)."""
    r = subprocess.run(["timeout", "6", "script", "-qec", " ".join(["btmgmt", "--index", str(hci)] + list(cmd)), "/dev/null"],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0 and "failed" not in out.lower()
    if not ok:
        log(f"btmgmt --index {hci} {' '.join(cmd)}: rc {r.returncode} {out[-200:]}")
    return ok, out


def advertise(hci):
    """Connectable LE advertising with the public address. 'advertising on' alone advertises non-connectable
    with a random address: the UEs then see the access point but cannot connect (LE connect times out)."""
    for c in (["power", "on"], ["le", "on"], ["privacy", "off"], ["connectable", "on"], ["advertising", "on"]):
        btmgmt(hci, *c)
    log(f"hci{hci}: connectable LE advertising on")


def readvertise(hci):
    """The controller stops advertising when a UE connects, and while an LE link exists the kernel's mgmt
    'advertising on' changes only flags. Send HCI LE Set Advertising Enable ourselves so the next UE can
    connect; the parameters and data the kernel set stay in the controller."""
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, 1)                     # BTPROTO_HCI
    try:
        s.setsockopt(0, 2, struct.pack("<IIIH2x", 1 << 4, 1 << 0x0E, 0, 0x200A))  # SOL_HCI, HCI_FILTER: Command Complete
        s.bind((hci,))
        s.settimeout(2)
        s.send(bytes([0x01, 0x0A, 0x20, 0x01, 0x01]))                       # LE Set Advertising Enable, on
        ev = s.recv(16)                                                      # 04 0e 04 ncmd 0a 20 status
        st = ev[6] if len(ev) > 6 else None
        log(f"hci{hci}: advertising re-enabled for the next UE" if st == 0 else
            f"hci{hci}: LE Set Advertising Enable returned status {st:#04x}" if st is not None else f"hci{hci}: short event {ev.hex()}")
    except OSError as e:
        log(f"hci{hci}: could not re-enable advertising: {e}")
    finally:
        s.close()


def hci_index(addr):
    """hciN (index) whose controller has this Bluetooth address, or None."""
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, 1)
    try:
        for i in range(16):
            buf = bytearray(struct.pack("<H", i) + bytes(90))
            try:
                fcntl.ioctl(s.fileno(), 0x800448D3, buf)                 # HCIGETDEVINFO
            except OSError:
                continue
            if ":".join(f"{b:02X}" for b in reversed(buf[10:16])) == addr.upper():
                return i
    finally:
        s.close()
    return None


def set_interval(sock, local, name, ms, hci=None, ce_ms=0):
    """The LE central (UE board, or the core in ble_core_central mode): ask for a ms connection interval on the
    link under this L2CAP socket (HCI LE Connection Update), and log what the controllers agreed on. Per
    connection: run after every (re)connect. hci: the adapter index, else found from the local address.
    ce_ms: connection event length to ask for (min = max; a hint the controller may ignore), 0 = no preference."""
    if not ms:
        return True
    try:
        info = sock.getsockopt(6, 2, 8)                                     # SOL_L2CAP, L2CAP_CONNINFO
        h = struct.unpack_from("<H", info)[0]
        if hci is None:
            hci = hci_index(local)
        if hci is None:
            raise OSError(f"no hci device with address {local}")
    except OSError as e:
        log(f"{name}: connection interval left as is ({e})"); return True
    units, to, ce = round(ms / 1.25), SUPERVISION_MS // 10, round(ce_ms / 0.625)
    r = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, 1)
    try:
        r.setsockopt(0, 2, struct.pack("<IIIH2x", 1 << 4, 1 << 0x0F, 1 << (0x3E - 32), 0))  # Command Status, LE Meta
        r.bind((hci,))
        r.send(bytes([0x01, 0x13, 0x20, 14]) + struct.pack("<HHHHHHH", h, units, units, 0, to, ce, ce))
        end = time.monotonic() + 4
        while time.monotonic() < end:
            r.settimeout(max(0.1, end - time.monotonic()))
            ev = r.recv(64)
            if ev[1] == 0x0F and ev[5:7] == b"\x13\x20" and ev[3] != 0:       # Command Status, refused
                log(f"{name}: hci{hci} refused the connection update, status {ev[3]:#04x}"); return True
            if ev[1] == 0x3E and ev[3] == 0x03 and struct.unpack_from("<H", ev, 5)[0] == h:
                st, iv = ev[4], struct.unpack_from("<H", ev, 7)[0]
                log(f"{name}: connection interval {iv * 1.25:g} ms" if st == 0 else
                    f"{name}: connection update failed, status {st:#04x}")
                return True
        log(f"{name}: no answer to the connection update")
    except OSError as e:
        log(f"{name}: connection update: {e}")
    finally:
        r.close()
    return False     # no answer: the caller retries (keep_interval)


def keep_interval(sock, local, name, ms, tries=4, every=5.0):
    """Retry a connection update that got no answer; if the peer never answers, close the link so it reconnects.
    Seen 11 Nov 2026: BLE1 reconnected, its update timed out, and the link stayed at the 45 ms it opens with
    (Bluetooth LiDAR 170 -> 265 ms) until a reconnect; a later hcitool lecup on that connection timed out too."""
    def run():
        for _ in range(tries):
            time.sleep(every)
            if sock.fileno() < 0:
                return
            if set_interval(sock, local, name, ms):
                return
        log(f"{name}: the peer never answered the connection update; reconnecting")
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    threading.Thread(target=run, daemon=True).start()


def listen(args):
    local = args.local or "00:00:00:00:00:00"
    ues = central_ues()
    if ues:
        log(f"ble_core_central: hci{args.hci} is the LE central, connecting to the UEs "
            + ", ".join(f"{k} {a} at {ms:g} ms (event {ce:g} ms)" for k, (a, ms, ce) in ues.items()) + f", link subnet {NET}.0/24")
        # stop advertising: the old mode's advertising stays on in the kernel, and an advertising adapter here
        # refuses to start LE connections (EBUSY) and lets the boards connect to it the old way
        for c in (["advertising", "off"], ["connectable", "off"]):
            btmgmt(args.hci, *c)
        srv = None
    else:
        advertise(args.hci)
        srv = l2cap_socket(local, BDADDR_LE_PUBLIC, PSM)
        srv.listen(4)
        log(f"L2CAP listener on PSM {PSM:#06x} ({local}), link subnet {NET}.0/24, waiting for the UEs")
    busy = set()
    tuns = {}   # UE index -> tun fd, kept across reconnects

    def serve(conn, peer):
        k = None
        try:
            conn.settimeout(10)
            h = conn.recv(16)
            conn.settimeout(None)
            if len(h) != 5 or not h.startswith(HELLO) or not 1 <= h[4] <= 9:
                log(f"{peer}: bad hello {h!r}, closing")
                conn.close()
                return
            if h[4] in busy:
                log(f"{peer}: UE {h[4]} is already connected, closing")
                conn.close()
                return
            k = h[4]
            busy.add(k)
            name = f"ble{k}"
            again = k in tuns
            if not again:
                tun = tun_open(name)
                try:
                    tun_up(tun, name, f"{NET}.1", f"{NET}.1{k}")
                except subprocess.CalledProcessError:
                    os.close(tun)
                    raise
                tuns[k] = tun
            conn.send(HELLO + bytes([k]))   # only once the tun is up, so the UE never sees a link that is not there
            log(f"{name}: UE {k} {'reconnected' if again else 'connected'} from {peer}, {NET}.1 <-> {NET}.1{k}")
            pump(conn, tuns[k], name)
        except (OSError, subprocess.CalledProcessError) as e:   # tun/ip failures too: never leave UE k marked busy
            log(f"{peer}: {e}")
            conn.close()
        finally:
            busy.discard(k)

    def dial(k, addr, ms, ce):   # ble_core_central mode: the core connects to UE k's board and keeps reconnecting
        name, backoff = f"ble{k}", 2
        while True:
            s = None
            try:
                s = l2cap_socket(local, BDADDR_LE_PUBLIC)
                buf, ln = sockaddr_l2(addr, PSM, BDADDR_LE_PUBLIC)
                if libc.connect(s.fileno(), buf, ln) < 0:
                    raise OSError(ctypes.get_errno(), "connect: " + os.strerror(ctypes.get_errno()))
                set_interval(s, local, name, ms, hci=args.hci, ce_ms=ce)
                backoff = 2
                serve(s, addr)            # hello, tun, pump; returns when the link goes down
            except OSError as e:
                log(f"{name}: {addr}: {e}; retrying in {backoff}s")
                try:
                    s and s.close()
                except OSError:
                    pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    if ues:
        for k, (addr, ms, ce) in ues.items():   # file order = connect order
            threading.Thread(target=dial, args=(k, addr, ms, ce), daemon=True).start()
            time.sleep(1)                 # one LE connection setup at a time
        while True:
            time.sleep(3600)
    while True:
        conn, peer = srv.accept()
        threading.Timer(0.5, readvertise, args=(args.hci,)).start()
        threading.Thread(target=serve, args=(conn, peer[0] if isinstance(peer, tuple) else peer), daemon=True).start()


def connect(args):
    if central_ues() is not None:
        return accept_core(args)
    k = args.index
    name = f"ble{k}"
    routes = args.route or []
    h = hci_index(args.local)
    if h is not None:   # left on by ble_core_central mode: a board that still advertises times out connecting as central
        btmgmt(h, "advertising", "off")
    peer_type = BDADDR_LE_RANDOM if args.peer_random else BDADDR_LE_PUBLIC
    backoff = 2
    s = None
    tun = None   # kept across reconnects, see pump()
    while True:
        if args.netns and not os.path.exists(f"/proc/{args.netns}"):
            log(f"{name}: pid {args.netns} (target network namespace) is gone, exiting")
            return
        try:
            s = l2cap_socket(args.local, BDADDR_LE_PUBLIC)
            buf, ln = sockaddr_l2(args.connect, PSM, peer_type)
            log(f"{name}: connecting {args.local} -> {args.connect} PSM {PSM:#06x}")
            if libc.connect(s.fileno(), buf, ln) < 0:
                raise OSError(ctypes.get_errno(), "connect: " + os.strerror(ctypes.get_errno()))
            s.send(HELLO + bytes([k]))
            s.settimeout(10)
            if s.recv(16) != HELLO + bytes([k]):
                raise OSError("no hello from the access point")
            s.settimeout(None)
            ms = conn_interval_ms(k) if args.interval is None else args.interval
            if not set_interval(s, args.local, name, ms):
                keep_interval(s, args.local, name, ms)
            again = tun is not None
            if not again:
                t = tun_open(name)
                try:
                    tun_up(t, name, f"{NET}.1{k}", f"{NET}.1", netns=args.netns, routes=routes)
                except subprocess.CalledProcessError:
                    os.close(t)
                    raise
                tun = t
            log(f"{name}: {'up again' if again else 'up'}, {NET}.1{k} <-> {NET}.1"
                + (f" in netns of pid {args.netns}" if args.netns else "")
                + (f", routes {' '.join(routes)}" if routes else ""))
            backoff = 2
            pump(s, tun, name)
        except (OSError, subprocess.CalledProcessError) as e:   # ip/nsenter failures too
            log(f"{name}: {e}; retrying in {backoff}s")
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


def accept_core(args):
    """UE side in ble_core_central mode: the board advertises (connectable, public address) and waits for the
    core's adapter, which connects as the LE central and sets the interval. Then the same hello, tun and pump."""
    k, name, routes = args.index, f"ble{args.index}", args.route or []
    hci = hci_index(args.local)
    if hci is None:
        log(f"{name}: no hci device with address {args.local}, exiting"); return
    advertise(hci)
    srv = l2cap_socket(args.local, BDADDR_LE_PUBLIC, PSM)
    srv.listen(1)
    log(f"{name}: ble_core_central: hci{hci} ({args.local}) advertises, waiting for the core {args.connect} on PSM {PSM:#06x}")
    tun = None
    while True:
        if args.netns and not os.path.exists(f"/proc/{args.netns}"):
            log(f"{name}: pid {args.netns} (target network namespace) is gone, exiting")
            return
        srv.settimeout(5)
        try:
            conn, peer = srv.accept()
        except socket.timeout:
            continue
        except OSError as e:
            log(f"{name}: accept: {e}"); time.sleep(2); continue
        peer = (peer[0] if isinstance(peer, tuple) else str(peer)).upper()
        if peer != args.connect.upper():
            log(f"{name}: connection from {peer}, not the core, closing"); conn.close(); continue
        try:
            conn.settimeout(10)
            conn.send(HELLO + bytes([k]))
            if conn.recv(16) != HELLO + bytes([k]):
                raise OSError("no hello from the access point")
            conn.settimeout(None)
            again = tun is not None
            if not again:
                t = tun_open(name)
                try:
                    tun_up(t, name, f"{NET}.1{k}", f"{NET}.1", netns=args.netns, routes=routes)
                except subprocess.CalledProcessError:
                    os.close(t)
                    raise
                tun = t
            log(f"{name}: {'up again' if again else 'up'} (core is central), {NET}.1{k} <-> {NET}.1"
                + (f" in netns of pid {args.netns}" if args.netns else "") + (f", routes {' '.join(routes)}" if routes else ""))
            pump(conn, tun, name)
        except (OSError, subprocess.CalledProcessError) as e:
            log(f"{name}: {e}")
            try:
                conn.close()
            except OSError:
                pass
        threading.Timer(0.5, readvertise, args=(hci,)).start()   # the controller stopped advertising on connect


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--listen", action="store_true", help="access point (core PC)")
    m.add_argument("--connect", metavar="ADDR", help="UE: Bluetooth address of the access point")
    ap.add_argument("--local", metavar="ADDR", help="local controller address (UE: required; core: default any)")
    ap.add_argument("--index", type=int, default=1, help="UE number k: tun ble<k>, address 10.70.0.1k")
    ap.add_argument("--netns", type=int, help="UE: move the tun into the network namespace of this pid")
    ap.add_argument("--route", action="append", help="UE: route through the link (repeatable)")
    ap.add_argument("--peer-random", action="store_true", help="the access point uses a random (static) address")
    ap.add_argument("--hci", type=int, default=0, help="access point: adapter index to advertise on (default 0)")
    ap.add_argument("--interval", type=float, help="UE: LE connection interval to ask for, ms (default 15 for UE 1, 18.75 for UE 2; 0 = keep 45)")
    args = ap.parse_args()
    global RATE_KBPS, RATE_BPS
    RATE_KBPS = RATE_KBPS_DOWN if args.listen else RATE_KBPS_UP
    RATE_BPS = _rate[0] = RATE_KBPS * 1000 / 8
    if os.geteuid() != 0:
        sys.exit("needs root (tun, netns)")
    if args.connect and not args.local:
        sys.exit("--connect needs --local <board controller address>")
    (listen if args.listen else connect)(args)


if __name__ == "__main__":
    main()
