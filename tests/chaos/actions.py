"""
tests/chaos/actions.py
======================

Chaos actions used by Chaos Toolkit experiment JSON files.

Each action function simulates a specific failure mode. In production
chaos testing, these would actually manipulate network interfaces,
kill processes, or exhaust resources. For this test suite, they
simulate the conditions for unit/integration testing.

Imported by: tests/chaos/experiments/*.json (chaostoolkit format)
"""

from datetime import datetime, timezone
from typing import Any


def disconnect_websocket(
    market_id: str,
    duration_seconds: int = 120,
) -> dict[str, Any]:
    """
    Action: Disconnect the WebSocket for a specific market.
    Simulates a network partition between the bot and Polymarket CLOB API.

    In production chaos testing, this would:
      - Use iptables to block outbound traffic to ws.polymarket.com
      - Or use tc (traffic control) to drop packets on the WS port
      - Or kill the WebSocket process

    Returns a dict with the action result for chaostoolkit.
    """
    start_time = datetime.now(timezone.utc)
    return {
        "action": "disconnect_websocket",
        "market_id": market_id,
        "status": "executed",
        "duration_seconds": duration_seconds,
        "started_at": start_time.isoformat(),
        "message": f"WS connection for {market_id} disconnected "
                   f"(simulated — would block in production)",
    }


def reconnect_websocket(market_id: str) -> dict[str, Any]:
    """
    Rollback: Reconnect the WebSocket for a specific market.
    Restores normal WebSocket connectivity after the chaos experiment.
    """
    return {
        "action": "reconnect_websocket",
        "market_id": market_id,
        "status": "executed",
        "reconnected_at": datetime.now(timezone.utc).isoformat(),
        "message": f"WS connection for {market_id} restored",
    }


def exhaust_db_pool(
    db_connection_string: str = "postgresql://localhost:5432/polybot",
    duration_seconds: int = 60,
) -> dict[str, Any]:
    """
    Action: Exhaust the DB connection pool by opening connections
    and holding them, preventing new queries from acquiring connections.
    """
    return {
        "action": "exhaust_db_pool",
        "db_connection_string": db_connection_string,
        "status": "executed",
        "duration_seconds": duration_seconds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "message": "DB pool exhausted (simulated — would open max connections)",
    }


def release_db_pool() -> dict[str, Any]:
    """
    Rollback: Release all held DB connections to restore the pool.
    """
    return {
        "action": "release_db_pool",
        "status": "executed",
        "released_at": datetime.now(timezone.utc).isoformat(),
        "message": "DB pool connections released — pool restored",
    }


def block_redis(
    duration_seconds: int = 60,
) -> dict[str, Any]:
    """
    Action: Block access to Redis (simulate Redis failure).
    In production, this would use iptables or kill the Redis process.
    """
    return {
        "action": "block_redis",
        "status": "executed",
        "duration_seconds": duration_seconds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "message": "Redis blocked (simulated — would stop Redis or block port)",
    }


def unblock_redis() -> dict[str, Any]:
    """
    Rollback: Restore Redis access.
    """
    return {
        "action": "unblock_redis",
        "status": "executed",
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "message": "Redis access restored",
    }


def add_packet_loss(
    percentage: float = 50.0,
    interface: str = "eth0",
    duration_seconds: int = 60,
) -> dict[str, Any]:
    """
    Action: Add packet loss to outbound traffic to simulate
    unreliable Polymarket API connection.

    In production: tc qdisc add dev eth0 root netem loss 50%
    """
    return {
        "action": "add_packet_loss",
        "percentage": percentage,
        "interface": interface,
        "status": "executed",
        "duration_seconds": duration_seconds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Packet loss {percentage}% applied to {interface} "
                   f"(simulated — would use tc netem)",
    }


def remove_packet_loss(interface: str = "eth0") -> dict[str, Any]:
    """
    Rollback: Remove packet loss from the network interface.
    """
    return {
        "action": "remove_packet_loss",
        "interface": interface,
        "status": "executed",
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Packet loss removed from {interface}",
    }


def add_latency(
    latency_ms: int = 500,
    jitter_ms: int = 100,
    interface: str = "eth0",
    duration_seconds: int = 60,
) -> dict[str, Any]:
    """
    Action: Add artificial latency to outbound traffic to simulate
    slow Polymarket API responses.

    In production: tc qdisc add dev eth0 root netem delay 500ms 100ms
    """
    return {
        "action": "add_latency",
        "latency_ms": latency_ms,
        "jitter_ms": jitter_ms,
        "interface": interface,
        "status": "executed",
        "duration_seconds": duration_seconds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Latency of {latency_ms}ms ± {jitter_ms}ms added "
                   f"to {interface} (simulated — would use tc netem)",
    }


def remove_latency(interface: str = "eth0") -> dict[str, Any]:
    """
    Rollback: Remove artificial latency from the network interface.
    """
    return {
        "action": "remove_latency",
        "interface": interface,
        "status": "executed",
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Latency removed from {interface}",
    }
