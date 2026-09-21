// Command isoforged is the IsoForge Go daemon.
//
// Per the ratified plan it merges the spec's iso_gateway and scene_store into one
// process: the gateway calls the store through an in-process interface rather than an
// HTTP hop, while every route from the spec is preserved so the two can be split
// apart later without changing a single contract.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/coopdloop/isoforge/internal/gateway"
	"github.com/coopdloop/isoforge/internal/store"
	"github.com/coopdloop/isoforge/web"
)

func main() {
	var (
		port      = flag.Int("port", envInt("ISO_GATEWAY_PORT", 4747), "listen port")
		host      = flag.String("host", envOr("ISO_GATEWAY_HOST", "127.0.0.1"), "listen host")
		agentURL  = flag.String("agent-url", envOr("AGENT_SERVICE_URL", "http://127.0.0.1:5001"), "agent service base URL")
		renderURL = flag.String("render-url", envOr("RENDER_SERVICE_URL", "http://127.0.0.1:5002"), "render service base URL")
		dbPath    = flag.String("db", envOr("SQLITE_DB_PATH", "./data/isoforge.db"), "SQLite database path")
		scenesDir = flag.String("scenes", envOr("SCENE_DATA_DIR", "./data/scenes"), "scene JSON directory")
		logLevel  = flag.String("log-level", envOr("LOG_LEVEL", "info"), "debug, info, warn or error")
		noWeb     = flag.Bool("no-web", false, "disable serving the embedded web UI")
		printPort = flag.Bool("print-port", false, "print the bound port as JSON on startup")
	)
	flag.Parse()

	log := newLogger(*logLevel)

	db, err := store.Open(store.Options{DBPath: *dbPath, ScenesDir: *scenesDir})
	if err != nil {
		log.Error("could not open store", "error", err)
		os.Exit(1)
	}
	defer db.Close()

	var webFS fs.FS
	if !*noWeb {
		if bundled, err := web.FS(); err == nil {
			webFS = bundled
		} else {
			log.Debug("no embedded web bundle in this build", "reason", err)
		}
	}

	server := gateway.NewServer(gateway.Config{
		Store:   db,
		Backend: gateway.NewBackend(*agentURL, *renderURL),
		Logger:  log,
		WebFS:   webFS,
	})

	// Bind before serving so that port 0 can be resolved and reported; the CLI uses
	// this to pick a free port and still know where to point the browser.
	addr := fmt.Sprintf("%s:%d", *host, *port)
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		log.Error("could not bind", "addr", addr, "error", err)
		os.Exit(1)
	}
	bound := listener.Addr().(*net.TCPAddr)

	httpServer := &http.Server{
		Handler:           server.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		// No write timeout: WebSocket connections are long-lived by design.
	}

	if *printPort {
		fmt.Printf(`{"port":%d,"host":%q,"url":"http://%s:%d"}`+"\n",
			bound.Port, *host, *host, bound.Port)
		os.Stdout.Sync()
	}
	log.Info("isoforged listening",
		"url", fmt.Sprintf("http://%s:%d", *host, bound.Port),
		"db", *dbPath, "scenes", *scenesDir, "web", webFS != nil)

	errCh := make(chan error, 1)
	go func() {
		if err := httpServer.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	select {
	case err := <-errCh:
		log.Error("server failed", "error", err)
		os.Exit(1)
	case sig := <-stop:
		log.Info("shutting down", "signal", sig.String())
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	if err := httpServer.Shutdown(ctx); err != nil {
		log.Warn("graceful shutdown incomplete", "error", err)
	}
}

func newLogger(level string) *slog.Logger {
	var lvl slog.Level
	switch level {
	case "debug":
		lvl = slog.LevelDebug
	case "warn":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: lvl}))
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		var out int
		if _, err := fmt.Sscanf(v, "%d", &out); err == nil {
			return out
		}
	}
	return fallback
}
