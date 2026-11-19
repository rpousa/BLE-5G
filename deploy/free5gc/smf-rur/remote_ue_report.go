package processor

// Remote UE report (TS 24.501 8.3.19, 9.11.4.29), from a layer-3 UE-to-network relay on its relay PDU session
// (TS 23.304 5.4.4.2). The nas library's Remote UE context list IE is a stub, so the report is decoded here.
// Context encoding: length, number of user IDs, per ID: length, type (low 3 bits), value; then address type
// (low 3 bits: 1 = IPv4) and the address. The SMF keeps the list per SM context and logs each change.

import (
	"encoding/hex"
	"fmt"
	"net"
	"sync"

	smf_context "github.com/free5gc/smf/internal/context"
)

const (
	msgTypeRemoteUEReport    = 218
	msgTypeRemoteUEReportRsp = 219
	ieiRemoteUECtxConnected  = 0x76
	ieiRemoteUECtxDisconn    = 0x70
)

type RemoteUECtx struct {
	UserIDs []string
	Addr    string
}

var remoteUEs sync.Map // SM context ref -> map[user ID]RemoteUECtx

func parseRemoteUECtxList(b []byte) ([]RemoteUECtx, error) {
	if len(b) < 1 {
		return nil, fmt.Errorf("empty list")
	}
	n, p, out := int(b[0]), 1, []RemoteUECtx{}
	for k := 0; k < n; k++ {
		if p >= len(b) {
			return out, fmt.Errorf("context %d: short", k)
		}
		l := int(b[p])
		if p+1+l > len(b) || l < 2 {
			return out, fmt.Errorf("context %d: bad length %d", k, l)
		}
		c, q, ctx := b[p+1:p+1+l], 1, RemoteUECtx{}
		for i := 0; i < int(c[0]) && q < len(c); i++ {
			il := int(c[q])
			if q+1+il > len(c) || il < 1 {
				return out, fmt.Errorf("context %d: bad user ID", k)
			}
			ctx.UserIDs = append(ctx.UserIDs, fmt.Sprintf("type%d:%s", c[q+1]&7, hex.EncodeToString(c[q+2:q+1+il])))
			q += 1 + il
		}
		if q < len(c) && c[q]&7 == 1 && q+5 <= len(c) {
			ctx.Addr = net.IP(c[q+1 : q+5]).String()
		}
		out = append(out, ctx)
		p += 1 + l
	}
	return out, nil
}

// handleRemoteUEReport returns the Remote UE report response, or nil if b is not a Remote UE report.
func handleRemoteUEReport(smContext *smf_context.SMContext, b []byte) ([]byte, error) {
	if len(b) < 4 || b[3] != msgTypeRemoteUEReport {
		return nil, nil
	}
	v, _ := remoteUEs.LoadOrStore(smContext.Ref, &sync.Map{})
	known := v.(*sync.Map)
	for p := 4; p+3 <= len(b); {
		iei, l := b[p], int(b[p+1])<<8|int(b[p+2])
		if p+3+l > len(b) {
			return nil, fmt.Errorf("Remote UE report: IE 0x%02x overruns", iei)
		}
		list, err := parseRemoteUECtxList(b[p+3 : p+3+l])
		if err != nil {
			return nil, fmt.Errorf("Remote UE report: IE 0x%02x: %v", iei, err)
		}
		for _, c := range list {
			id := fmt.Sprint(c.UserIDs)
			switch iei {
			case ieiRemoteUECtxConnected:
				known.Store(id, c)
				smContext.Log.Infof("Remote UE report: remote UE %s connected, address %s, via relay %s PDU session %d (DNN %s, %s)",
					id, c.Addr, smContext.Supi, smContext.PDUSessionID, smContext.Dnn, smContext.PDUAddress)
			case ieiRemoteUECtxDisconn:
				known.Delete(id)
				smContext.Log.Infof("Remote UE report: remote UE %s disconnected, address %s, via relay %s PDU session %d",
					id, c.Addr, smContext.Supi, smContext.PDUSessionID)
			default:
				return nil, fmt.Errorf("Remote UE report: unknown IE 0x%02x", iei)
			}
		}
		p += 3 + l
	}
	n := 0
	known.Range(func(_, _ any) bool { n++; return true })
	smContext.Log.Infof("Remote UE report: relay %s now serves %d remote UE(s)", smContext.Supi, n)
	return []byte{b[0], b[1], b[2], msgTypeRemoteUEReportRsp}, nil
}
