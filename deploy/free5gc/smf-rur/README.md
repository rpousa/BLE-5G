# free5GC SMF with Remote UE report (TS 24.501 8.3.19 / 8.3.20)

free5GC v4.3.0 pins SMF commit 1c9d7662fb51de39df0591bad9c609ac85fec64d. Its nas library decodes the Remote UE report
message but leaves the Remote UE context list IE (9.11.4.29) as a stub, so the SMF answers it with 403. This patch
decodes the report before ParseGSM (remote_ue_report.go), keeps the remote UEs per SM context, logs each change and
answers with a Remote UE report response, which the AMF forwards to the relay in a DL NAS transport.

Build: check out that SMF commit, copy remote_ue_report*.go into internal/sbi/processor, apply pdu_session.go.patch, then
  docker run --rm -e CGO_ENABLED=0 -v $PWD:/src -w /src golang:1.26-alpine go build -trimpath -o smf-rur ./cmd
and `docker build -t free5gc/smf:v4.3.0-rur .` with that binary as ./smf next to this Dockerfile.
