package processor

import (
	"encoding/hex"
	"testing"
)

func TestParseRemoteUECtxList(t *testing.T) {
	b, _ := hex.DecodeString("2e0203da760010010e0107010a6c00000100010a280102")
	l := int(b[5])<<8 | int(b[6])
	list, err := parseRemoteUECtxList(b[7 : 7+l])
	if err != nil || len(list) != 1 || list[0].Addr != "10.40.1.2" || list[0].UserIDs[0] != "type1:0a6c00000100" {
		t.Fatalf("got %+v, %v", list, err)
	}
}
