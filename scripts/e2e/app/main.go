// A stand-in for an app like BalanceVid, used by scripts/e2e/run.sh: a web
// process that accepts recordings into /data, a worker that "renders" them
// slowly and finishes the render in hand when it gets SIGTERM, and a one-shot
// job mode for cron.
package main

import (
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
)

var version = "dev"

func logf(format string, args ...any) {
	line := fmt.Sprintf("%s %s ", time.Now().UTC().Format("15:04:05"), version) + fmt.Sprintf(format, args...)
	fmt.Println(line)
	if f, err := os.OpenFile("/data/events.log", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		fmt.Fprintln(f, line)
		f.Close()
	}
}

func web() {
	port := os.Getenv("PORT")
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		entries, _ := os.ReadDir("/data")
		names := []string{}
		for _, e := range entries {
			names = append(names, e.Name())
		}
		sort.Strings(names)
		fmt.Fprintf(w, "version=%s deployment=%s files=%s\n", version, os.Getenv("FORGE_DEPLOYMENT"), strings.Join(names, ","))
	})
	http.HandleFunc("/upload", func(w http.ResponseWriter, r *http.Request) {
		name := filepath.Base(r.URL.Query().Get("name"))
		if err := os.WriteFile(filepath.Join("/data", name+".raw"), []byte("frames"), 0o644); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		logf("web accepted %s", name)
		fmt.Fprintln(w, "ok")
	})
	http.HandleFunc("/events", func(w http.ResponseWriter, r *http.Request) {
		b, _ := os.ReadFile("/data/events.log")
		w.Write(b)
	})
	logf("web listening on %s", port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
}

func worker() {
	seconds, _ := time.ParseDuration(os.Getenv("RENDER_TIME"))
	if seconds == 0 {
		seconds = 20 * time.Second
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	logf("worker started")
	for {
		select {
		case <-stop:
			logf("worker got SIGTERM while idle, exiting")
			return
		default:
		}
		raws, _ := filepath.Glob("/data/*.raw")
		if len(raws) == 0 {
			time.Sleep(500 * time.Millisecond)
			continue
		}
		raw := raws[0]
		claimed := raw + ".rendering"
		if os.Rename(raw, claimed) != nil {
			continue // the other worker took it
		}
		name := strings.TrimSuffix(filepath.Base(raw), ".raw")
		logf("worker rendering %s", name)
		deadline := time.After(seconds)
		stopping := false
	render:
		for {
			select {
			case <-deadline:
				break render
			case <-stop:
				stopping = true
				logf("worker got SIGTERM mid-render of %s, finishing it first", name)
			}
		}
		os.WriteFile(filepath.Join("/data", name+".mp4"), []byte("video"), 0o644)
		os.Remove(claimed)
		logf("worker finished %s", name)
		if stopping {
			logf("worker exiting after finishing its render")
			return
		}
	}
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "job" {
		entries, _ := os.ReadDir("/data")
		logf("job ran and saw %d files", len(entries))
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "worker" {
		worker()
		return
	}
	web()
}
