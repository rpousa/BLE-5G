#!/usr/bin/env python3
"""IPv4 over a Bluetooth LE L2CAP connection-oriented channel (CoC): the non-3GPP access link of nrlink.

One IP packet per L2CAP SDU, PSM 0x0081, link subnet 10.70.0.0/24:
  core PC (access point, 10.70.0.1)  <-- Bluetooth LE -->  UE PC board k (10.70.0.1k), k = 1, 2

  core PC:  sudo ble_ip_link.py --listen [--hci 0]
            accepts the UEs; each gets its own point-to-point tun "ble<k>" 10.70.0.1 -> 10.70.0.1k.
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
import ctypes
import fcntl
import os
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
TUN_MTU = 1280
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
    sh("ip", "link", "set", name, "mtu", str(TUN_MTU), "up", netns=netns)
    for r in routes:
        sh("ip", "route", "replace", r, "dev", name, netns=netns)


def pump(sock, tun, name):
    """Move packets both ways until either side closes; returns when the link is gone."""
    stop = threading.Event()

    def up():  # tun -> Bluetooth
        try:
            while not stop.is_set():
                sock.send(os.read(tun, 2048))
        except OSError:
            pass
        stop.set()

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
        log(f"{name}: link error: {e}")
    stop.set()
    for c in (sock.close, lambda: os.close(tun)):
        try:
            c()
        except OSError:
            pass
    log(f"{name}: link down ({rx} packets received)")


def listen(args):
    local = args.local or "00:00:00:00:00:00"
    srv = l2cap_socket(local, BDADDR_LE_PUBLIC, PSM)
    srv.listen(4)
    log(f"L2CAP listener on PSM {PSM:#06x} ({local}), link subnet {NET}.0/24, waiting for the UEs")
    busy = set()

    def serve(conn, peer):
        try:
            conn.settimeout(10)
            h = conn.recv(16)
            conn.settimeout(None)
            if len(h) != 5 or not h.startswith(HELLO) or not 1 <= h[4] <= 9:
                log(f"{peer}: bad hello {h!r}, closing")
                conn.close()
                return
            k = h[4]
            if k in busy:
                log(f"{peer}: UE {k} is already connected, closing")
                conn.close()
                return
            busy.add(k)
            conn.send(HELLO + bytes([k]))
            name = f"ble{k}"
            tun = tun_open(name)
            tun_up(tun, name, f"{NET}.1", f"{NET}.1{k}")
            log(f"{name}: UE {k} connected from {peer}, {NET}.1 <-> {NET}.1{k}")
            pump(conn, tun, name)
            busy.discard(k)
        except OSError as e:
            log(f"{peer}: {e}")
            conn.close()

    while True:
        conn, peer = srv.accept()
        threading.Thread(target=serve, args=(conn, peer[0] if isinstance(peer, tuple) else peer), daemon=True).start()


def connect(args):
    k = args.index
    name = f"ble{k}"
    routes = args.route or []
    peer_type = BDADDR_LE_RANDOM if args.peer_random else BDADDR_LE_PUBLIC
    backoff = 2
    while True:
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
            tun = tun_open(name)
            tun_up(tun, name, f"{NET}.1{k}", f"{NET}.1", netns=args.netns, routes=routes)
            log(f"{name}: up, {NET}.1{k} <-> {NET}.1" + (f" in netns of pid {args.netns}" if args.netns else "")
                + (f", routes {' '.join(routes)}" if routes else ""))
            backoff = 2
            pump(s, tun, name)
        except OSError as e:
            log(f"{name}: {e}; retrying in {backoff}s")
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


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
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root (tun, netns)")
    if args.connect and not args.local:
        sys.exit("--connect needs --local <board controller address>")
    (listen if args.listen else connect)(args)


if __name__ == "__main__":
    main()
