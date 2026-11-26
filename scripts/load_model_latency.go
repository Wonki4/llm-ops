// Load-test a dedicated loopback portal with real, distinct test sessions (Go 1.24+).
// The cookies file must contain one synthetic session per line; it is never logged.
// For a 10k cold-open burst plus three refresh waves, run:
//
//	go run scripts/load_model_latency.go -sessions /path/to/disposable/cookies -upstreams 160
//
// For ten independently scheduled live-only waves with evenly phased users, add:
//
//	-open-history=false -rounds 9 -spread 15s
//
// A page cold start (add -detail -rounds 0) starts summary and profile, mounts the
// graphs after a valid summary and fetches the model list for group-granted users.
// The fixture should expose one visible model backed by one Deployment. Validation
// checks all three live histogram values and at least 55 complete, owner-confirmed
// points in the 30-minute history. Detail mode also checks each ordinary user's
// profile ID (default GRAPH-00000 onward, in cookie-file order).
// A local TLS HTTP/2 ingress accepts the burst without consuming one TCP port per request.
// All timings start at the scheduled release, including generator and ingress queuing.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type result struct {
	wave                       string
	latency, arrival, dispatch float64
	status                     int
	valid                      bool
	err                        string
	needsGroups                bool
}

// Validate both the HTTP response and the data that the chart or header consumes.
func validateResponse(body []byte, kind, model, deployment, expectedUser string) (valid, needsGroups bool) {
	switch kind {
	case "me":
		var v struct {
			User   string `json:"user_id"`
			Role   string
			Models []string
		}
		ok := json.Unmarshal(body, &v) == nil && v.User == expectedUser && v.Role == "user"
		needsGroups := slices.ContainsFunc(v.Models, func(id string) bool { return id != "" && id != "no-default-models" }) && !slices.Contains(v.Models, model) && !slices.Contains(v.Models, "all-proxy-models")
		return ok, ok && needsGroups
	case "summary":
		var v struct {
			Name string          `json:"model_name"`
			Info json.RawMessage `json:"litellm_info"`
		}
		return json.Unmarshal(body, &v) == nil && v.Name == model && len(v.Info) > 0 && string(v.Info) != "null", false
	case "models":
		var v struct {
			Models []struct {
				Name string `json:"model_name"`
			}
		}
		if json.Unmarshal(body, &v) == nil {
			for _, entry := range v.Models {
				if entry.Name == model {
					return true, false
				}
			}
		}
		return false, false
	}
	var p payload
	if json.Unmarshal(body, &p) != nil || len(p.Rows) != 1 || p.Rows[0].Deployment != deployment {
		return false, false
	}
	row := p.Rows[0]
	if kind == "history" {
		n := 0
		for _, pt := range row.History {
			if pt.TTFT != nil && pt.E2E != nil && pt.ITL != nil {
				n++
			}
		}
		return n >= 55 && row.Source == "deployment_owner", false
	}
	for _, m := range []string{"ttft", "e2e", "itl"} {
		v := row.Metrics[m]
		if v.Status != "ok" || v.Value == nil || *v.Value < 0 {
			return false, false
		}
	}
	return true, false
}

type payload struct {
	Rows []struct {
		Deployment string `json:"deployment_name"`
		Metrics    map[string]struct {
			Status string
			Value  *float64 `json:"p50_ms"`
		}
		History []struct{ TTFT, E2E, ITL *float64 }
		Source  string `json:"scope_source"`
	}
}

func readSessions(sessions string, n int) []string {
	file, e := os.Open(sessions)
	if e != nil {
		log.Fatal(e)
	}
	var cookies []string
	seen := map[string]bool{}
	s := bufio.NewScanner(file)
	for s.Scan() {
		cookie := s.Text()
		if cookie == "" || seen[cookie] {
			log.Fatal("empty or duplicate session cookie")
		}
		seen[cookie] = true
		cookies = append(cookies, cookie)
		if len(cookies) == n {
			break
		}
	}
	file.Close()
	if s.Err() != nil || len(cookies) != n {
		log.Fatal("insufficient users")
	}
	return cookies
}

