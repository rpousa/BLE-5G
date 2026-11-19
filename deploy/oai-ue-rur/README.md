# OAI UE: Remote UE report over NAS

`nas_remote_ue_report.patch` (against openairinterface5g-develop as used here) lets a layer-3 UE-to-network relay send
the Remote UE report of TS 24.501 8.3.19 to the SMF:

- a thread in the NAS task listens on UDP 127.0.0.1:6620 (env `NAS_RAW5GSM_PORT`) in the UE's network namespace for
  `<PDU session ID><5GSM message>`; the relay's controller (`agv_chain.py`, `rur_5gsm`) encodes the report;
- the NAS task sends it in an integrity-protected, ciphered UL NAS transport (payload container type N1 SM information)
  for that PDU session (the relay session, DNN relay);
- the Remote UE report response (8.3.20) from the SMF, in a DL NAS transport, is logged and echoed back to the sender.

The SMF side is in `deploy/free5gc/smf-rur`. Build: apply the patch, then in the builder image
`docker run --rm -v $PWD:/oai-ran -w /oai-ran/build_nrlink --entrypoint sh ran-base:local -c "ninja nr-uesoftmodem"`.
