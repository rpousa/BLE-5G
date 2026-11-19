#!/usr/bin/env python3
"""Step C: provision the sidelink relay UE (IMSI 208950000000039, nrUE key/OPc) with the static PDU address 12.1.1.200.
The remote UE behind it uses the same address; the relay gNB (NRLINK_REMOTE_IP=12.1.1.200) sends that downlink on the relay DRB."""
import sys
sys.path.insert(0, "/home/debian/oai/nrlink")
import f5gc_subscribers as f

IMSI, IP = "208950000000039", "12.1.1.200"
token = f.call("POST", "/login", {"username": "admin", "password": "free5gc"})["access_token"]
body = f.subscriber(IMSI, *f.NRUE)
body["SessionManagementSubscriptionData"][0]["dnnConfigurations"][f.DNN]["staticIpAddress"] = [{"ipv4Addr": IP}]
path = f"/subscriber/imsi-{IMSI}/{f.PLMN}"
try:
    f.call("DELETE", path, token=token)
except Exception:
    pass
f.call("POST", path, body, token=token)
got = f.call("GET", path, token=token)
print(IMSI, got["SessionManagementSubscriptionData"][0]["dnnConfigurations"][f.DNN].get("staticIpAddress"))