// Multiplex virtual users over TLS so a burst of 20,000 simultaneous requests
// is accepted at ingress even on hosts with fewer available ephemeral ports.
func openIngress(u *url.URL, upstreams int, timeout time.Duration, start *time.Time, active, peak *atomic.Int64) (*httptest.Server, []*http.Client) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxConnsPerHost = upstreams
	transport.MaxIdleConnsPerHost = upstreams
	transport.MaxIdleConns = upstreams
	proxy := httputil.NewSingleHostReverseProxy(u)
	proxy.Transport = transport
	proxy.ErrorLog = log.New(io.Discard, "", 0)
	counts := make(chan struct{}, upstreams)

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ping" {
			w.WriteHeader(204)
			return
		}
		if r.ProtoMajor != 2 {
			http.Error(w, "expected h2", 500)
			return
		}
		on := active.Add(1)
		defer active.Add(-1)
		for p := peak.Load(); on > p && !peak.CompareAndSwap(p, on); p = peak.Load() {
		}
		arrival := time.Since(*start).Milliseconds()
		select {
		case counts <- struct{}{}:
		case <-r.Context().Done():
			return
		}
		defer func() { <-counts }()
		w.Header().Set("X-Test-Receipt", strconv.FormatInt(arrival, 10))
		w.Header().Set("X-Test-Dispatch", strconv.FormatInt(time.Since(*start).Milliseconds(), 10))
		proxy.ServeHTTP(w, r)
	})
	srv := httptest.NewUnstartedServer(handler)
	srv.EnableHTTP2 = true
	srv.Config.HTTP2 = &http.HTTP2Config{MaxConcurrentStreams: 5000}
	srv.StartTLS()
	var clients []*http.Client
	for i := 0; i < 24; i++ {
		tr := srv.Client().Transport.(*http.Transport).Clone()
		tr.MaxConnsPerHost = 1
		tr.ForceAttemptHTTP2 = true
		c := &http.Client{Transport: tr, Timeout: timeout, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
		resp, err := c.Get(srv.URL + "/ping")
		if err != nil || resp.ProtoMajor != 2 {
			log.Fatal("HTTP/2 setup failed")
		}
		resp.Body.Close()
		clients = append(clients, c)
	}
	return srv, clients
}

func percentiles(samples []float64) []float64 {
	if len(samples) == 0 {
		return nil
	}
	sort.Float64s(samples)
	// Return the empirical nearest-rank quantiles and maximum, in milliseconds.
	q := func(f float64) float64 { return math.Round(samples[int(math.Ceil(f*float64(len(samples))))-1]) }
	return []float64{q(.5), q(.95), q(.99), q(1)}
}

func summarizeWave(g []result) (int, map[string]any) {
	var lat, ar, di, downstream []float64
	valid := 0
	under15 := 0
	errs := map[string]int{}
	status := map[int]int{}
	for _, x := range g {
		lat = append(lat, x.latency)
		if x.arrival >= 0 {
			ar = append(ar, x.arrival)
		}
		if x.dispatch >= 0 {
			di = append(di, x.dispatch)
			downstream = append(downstream, x.latency-x.dispatch)
		}
		if x.valid {
			valid++
			if x.latency <= 15000 {
				under15++
			}
		} else {
			errs[x.err]++
		}
		status[x.status]++
	}
	return valid, map[string]any{
		"total": len(g), "valid": valid, "valid_within_15s": under15, "http": status, "errors": errs,
		"end_to_end_ms_p50_p95_p99_max": percentiles(lat), "ingress_arrival_ms_p50_p95_p99_max": percentiles(ar),
		"upstream_dispatch_ms_p50_p95_p99_max": percentiles(di), "post_dispatch_ms_p50_p95_p99_max": percentiles(downstream),
	}
}

