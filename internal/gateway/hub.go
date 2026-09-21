// Package gateway is the single local entrypoint for the CLI and browser preview.
//
// ADR-002: it is the only network-exposed surface. It coordinates the Python agent
// and render services and the in-process scene store, and fans scene updates out to
// connected browsers over WebSocket.
package gateway

import (
	"encoding/json"
	"log/slog"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// Event types pushed to the browser preview.
const (
	EventSceneUpdated     = "scene.updated"
	EventExportComplete   = "export.complete"
	EventValidationFailed = "validation.failed"
	EventSessionEnded     = "session.ended"
	EventConnected        = "connected"
)

// Event is one message on the preview socket.
type Event struct {
	Type      string          `json:"type"`
	SessionID string          `json:"session_id,omitempty"`
	Payload   json.RawMessage `json:"payload,omitempty"`
	Timestamp string          `json:"timestamp"`
}

const (
	writeWait      = 10 * time.Second
	pongWait       = 60 * time.Second
	pingPeriod     = (pongWait * 9) / 10
	maxMessageSize = 1 << 20
	sendBuffer     = 32
)

// client is one browser connection.
type client struct {
	hub       *Hub
	conn      *websocket.Conn
	sessionID string
	send      chan []byte
	closeOnce sync.Once
}

// Hub fans events out to every client watching a session.
//
// A mutex-guarded map is used rather than the usual channel-based select loop: this
// is a single-user local tool with at most a handful of browser tabs, and the direct
// approach is far easier to reason about than an event loop.
type Hub struct {
	mu      sync.RWMutex
	clients map[string]map[*client]struct{} // sessionID -> set
	log     *slog.Logger
}

// NewHub creates an empty hub.
func NewHub(log *slog.Logger) *Hub {
	if log == nil {
		log = slog.Default()
	}
	return &Hub{clients: make(map[string]map[*client]struct{}), log: log}
}

func (h *Hub) add(c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.clients[c.sessionID] == nil {
		h.clients[c.sessionID] = make(map[*client]struct{})
	}
	h.clients[c.sessionID][c] = struct{}{}
	h.log.Debug("preview client connected", "session", c.sessionID,
		"clients", len(h.clients[c.sessionID]))
}

func (h *Hub) remove(c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if set, ok := h.clients[c.sessionID]; ok {
		if _, present := set[c]; present {
			delete(set, c)
			c.closeOnce.Do(func() { close(c.send) })
		}
		if len(set) == 0 {
			delete(h.clients, c.sessionID)
		}
	}
}

// Broadcast delivers an event to every client watching a session.
//
// Slow clients are dropped rather than allowed to block the sender: a stalled browser
// tab must never stall the CLI's turn, and the client will resync on reconnect.
func (h *Hub) Broadcast(sessionID, eventType string, payload any) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		h.log.Error("encode event payload", "error", err, "type", eventType)
		return
	}
	event := Event{
		Type:      eventType,
		SessionID: sessionID,
		Payload:   encoded,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
	message, err := json.Marshal(event)
	if err != nil {
		h.log.Error("encode event", "error", err)
		return
	}

	h.mu.RLock()
	targets := make([]*client, 0, len(h.clients[sessionID]))
	for c := range h.clients[sessionID] {
		targets = append(targets, c)
	}
	h.mu.RUnlock()

	for _, c := range targets {
		select {
		case c.send <- message:
		default:
			h.log.Warn("dropping slow preview client", "session", sessionID)
			h.remove(c)
		}
	}
}

// ClientCount reports how many browsers are watching a session.
func (h *Hub) ClientCount(sessionID string) int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients[sessionID])
}

// TotalClients reports all connected clients, for the health endpoint.
func (h *Hub) TotalClients() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	total := 0
	for _, set := range h.clients {
		total += len(set)
	}
	return total
}

// CloseSession disconnects every client watching a session.
func (h *Hub) CloseSession(sessionID string) {
	h.Broadcast(sessionID, EventSessionEnded, map[string]string{"session_id": sessionID})

	h.mu.Lock()
	set := h.clients[sessionID]
	delete(h.clients, sessionID)
	h.mu.Unlock()

	for c := range set {
		c.closeOnce.Do(func() { close(c.send) })
	}
}

// readPump drains inbound frames so the connection stays healthy and pongs are
// processed. The browser is a consumer only; anything it sends is ignored.
func (c *client) readPump() {
	defer func() {
		c.hub.remove(c)
		c.conn.Close()
	}()

	c.conn.SetReadLimit(maxMessageSize)
	_ = c.conn.SetReadDeadline(time.Now().Add(pongWait))
	c.conn.SetPongHandler(func(string) error {
		return c.conn.SetReadDeadline(time.Now().Add(pongWait))
	})

	for {
		if _, _, err := c.conn.ReadMessage(); err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway,
				websocket.CloseNormalClosure) {
				c.hub.log.Debug("preview socket closed unexpectedly", "error", err)
			}
			return
		}
	}
}

// writePump serialises writes and sends periodic pings, since concurrent writes to a
// websocket.Conn are not safe.
func (c *client) writePump() {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		c.conn.Close()
	}()

	for {
		select {
		case message, ok := <-c.send:
			_ = c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				_ = c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, message); err != nil {
				return
			}
		case <-ticker.C:
			_ = c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
