# Bosch EasyAir / BCC110 — Local Control Research & Capture Guide

Goal: control a **Bosch BCC110** thermostat from Home Assistant **without the cloud**,
by talking to it directly over the LAN.

This document records what is known, what is unknown, and exactly what to do to
find out — including how to get a capture host onto the thermostat's IoT VLAN.

---

## 1. TL;DR feasibility

- Local control of the **older** BCC family (BCC100 / BCC101 / BCC102 / BCC50) is
  *proven* to be possible: those units use a High-Flying **HF-LPT230** serial-to-WiFi
  module running in transparent mode, which listens on **TCP 8899** and pipes bytes
  straight to the thermostat's microcontroller over UART. Anything the cloud can send,
  a LAN client can send.
- The **BCC110 is a different, newer device**. Your own captures show it talks to the
  **EasyAir / "smart-climate-ids"** cloud (`bccapi.smart-climate-ids.com`, AWS Cognito
  auth), *not* the older `connect.boschconnectedcontrol.com` WebSocket cloud used by the
  BCC100/101. Bosch's own advisory for the 8899 issue lists BCC101/BCC102/BCC50 and says
  the BCC100 is unaffected because it "uses a different WiFi module." The BCC110 is not
  mentioned anywhere.
- **Conclusion: local control of the BCC110 is plausible but unproven.** The only way to
  know is to get on its VLAN, scan it, and watch what it actually does on the wire. This
  guide is how you do that. Treat "port 8899 works" as a hopeful hypothesis to test, not
  a given.

---

## 2. Background: two separate Bosch ecosystems

| | BCC100 / BCC101 / BCC102 / BCC50 | **BCC110 (yours)** |
|---|---|---|
| App | Bosch Connected Control | **Bosch EasyAir** (`com.idsmobileapp` / `com.bosch.tt.easyair`) |
| Cloud | `connect.boschconnectedcontrol.com` | `bccapi.smart-climate-ids.com` + `auth.smart-climate-ids.com` |
| Cloud protocol | JSON over WebSocket | REST/HTTPS + AWS Cognito OAuth |
| WiFi module | HF-LPT230 (High-Flying) | Unknown — **verify by FCC ID / teardown / scan** |
| Local port | TCP 8899 (transparent UART bridge) | Unknown — **the thing to find** |

The `bosch_easyair` integration in this repo currently implements **only** the EasyAir
*cloud* contract. Local support would be an additional transport, ideally behind the same
`EasyAirClient` interface so `climate.py` / `coordinator.py` don't care where state comes
from.

---

## 3. How the proven (BCC100) local vector works

Worth understanding, because if the BCC110 uses the same class of module the mechanism
is identical:

- The HF-LPT230 default config is **TCP server on port 8899**, transparent transmission:
  every byte received on 8899 is forwarded verbatim to the MCU over UART, and vice versa.
- The cloud protocol on the older units is **JSON wrapped in WebSocket text frames**. The
  device firmware parses frames exactly as the cloud sends them — **unmasked**. So a LAN
  client just opens a TCP socket to `deviceip:8899` and writes a WebSocket text frame:

  ```
  0x81  <length>  <JSON bytes>
  ```

  - `0x81` = FIN + opcode 0x1 (text frame).
  - length byte < 0x80 means "no mask, payload length = that byte" (e.g. `0x46` = 70 bytes).
  - `0x7e` means "16-bit length follows" (e.g. `0x7e 0x01 0x33` = 307 bytes).
  - Bitdefender's public example (the firmware-update abuse, CVE-2023-49722):
    `\x81\x46{"cmd":"device/update","deviceid":"<mac>","timestamp":1111111}`

- Bitdefender only *documented* the firmware-update command because that was the scary
  finding, **but the same channel carries every normal command** (setpoint, mode,
  schedule). Those command names/fields were never published — you would recover them by
  capturing the cloud→device traffic (Section 6) and replaying/adapting them.

Everything above is the *template* to test against the BCC110. If the BCC110's module also
does transparent 8899, you replay its own captured frames locally and you're done. If it
doesn't, you fall back to one of the other outcomes in Section 8.

---

## 4. Step 1 — Get a capture host onto the IoT VLAN

Your dev machine can't currently reach the IoT VLAN. Pick whichever of these fits your
gear. You want a host that can (a) send packets to the thermostat, and ideally (b) see the
thermostat's traffic to the internet.

