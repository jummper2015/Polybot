# src/strategies/mean_reversion/config.py

from dataclasses import dataclass, field


@dataclass
class MeanReversionConfig:
    """
    Parámetros configurables de la estrategia Mean Reversion.
    Compra en sobreventa (z-score < entry_zscore) y vende en retorno a media.
    Defaults seguros para paper trading.
    """

    # ── SMA y Z-Score ─────────────────────────────────────────────────
    ma_window:      int   = 20       # Ticks para calcular SMA y desviación estándar
    entry_zscore:   float = -2.0     # Compra cuando z_score cae por debajo (sobreventa)
    exit_zscore:    float = 0.0      # Vende cuando z_score retorna a media o superior

    # ── Condiciones de salida ─────────────────────────────────────────
    stop_loss_pct:   float = 0.10    # -10% desde entrada → stop loss
    timeout_minutes: float = 45.0    # Máximo minutos en posición (más largo que BAT)

    # ── Filtros ───────────────────────────────────────────────────────
    max_spread:      float = 0.03    # Spread máximo bid-ask (3%)
    min_volume_usdc: float = 1000.0  # Volumen mínimo 24h en USDC
    blocked_hours: list[tuple[int, int]] = field(
        default_factory=lambda: [(0, 6)]  # Bloquea 00:00-06:00 UTC
    )

    # ── Tamaño de posición ────────────────────────────────────────────
    position_size_usdc: float = 10.0  # USDC por operación (RiskEngine puede reducir)

    def validate(self) -> None:
        """
        Valida coherencia de los parámetros.
        Lanza ValueError si algún parámetro es incoherente.
        """
        if self.ma_window < 3:
            raise ValueError(
                f"ma_window debe ser >= 3 ticks para z-score significativo, "
                f"got {self.ma_window}"
            )

        if not -5.0 <= self.entry_zscore < self.exit_zscore:
            raise ValueError(
                f"entry_zscore={self.entry_zscore} debe ser "
                f"< exit_zscore={self.exit_zscore} "
                f"y >= -5.0"
            )

        if self.exit_zscore < -4.0:
            raise ValueError(
                f"exit_zscore={self.exit_zscore} debe ser >= -4.0"
            )

        if self.stop_loss_pct <= 0 or self.stop_loss_pct > 0.50:
            raise ValueError(
                f"stop_loss_pct debe estar entre 0 y 0.50, got {self.stop_loss_pct}"
            )

        if self.timeout_minutes <= 0:
            raise ValueError(
                f"timeout_minutes debe ser > 0, got {self.timeout_minutes}"
            )

        if not 0 < self.max_spread < 1.0:
            raise ValueError(
                f"max_spread debe estar entre 0 y 1.0, got {self.max_spread}"
            )

        if self.min_volume_usdc <= 0:
            raise ValueError(
                f"min_volume_usdc debe ser > 0, got {self.min_volume_usdc}"
            )

        if self.position_size_usdc <= 0:
            raise ValueError(
                f"position_size_usdc debe ser > 0, got {self.position_size_usdc}"
            )
