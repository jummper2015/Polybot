# tests/performance/locustfile.py
"""
Locust performance tests para PolyBot API.

Endpoints probados (ratio de peso según PLAN_MEJORAS.txt P3.4):
  - GET /api/v1/health     — weight 3  (health check, tráfico principal)
  - GET /api/v1/positions  — weight 2  (consulta de posiciones)
  - GET /api/v1/metrics    — weight 1  (scrape de Prometheus)
  - GET /api/v1/markets    — weight 1  (consulta de mercados)

Objetivos de latencia:
  - health:    p95 < 50ms
  - positions: p95 < 200ms
  - metrics:   p95 < 100ms
  - markets:   p95 < 200ms

Uso:
  # Web UI
  locust -f tests/performance/locustfile.py --host http://localhost:8000

  # Headless (CI/CD)
  locust -f tests/performance/locustfile.py --host http://localhost:8000 \\
      --headless --users 50 --spawn-rate 5 --run-time 60s \\
      --csv=reports/locust_results

  # Validación de sintaxis (sin server)
  locust -f tests/performance/locustfile.py --host http://localhost:8000 \\
      --headless --users 1 --spawn-rate 1 --run-time 0s
"""

from locust import HttpUser, between, events, task

# ── Ratios de peso según PLAN_MEJORAS.txt ────────────────────────────────
# health:3  positions:2  metrics:1  markets:1  →  total=7

class PolyBotUser(HttpUser):
    """
    Usuario simulado que ejecuta requests contra la API del bot.
    Cada usuario espera entre 1 y 5 segundos entre requests
    (simulando tráfico humano/automático mixto).
    """
    wait_time = between(1, 5)

    def on_start(self):
        """Setup que se ejecuta una vez por usuario al inicio."""
        self.client.headers.update({
            "Accept": "application/json",
            "User-Agent": "Locust-PolyBot-PerformanceTest/1.0",
        })

    # ── Health Check (weight 3) ───────────────────────────────────────

    @task(3)
    def health_check(self):
        """
        GET /api/v1/health
        Objetivo: p95 < 50ms
        Endpoint más frecuente — usado por Docker healthcheck, LB, monitoreo.
        """
        with self.client.get(
            "/api/v1/health",
            catch_response=True,
            name="GET /api/v1/health",
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get("status") == "ok":
                        response.success()
                    else:
                        # Degradado o down — es éxito HTTP pero no funcional
                        response.failure(
                            f"Health status={data.get('status')}"
                        )
                except Exception:
                    response.failure("Invalid JSON response")
            else:
                response.failure(
                    f"HTTP {response.status_code}"
                )

    # ── Positions (weight 2) ──────────────────────────────────────────

    @task(2)
    def get_positions(self):
        """
        GET /api/v1/positions
        Objetivo: p95 < 200ms
        Consulta frecuente desde dashboard y Telegram /positions.
        """
        with self.client.get(
            "/api/v1/positions",
            catch_response=True,
            name="GET /api/v1/positions",
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "positions" in data:
                        response.success()
                    else:
                        response.failure("Missing 'positions' field")
                except Exception:
                    response.failure("Invalid JSON response")
            else:
                response.failure(
                    f"HTTP {response.status_code}"
                )

    # ── Metrics (weight 1) ────────────────────────────────────────────

    @task(1)
    def get_metrics(self):
        """
        GET /api/v1/metrics
        Objetivo: p95 < 100ms
        Scrapeado por Prometheus cada 15-30s.
        Respuesta text/plain, no JSON.
        """
        with self.client.get(
            "/api/v1/metrics",
            catch_response=True,
            name="GET /api/v1/metrics",
        ) as response:
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                if "text/plain" in content_type:
                    # Verificar que hay métricas relevantes
                    body = response.text
                    if len(body) > 100:
                        response.success()
                    else:
                        response.failure("Empty metrics response")
                else:
                    response.failure(
                        f"Unexpected content-type: {content_type}"
                    )
            else:
                response.failure(
                    f"HTTP {response.status_code}"
                )

    # ── Markets (weight 1) ────────────────────────────────────────────

    @task(1)
    def get_markets(self):
        """
        GET /api/v1/markets
        Objetivo: p95 < 200ms
        Consulta de mercados activos desde dashboard.
        """
        with self.client.get(
            "/api/v1/markets",
            catch_response=True,
            name="GET /api/v1/markets",
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "markets" in data:
                        response.success()
                    else:
                        response.failure("Missing 'markets' field")
                except Exception:
                    response.failure("Invalid JSON response")
            else:
                response.failure(
                    f"HTTP {response.status_code}"
                )


# ── Event Hooks: validación de objetivos de latencia ────────────────────

@events.quitting.add_listener
def check_latency_targets(environment, **kwargs):
    """
    Al finalizar la prueba, verifica que las métricas de latencia
    cumplen los objetivos definidos en PLAN_MEJORAS.txt P3.4.

    Imprime un reporte y devuelve exit code 1 si algún objetivo falla.
    Esto permite usar Locust como test de CI (fail si latencia > objetivo).
    """
    if not hasattr(environment, 'stats') or environment.stats is None:
        return

    targets = {
        "GET /api/v1/health":    50,   # ms p95
        "GET /api/v1/positions": 200,  # ms p95
        "GET /api/v1/metrics":   100,  # ms p95
        "GET /api/v1/markets":   200,  # ms p95
    }

    failures = []

    print("\n" + "=" * 70)
    print("  LATENCY TARGET CHECK (P3.4)")
    print("=" * 70)

    for entry in environment.stats.entries.values():
        name = entry.name
        if name not in targets:
            continue

        p95 = entry.get_response_time_percentile(0.95)
        target = targets[name]
        status = "✅" if p95 <= target else "❌"

        print(
            f"  {status} {name:<30s} "
            f"p95={p95:>8.1f}ms  target={target:>5}ms  "
            f"({entry.num_requests} requests, "
            f"{entry.fail_ratio:.1%} failures)"
        )

        if p95 > target and entry.num_requests > 0:
            failures.append((name, p95, target))

    print("-" * 70)

    if failures:
        print("  ❌ LATENCY TARGETS FAILED:")
        for name, p95, target in failures:
            print(f"     {name}: p95={p95:.1f}ms > target={target}ms")
        print("-" * 70)
        # Exit code 1 → CI falla
        environment.process_exit_code = 1
    else:
        print("  ✅ All latency targets met")
        print("-" * 70)
        environment.process_exit_code = 0