**Option A — Add a routed interface / firewall rule (simplest, least visibility).**
On your router/firewall (UniFi, pfSense, OPNsense, etcetera) temporarily allow your
management VLAN to reach the IoT VLAN, or give your machine a second IP on the IoT subnet.
This lets you *scan and connect to* the thermostat, but you won't automatically see its
cloud traffic (switched networks only show you your own + broadcast traffic).

**Option B — Put a Linux box directly on the IoT VLAN.** A spare laptop, NUC, or Pi with
its NIC tagged/access-ported onto the IoT VLAN. Best general-purpose choice: it can scan,
connect, run `mitmproxy`, and act as a fake gateway.

**Option C — Port mirror / SPAN (best passive visibility).** If your switch supports it,
mirror the thermostat's switch port (or the IoT VLAN uplink) to a port where your capture
host listens. You'll see *all* the thermostat's traffic without touching it. Managed
UniFi, Netgear, MikroTik, and most L2+ switches can do this.

**Option D — Capture at the router.** If the IoT gateway is your router, run
`tcpdump -i <iot-iface> host <thermostat-ip> -w bcc110.pcap` directly on it (pfSense/
OPNsense: Diagnostics → Packet Capture). Simple and sees everything to/from the WAN.

Whichever you pick, first find the thermostat's IP: check your router's DHCP leases for a
hostname containing `BCC`, `bosch`, `easyair`, or the OUI of its WiFi chip. Give it a DHCP
reservation so it doesn't move.

> Keep any temporary inter-VLAN allow rule scoped to the thermostat's IP and remove it when
> you're done — the whole reason it's on an IoT VLAN is to contain it.

---

## 5. Step 2 — Reconnaissance: scan the thermostat

From a host that can reach the IoT VLAN:

```bash
# Full TCP port sweep, service/version detection, no host-up ping (IoT often blocks ping)
nmap -Pn -p- -sV --reason -T4 <thermostat-ip> -oN bcc110-nmap.txt

# Then probe the interesting ones specifically
nmap -Pn -sV -p 80,443,1883,8080,8443,8883,8899,9999 <thermostat-ip>
```

What each result would mean:

- **8899 open** → strong signal it's the same transparent-UART design as the BCC100.
  Go straight to Section 7.
- **1883 / 8883 open (MQTT)** → it may run a local MQTT broker or expose MQTT; capture and
  inspect (Section 6). Some rebadged thermostats are Tuya-like under the hood.
- **80 / 443 / 8080 open** → local HTTP/REST or a config UI. `curl -k https://<ip>/` and
  poke around.
- **Only outbound, nothing open** → it's cloud-only for control; local push isn't
  available and you're limited to passive local *reads* (Section 8, outcome 3) or staying
  on the cloud API.

Also grab the WiFi module identity so you can predict behavior:
- Look up the BCC110 on `fccid.io` (FCC grantee/photos often reveal the WiFi module).
- If you're willing to open the unit, read the module's silkscreen. An HF-LPxxx / USR-style
  module strongly implies the 8899 transparent-bridge pattern.

---

## 6. Step 3 — Capture what the thermostat actually says

You need real traffic to know the protocol. Two layers matter: **local** frames (if any)
and **cloud** API calls (which you may end up re-pointing locally).

### 6a. Passive capture (SPAN / router — Options C/D above)

```bash
tcpdump -i <iface> -s0 -w bcc110.pcap host <thermostat-ip>
```

Then open in Wireshark. Change a setpoint from the EasyAir app and from the thermostat's
own touchscreen while capturing, so you can correlate packets to actions.

### 6b. Active capture on a switched network (no SPAN available)

Make your Linux capture host the thermostat's gateway so its traffic flows through you:

- **Cleanest:** in DHCP for the thermostat's IP, hand out *your* capture host as the
  gateway/DNS. Reboot the thermostat to pick it up.
- **Or ARP-spoof** just the thermostat ↔ real gateway pair:
  ```bash
  echo 1 > /proc/sys/net/ipv4/ip_forward
  ettercap -T -M arp:remote /<thermostat-ip>// /<gateway-ip>//
  ```

### 6c. Decrypt the cloud TLS (EasyAir uses HTTPS)

The EasyAir REST calls are TLS. To read them you must terminate TLS with `mitmproxy`:

```bash
mitmproxy --mode transparent --showhost
# route the thermostat's :443 through this host (gateway/DNS trick from 6b),
# and add mitmproxy's CA so the device trusts it — see note below.
```

