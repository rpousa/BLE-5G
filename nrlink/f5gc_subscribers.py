#!/usr/bin/env python3
"""Provision the nrlink subscribers in free5GC through its webconsole API (idempotent).

  IMSI 208950000000036-038  the three OAI nrUEs (3GPP access over the boards)
  IMSI 208950000000040      the non-3GPP UE (n3iwue, via the N3IWF)

All on slice SST 1 / SD 010203, DNN internet (UE pool 12.1.1.0/24).
"""
import json
import sys
import urllib.error
import urllib.request

WEBUI = "http://192.168.70.147:5000/api"
PLMN = "20895"
SST, SD, DNN = 1, "010203", "internet"
NRUE = ("0C0A34601D4F07677303652C0462535B", "63bfa50ee6523365ff14c1f45f88737d")   # key, OPc (ue*.conf)
N3UE = ("b73a90cbcf3afb622dba83c58a8415df", "df0c67868fa25f748b7044c6e7c245b8")   # key, OPc (n3uecfg.yaml)
SUBSCRIBERS = {
    "208950000000036": NRUE,
    "208950000000037": NRUE,
    "208950000000038": NRUE,
    "208950000000040": N3UE,
}


def call(method, path, body=None, token=None):
    req = urllib.request.Request(WEBUI + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read()
        return json.loads(data) if data else None


def subscriber(imsi, key, opc):
    snssai = f"{SST:02x}{SD}"
    dnn_cfg = {
        "sscModes": {"defaultSscMode": "SSC_MODE_1", "allowedSscModes": ["SSC_MODE_2", "SSC_MODE_3"]},
        "pduSessionTypes": {"defaultSessionType": "IPV4", "allowedSessionTypes": ["IPV4"]},
        "sessionAmbr": {"uplink": "1 Gbps", "downlink": "1 Gbps"},
        "5gQosProfile": {"5qi": 9, "arp": {"priorityLevel": 8}, "priorityLevel": 8},
    }
    return {
        "plmnID": PLMN,
        "ueId": f"imsi-{imsi}",
        "AuthenticationSubscription": {
            "authenticationManagementField": "8000",
            "authenticationMethod": "5G_AKA",
            "milenage": {"op": {"encryptionAlgorithm": 0, "encryptionKey": 0, "opValue": ""}},
            "opc": {"encryptionAlgorithm": 0, "encryptionKey": 0, "opcValue": opc},
            "permanentKey": {"encryptionAlgorithm": 0, "encryptionKey": 0, "permanentKeyValue": key},
            "sequenceNumber": "16f3b3f70fc2",
        },
        "AccessAndMobilitySubscriptionData": {
            "gpsis": [f"msisdn-09000{imsi[-5:]}"],
            "nssai": {"defaultSingleNssais": [{"sst": SST, "sd": SD, "isDefault": True}], "singleNssais": []},
            "subscribedUeAmbr": {"uplink": "1 Gbps", "downlink": "2 Gbps"},
        },
        "SessionManagementSubscriptionData": [{"singleNssai": {"sst": SST, "sd": SD}, "dnnConfigurations": {DNN: dnn_cfg}}],
        "SmfSelectionSubscriptionData": {"subscribedSnssaiInfos": {snssai: {"dnnInfos": [{"dnn": DNN}]}}},
        "AmPolicyData": {"subscCats": ["free5gc"]},
        "SmPolicyData": {"smPolicySnssaiData": {snssai: {"snssai": {"sst": SST, "sd": SD},
                                                         "smPolicyDnnData": {DNN: {"dnn": DNN}}}}},
        "FlowRules": [],
        "QosFlows": [],
        "ChargingDatas": [],
    }


def main():
    token = call("POST", "/login", {"username": "admin", "password": "free5gc"})["access_token"]
    for imsi, (key, opc) in SUBSCRIBERS.items():
        path = f"/subscriber/imsi-{imsi}/{PLMN}"
        try:
            call("DELETE", path, token=token)  # replace whatever is there
        except urllib.error.HTTPError:
            pass
        call("POST", path, subscriber(imsi, key, opc), token=token)
        print(f"   subscriber imsi-{imsi}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