func main() {
	sessions := flag.String("sessions", "", "file containing one synthetic local session cookie per user")
	n := flag.Int("users", 10000, "virtual users")
	rounds := flag.Int("rounds", 3, "15-second live refresh rounds")
	capn := flag.Int("upstreams", 128, "maximum connections from local ingress to the Next server")
	endpoint := flag.String("portal", "http://127.0.0.1:18832", "dedicated local Next server")
	timeout := flag.Duration("timeout", 180*time.Second, "client deadline after dispatch")
	model := flag.String("model", "load-chat", "visible test model name")
	deployment := flag.String("deployment", "chat", "expected Deployment")
	openHistory := flag.Bool("open-history", true, "include history for each first-time viewer")
	detail := flag.Bool("detail", false, "exercise the first visit to the model performance tab, including its header")
	userPrefix := flag.String("user-prefix", "GRAPH-", "/me test user ID prefix, followed by the zero-padded five-digit cookie index")
	spread := flag.Duration("spread", 0, "distribute viewers within each 15-second period")
	flag.Parse()
	u, e := url.Parse(*endpoint)
	if e != nil || u.Hostname() != "127.0.0.1" || u.Scheme != "http" || u.Path != "" || u.RawQuery != "" || u.User != nil || u.Fragment != "" || u.Port() == "" {
		log.Fatal("supply the origin of a dedicated loopback portal")
	}
	if *n < 1 || *n > 10000 || *rounds < 0 || *rounds > 9 || *capn < 1 || *capn > 400 || *spread < 0 || *spread > 15*time.Second || *model == "" || *deployment == "" {
		log.Fatal("invalid local test configuration")
	}
	cookies := readSessions(*sessions, *n)
	var active, peak atomic.Int64
	start := time.Now()
	srv, clients := openIngress(u, *capn, *timeout, &start, &active, &peak)
	defer srv.Close()
	start = time.Now().Add(1 * time.Second)
	target := "/api/proxy/models/" + url.PathEscape(*model) + "/live-latency"
	out := make(chan result, 1024)
	var callers sync.WaitGroup
	send := func(user, round int, kind string) result {
		offset := time.Duration(round)*15*time.Second + *spread*time.Duration(user)/time.Duration(*n)
		sched := start.Add(offset)
		time.Sleep(time.Until(sched))
		wave := fmt.Sprintf("refresh_%d", round)
		path := target
		if round == 0 && *openHistory {
			wave = "open_live"
		}
		switch kind {
		case "history":
			wave = "open_history"
			path += "/history"
		case "summary":
			wave = "summary"
			path = strings.TrimSuffix(target, "live-latency") + "summary"
		case "me":
			wave = "me"
			path = "/api/proxy/me"
		case "models":
			wave = "models"
			path = "/api/proxy/models"
		}
		x := result{wave: wave, arrival: -1, dispatch: -1}
		request, err := http.NewRequest("GET", srv.URL+path, nil)
		if err != nil {
			log.Fatal("invalid request path")
		}
		request.AddCookie(&http.Cookie{Name: "litellm_session", Value: cookies[user]})
		r, err := clients[(user+round)%len(clients)].Do(request)
		if err == nil {
			x.status = r.StatusCode
			data, err := io.ReadAll(io.LimitReader(r.Body, 4<<20))
			r.Body.Close()
			rawReceipt, er := strconv.ParseInt(r.Header.Get("X-Test-Receipt"), 10, 64)
			if er == nil {
				x.arrival = float64(rawReceipt - offset.Milliseconds())
			}
			td, er := strconv.ParseInt(r.Header.Get("X-Test-Dispatch"), 10, 64)
			if er == nil {
				x.dispatch = float64(td - offset.Milliseconds())
			}
			if err == nil && x.status == 200 {
				x.valid, x.needsGroups = validateResponse(data, kind, *model, *deployment, fmt.Sprintf("%s%05d", *userPrefix, user))
			}
			if !x.valid {
				if r.StatusCode != 200 {
					x.err = http.StatusText(r.StatusCode)
				} else {
					x.err = "invalid data"
				}
			}
		} else {
			x.err = fmt.Sprintf("%T", err)
		}
		x.latency = float64(time.Since(sched).Microseconds()) / 1000
		out <- x
		return x
	}
	// Summary controls mounting the charts. The personal grant in /me determines whether
	// the header needs the catalog list; its request can overlap the charts.
	detailSession := func(user int) {
		defer callers.Done()
		profile := make(chan result, 1)
		go func() { profile <- send(user, 0, "me") }()
		summary := send(user, 0, "summary")
		var chartDone sync.WaitGroup
		var live, history result
		if summary.valid {
			chartDone.Add(2)
			go func() { defer chartDone.Done(); live = send(user, 0, "live") }()
			go func() { defer chartDone.Done(); history = send(user, 0, "history") }()
		}
		me := <-profile
		if me.needsGroups && summary.valid {
			send(user, 0, "models")
		}
		chartDone.Wait()
		// Both graph responses are timed from the user's first navigation, even if /me
		// or the header list finishes later. This is a client observation, not an HTTP call.
		out <- result{wave: "graph_ready", needsGroups: me.needsGroups, valid: summary.valid && live.valid && history.valid,
			latency: math.Max(live.latency, history.latency), arrival: -1, dispatch: -1}
	}
	if *detail {
		if *rounds != 0 || !*openHistory || *spread != 0 {
			log.Fatal("detail mode requires -rounds 0 -open-history=true -spread=0")
		}
		for user := 0; user < *n; user++ {
			callers.Add(1)
			go detailSession(user)
		}
	} else {
		for round := 0; round <= *rounds; round++ {
			for user := 0; user < *n; user++ {
				callers.Add(1)
				go func() { defer callers.Done(); send(user, round, "live") }()
				if round == 0 && *openHistory {
					callers.Add(1)
					go func() { defer callers.Done(); send(user, 0, "history") }()
				}
			}
		}
	}
	go func() { callers.Wait(); close(out) }()
	var done atomic.Int64
	quit := make(chan struct{})
	go func() {
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				fmt.Fprintf(os.Stderr, "progress %.0fs done=%d ingress_active=%d max=%d\n", time.Since(start).Seconds(), done.Load(), active.Load(), peak.Load())
			case <-quit:
				return
			}
		}
	}()
	groups := map[string][]result{}
	for x := range out {
		groups[x.wave] = append(groups[x.wave], x)
		done.Add(1)
	}
	close(quit)
	keys := []string{"refresh_0"}
	if *openHistory {
		keys = []string{"open_live", "open_history"}
	}
	for i := 1; i <= *rounds; i++ {
		keys = append(keys, fmt.Sprintf("refresh_%d", i))
	}
	if *detail {
		keys = []string{"me", "summary", "open_live", "open_history", "graph_ready"}
		if len(groups["models"]) > 0 {
			keys = append(keys, "models")
		}
	}
	report := map[string]any{"detail": *detail, "users": *n, "open_history": *openHistory, "spread_ms": spread.Milliseconds(), "upstream_concurrency": *capn, "peak_ingress_requests": peak.Load(), "elapsed_s": time.Since(start).Seconds()}
	waves := map[string]any{}
	pass := true
	for _, k := range keys {
		g := groups[k]
		valid, stats := summarizeWave(g)
		waves[k] = stats
		expected := *n
		if k == "models" {
			expected = len(g)
		}
		if valid != expected {
			pass = false
		}
	}
	if *detail {
		const label = "graph_ready"
		var normal, group []float64
		for _, x := range groups[label] {
			if !x.valid {
				continue
			}
			if x.needsGroups {
				group = append(group, x.latency)
			} else {
				normal = append(normal, x.latency)
			}
		}
		report["graph_ready_ms_p50_p95_p99_max_by_personal_scope"] = map[string]any{"requires_group_list": percentiles(group), "no_group_list": percentiles(normal)}
	}
	report["waves"] = waves
	report["all_valid"] = pass
	buf, _ := json.MarshalIndent(report, "", "  ")
	fmt.Println(string(buf))
	if !pass {
		os.Exit(1)
	}
}
