// hopworld-probe sends one HTTP request through a node and prints the HTTP status.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/apernet/hysteria/core/v2/client"
	"github.com/apernet/hysteria/extras/v2/obfs"
)

type probeRequest struct {
	Target       string       `json:"target"`
	TimeoutMS    int          `json:"timeout_ms"`
	ProxyURL     string       `json:"proxy_url"`
	Server       string       `json:"server"`
	ServerName   string       `json:"server_name"`
	Password     string       `json:"password"`
	ObfsPassword string       `json:"obfs_password"`
	Xray         *xrayRequest `json:"xray"`
}

type salamanderConn struct {
	password string
}

func (c salamanderConn) New(addr net.Addr) (net.PacketConn, error) {
	socket, err := net.ListenUDP("udp", nil)
	if err != nil {
		return nil, err
	}
	wrapped, err := obfs.WrapPacketConnSalamander(socket, []byte(c.password))
	if err != nil {
		socket.Close()
	}
	return wrapped, err
}

func run() int {
	var req probeRequest
	if json.NewDecoder(io.LimitReader(os.Stdin, 16*1024)).Decode(&req) != nil ||
		req.TimeoutMS <= 0 || req.TimeoutMS > 60000 {
		return 2
	}
	target, err := url.Parse(req.Target)
	if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") {
		return 2
	}
	transport := &http.Transport{DisableKeepAlives: true, MaxResponseHeaderBytes: 64 * 1024}
	defer transport.CloseIdleConnections()
	if req.Xray != nil {
		dial, closeClient, err := newXrayDialer(req.Xray)
		if err != nil {
			return 3
		}
		defer closeClient()
		transport.DialContext = dial
	} else if req.ProxyURL != "" {
		proxyURL, err := url.Parse(req.ProxyURL)
		if err != nil || proxyURL.Host == "" {
			return 2
		}
		switch proxyURL.Scheme {
		case "http", "https", "socks5", "socks5h":
			transport.Proxy = http.ProxyURL(proxyURL)
		default:
			return 2
		}
	} else {
		server, err := net.ResolveUDPAddr("udp", req.Server)
		if err != nil || req.ServerName == "" || req.Password == "" {
			return 3
		}
		config := &client.Config{
			ServerAddr: server,
			Auth:       req.Password,
			TLSConfig:  client.TLSConfig{ServerName: req.ServerName},
		}
		if req.ObfsPassword != "" {
			config.ConnFactory = salamanderConn{password: req.ObfsPassword}
		}
		node, _, err := client.NewClient(config)
		if err != nil {
			return 3
		}
		defer node.Close()
		transport.DialContext = func(_ context.Context, _, address string) (net.Conn, error) {
			return node.TCP(address)
		}
	}
	httpClient := &http.Client{
		Transport: transport,
		Timeout:   time.Duration(req.TimeoutMS) * time.Millisecond,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	response, err := httpClient.Get(target.String())
	if err != nil {
		return 4
	}
	response.Body.Close()
	fmt.Println(response.StatusCode)
	return 0
}

func main() {
	os.Exit(run())
}
