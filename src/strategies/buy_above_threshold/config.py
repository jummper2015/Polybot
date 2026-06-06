# src/strategies/buy_above_threshold/config.py

from dataclasses import dataclass, field


@dataclass
class BuyAboveThresholdConfig:
    """
    Todos los parámetros configurables de la estrategia.
    Defaults seguros para paper trading.
    Puede sobreescribirse desde .env o desde Telegram /settings.
    """

    # ── Condición de entrada ──────────────────────────────────────────
    threshold:          float = 0.55    # Precio YES mínimo para considerar entrada
    required_ticks:     int   = 1       # Ticks consecutivos confirmando threshold

    # ── Filtros ───────────────────────────────────────────────────────
    max_spread:         float = 0.03    # Spread máximo bid-ask (3%)
    min_volume_pusd:    float = 1000.0  # Volumen mínimo 24h en pUSD
    blocked_hours: list[tuple[int, int]] = field(
        default_factory=lambda: [(0, 6)]  # Bloquea 00:00-06:00 UTC
    )

    # ── Condiciones de salida ─────────────────────────────────────────
    stop_loss_pct:      float = 0.15    # -15% desde entrada → stop loss
    stop_drop_floor:    float = 0.40    # Precio absoluto mínimo → salir siempre
    timeout_minutes:    float = 30.0    # Máximo minutos en posición
    target_price:       float = 0.75    # Precio objetivo → tomar ganancias

    # ── Hedge ─────────────────────────────────────────────────────────
    hedge_drop_pct:     float = 0.20    # Caída >20% en 2 ticks → evaluar hedge
    hedge_enabled:      bool  = True    # Si False, nunca genera señal de hedge

    # ── Tamaño de posición ────────────────────────────────────────────
    position_size_pusd: float = 10.0    # pUSD por operación (RiskEngine puede reducir)

    # ── Regime Awareness (P11.1) ───────────────────────────────────────
    # BAT performs best in trending markets with clear direction.
    # Also enabled in CHOP for testing with looser threshold (0.55).
    allowed_regimes: list[str] = field(
        default_factory=lambda: ["trend", "chop"]
    )

    def validate(self) -> None:
        """
        Valida coherencia de los parámetros.
        Lanza ValueError si algún parámetro es incoherente.
        """
        if not 0.0 < self.threshold < 1.0:
            raise ValueError(f"threshold debe estar entre 0 y 1, got {self.threshold}")

        if not 0.0 < self.stop_drop_floor < self.threshold:
            raise ValueError(
                f"stop_drop_floor={self.stop_drop_floor} debe ser < threshold={self.threshold}"
            )

        if not self.threshold < self.target_price <= 1.0:
            raise ValueError(
                f"target_price={self.target_price} debe ser > threshold={self.threshold}"
            )

        if self.stop_loss_pct <= 0:
            raise ValueError(f"stop_loss_pct debe ser > 0, got {self.stop_loss_pct}")

        if self.timeout_minutes <= 0:
            raise ValueError(f"timeout_minutes debe ser > 0, got {self.timeout_minutes}")

        if self.required_ticks < 1:
            raise ValueError(f"required_ticks debe ser >= 1, got {self.required_ticks}")