- If the thermostat **does not pin certificates**, installing mitmproxy's CA on the path is
  usually not even possible (you can't add a CA to the thermostat), so transparent TLS MITM
  of the *device itself* typically **fails** on an embedded client — it either accepts your
  cert (unlikely) or refuses. In practice you have better luck decrypting the **phone app**:
  run the EasyAir app through mitmproxy on a phone where you *can* install the CA, and read
  the same API that way. That's almost certainly how the existing `api.py` contract in this
  repo was captured (the code comments reference "Charles traces").
- If it **does pin**, even the app route needs Frida/objection SSL-unpinning on a rooted/
  jailbroken device.
- Practical takeaway: **cloud protocol → capture via the app**, **local protocol → capture
  via SPAN/tcpdump of the device.** For the local-control goal, 6a/6b (plaintext local
  frames) is what matters most; the 8899 UART bridge on BCC100-class units is **not** TLS,
  so if the BCC110 behaves the same, you'll see plaintext WebSocket/JSON frames directly.

### 6d. What to look for

- Any **plaintext** TCP stream on the LAN carrying `{"cmd":...}` JSON → that's the local
  control channel; note the frame framing bytes and every `cmd`.
- Whether the device makes any **inbound-reachable** service calls or only dials out.
- The exact JSON for: set temperature, set mode, fan, and status/telemetry.

---

## 7. Step 4 — Test the 8899 transparent bridge (if open)

If Section 5 showed 8899 open, try talking to it. Start read-only: just connect and see if
the device streams anything.

```python
import socket, json, time

DEV = ("<thermostat-ip>", 8899)

def ws_text_frame(payload: bytes) -> bytes:
    # Unmasked WebSocket text frame (server-style), as the BCC firmware expects.
    n = len(payload)
    if n < 126:
        header = bytes([0x81, n])
    elif n < 65536:
        header = bytes([0x81, 0x7e]) + n.to_bytes(2, "big")
    else:
        header = bytes([0x81, 0x7f]) + n.to_bytes(8, "big")
    return header + payload

s = socket.create_connection(DEV, timeout=5)
s.settimeout(5)
# 1) Just listen first — many transparent bridges echo device->cloud chatter.
try:
    while True:
        data = s.recv(4096)
        if not data:
            break
        print("RX", data)
except socket.timeout:
    pass

# 2) ONLY after you've captured a real status/setpoint command in Section 6,
#    reproduce it here. Example shape (fields are placeholders — use YOUR capture):
# cmd = {"cmd": "device/status", "deviceid": "<mac>", "timestamp": int(time.time())}
# s.sendall(ws_text_frame(json.dumps(cmd, separators=(",", ":")).encode()))
# print("RX", s.recv(4096))
```

**Do not send the `device/update` firmware command** — that's the CVE and can brick the
unit. Only replay benign status/setpoint/mode commands you captured from the device's own
normal operation.

---

## 8. Step 5 — Decision tree / outcomes

1. **8899 (or similar) open + plaintext JSON captured → full local control.**
   Recover the setpoint/mode/status `cmd`s from capture, implement a `LocalEasyAirClient`
   with the same method surface as `EasyAirClient` (`async_get_thermostats`,
   `async_set_temperature`, `async_set_hvac_mode`, `async_set_fan`) that speaks framed JSON
   over the socket. Add a "local" option to the config flow (host/IP instead of tokens).
   `climate.py` and `coordinator.py` shouldn't need changes.

2. **Local MQTT / HTTP found → local control via that transport.** Same plan, different
   client implementation.

3. **Nothing inbound, but you can see its telemetry passively → read-only local, cloud
   writes.** Parse the passive stream for current temp/humidity/state (fast, no cloud
   poll), keep using the cloud API for commands. A hybrid coordinator.

4. **Fully cloud-locked (pinned, no local port, encrypted local link) → stay on cloud.**
   In that case the best "local-ish" win is running your own DNS to keep the cloud
   dependency inside your network only where possible, but real offline control isn't
   available. Document it and move on.

Whatever you find, capture the pcaps and the exact JSON and drop them next to this file so
the protocol is recorded.

---

## 9. Notes

- This is your own device on your own network — scanning, capturing, and replaying its
  traffic for interoperability is fine. Just don't run the firmware-update path.
- Keep firmware in mind: if Bosch pushes an update that closes a local port (as they did
  for the 8899 CVE on BCC101/102/50 in firmware 4.13.33), a working local integration could
  stop working. Consider blocking the thermostat's outbound internet at the firewall once
  local control works, if you want to freeze its firmware — but verify local control is
  solid first, and know that also disables cloud/remote access and weather.
