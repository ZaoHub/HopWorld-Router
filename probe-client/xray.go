package main

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"net"

	"github.com/xtls/xray-core/app/dispatcher"
	"github.com/xtls/xray-core/app/proxyman"
	_ "github.com/xtls/xray-core/app/proxyman/outbound"
	"github.com/xtls/xray-core/common/log"
	xnet "github.com/xtls/xray-core/common/net"
	"github.com/xtls/xray-core/common/protocol"
	"github.com/xtls/xray-core/common/serial"
	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/proxy/trojan"
	"github.com/xtls/xray-core/proxy/vless"
	vlessout "github.com/xtls/xray-core/proxy/vless/outbound"
	"github.com/xtls/xray-core/transport/internet"
	"github.com/xtls/xray-core/transport/internet/reality"
	_ "github.com/xtls/xray-core/transport/internet/tcp"
	"github.com/xtls/xray-core/transport/internet/tls"
	"github.com/xtls/xray-core/transport/internet/websocket"
)

type xrayRequest struct {
	Protocol    string   `json:"protocol"`
	Address     string   `json:"address"`
	Port        uint32   `json:"port"`
	ID          string   `json:"id"`
	Flow        string   `json:"flow"`
	Password    string   `json:"password"`
	Network     string   `json:"network"`
	Security    string   `json:"security"`
	ServerName  string   `json:"server_name"`
	Fingerprint string   `json:"fingerprint"`
	ALPN        []string `json:"alpn"`
	PublicKey   string   `json:"public_key"`
	ShortID     string   `json:"short_id"`
	SpiderX     string   `json:"spider_x"`
	Path        string   `json:"path"`
	Host        string   `json:"host"`
	EarlyData   uint32   `json:"early_data"`
}

type silentXrayLog struct{}

func (silentXrayLog) Handle(log.Message) {}

func newXrayDialer(req *xrayRequest) (func(context.Context, string, string) (net.Conn, error), func() error, error) {
	log.RegisterHandler(silentXrayLog{})
	if req.Address == "" || req.Port == 0 || req.Port > 65535 {
		return nil, nil, errors.New("invalid node endpoint")
	}
	endpoint := &protocol.ServerEndpoint{
		Address: xnet.NewIPOrDomain(xnet.ParseAddress(req.Address)),
		Port:    req.Port,
		User:    &protocol.User{},
	}
	var proxy *serial.TypedMessage
	switch req.Protocol {
	case "vless":
		endpoint.User.Account = serial.ToTypedMessage(&vless.Account{Id: req.ID, Flow: req.Flow, Encryption: "none"})
		proxy = serial.ToTypedMessage(&vlessout.Config{Vnext: endpoint})
	case "trojan":
		endpoint.User.Account = serial.ToTypedMessage(&trojan.Account{Password: req.Password})
		proxy = serial.ToTypedMessage(&trojan.ClientConfig{Server: endpoint})
	default:
		return nil, nil, errors.New("unsupported outbound")
	}

	stream := &internet.StreamConfig{ProtocolName: req.Network}
	switch req.Network {
	case "tcp":
	case "ws":
		stream.TransportSettings = []*internet.TransportConfig{{
			ProtocolName: "websocket",
			Settings:     serial.ToTypedMessage(&websocket.Config{Host: req.Host, Path: req.Path, Ed: req.EarlyData}),
		}}
		stream.ProtocolName = "websocket"
	default:
		return nil, nil, errors.New("unsupported transport")
	}

	var security *serial.TypedMessage
	switch req.Security {
	case "none":
	case "tls":
		security = serial.ToTypedMessage(&tls.Config{
			ServerName: req.ServerName, Fingerprint: req.Fingerprint, NextProtocol: req.ALPN,
		})
	case "reality":
		if len(req.ShortID) > 16 {
			return nil, nil, errors.New("invalid Reality short ID")
		}
		key, keyErr := base64.RawURLEncoding.DecodeString(req.PublicKey)
		short := make([]byte, 8)
		_, shortErr := hex.Decode(short, []byte(req.ShortID))
		if keyErr != nil || len(key) != 32 || shortErr != nil {
			return nil, nil, errors.New("invalid Reality identity")
		}
		security = serial.ToTypedMessage(&reality.Config{
			ServerName: req.ServerName, Fingerprint: req.Fingerprint,
			PublicKey: key, ShortId: short, SpiderX: req.SpiderX,
		})
	default:
		return nil, nil, errors.New("unsupported security mode")
	}
	if security != nil {
		stream.SecurityType = security.Type
		stream.SecuritySettings = []*serial.TypedMessage{security}
	}

	instance, err := core.New(&core.Config{
		App: []*serial.TypedMessage{
			serial.ToTypedMessage(&dispatcher.Config{}),
			serial.ToTypedMessage(&proxyman.OutboundConfig{}),
		},
		Outbound: []*core.OutboundHandlerConfig{{
			ProxySettings:  proxy,
			SenderSettings: serial.ToTypedMessage(&proxyman.SenderConfig{StreamSettings: stream}),
		}},
	})
	if err != nil {
		return nil, nil, err
	}
	if err := instance.Start(); err != nil {
		instance.Close()
		return nil, nil, err
	}
	dial := func(ctx context.Context, _, address string) (net.Conn, error) {
		destination, err := xnet.ParseDestination("tcp:" + address)
		if err != nil {
			return nil, err
		}
		return core.Dial(ctx, instance, destination)
	}
	return dial, instance.Close, nil
}
