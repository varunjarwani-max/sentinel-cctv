"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert, Ban, Search } from "lucide-react";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

const MAX_ALERTS = 100;
const RECONNECT_DELAY_MS = 3000;

export interface AlertRecord {
  alert_id: string;
  camera_id: string;
  camera_name: string;
  track_id: number;
  plate_text: string;
  flag_type: string;
  confidence: number;
  alerted_at: string;
  lat: number;
  lng: number;
}

interface AlertFeedProps {
  onSelectPlate?: (plate: string) => void;
}

function formatRelativeTime(isoString: string): string {
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return isoString;

  const now = Date.now();
  const diffMs = now - then;
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));

  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;

  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;

  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;

  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function flagBadgeStyle(flagType: string): { bg: string; text: string; icon: React.ReactNode } {
  const normalized = flagType.toUpperCase();
  switch (normalized) {
    case "STOLEN":
    case "WANTED":
      return {
        bg: "bg-noc-critical/15",
        text: "text-noc-critical",
        icon: <ShieldAlert size={12} />,
      };
    case "SUSPECT":
    case "MISSING":
      return {
        bg: "bg-noc-warning/15",
        text: "text-noc-warning",
        icon: <AlertTriangle size={12} />,
      };
    case "BLACKLISTED":
      return {
        bg: "bg-noc-accent/15",
        text: "text-noc-accent",
        icon: <Ban size={12} />,
      };
    default:
      return {
        bg: "bg-gray-500/15",
        text: "text-gray-300",
        icon: <AlertTriangle size={12} />,
      };
  }
}

export default function AlertFeed({ onSelectPlate }: AlertFeedProps) {
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [connected, setConnected] = useState<boolean>(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef<boolean>(true);
  const [, forceTick] = useState<number>(0);

  useEffect(() => {
    mountedRef.current = true;

    const connect = () => {
      if (!mountedRef.current) return;

      let socket: WebSocket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        setConnected(false);
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
        return;
      }

      wsRef.current = socket;

      socket.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data) as Partial<AlertRecord> & {
            type?: string;
          };
          if (!data.alert_id || !data.camera_id) return;

          const record: AlertRecord = {
            alert_id: data.alert_id,
            camera_id: data.camera_id,
            camera_name: data.camera_name ?? data.camera_id,
            track_id: data.track_id ?? -1,
            plate_text: data.plate_text ?? "UNKNOWN",
            flag_type: data.flag_type ?? "UNKNOWN",
            confidence: data.confidence ?? 0,
            alerted_at: data.alerted_at ?? new Date().toISOString(),
            lat: data.lat ?? 0,
            lng: data.lng ?? 0,
          };

          setAlerts((prev) => {
            const next = [record, ...prev];
            return next.slice(0, MAX_ALERTS);
          });
        } catch (err) {
          console.error("AlertFeed: failed to parse websocket message", err);
        }
      };

      socket.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      socket.onerror = () => {
        socket.close();
      };
    };

    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }
    };
  }, []);

  // Periodically re-render so relative timestamps ("Xs ago") stay fresh.
  useEffect(() => {
    const interval = setInterval(() => {
      forceTick((t) => t + 1);
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex h-full w-full flex-col border-l border-noc-border bg-noc-panel">
      <div className="flex items-center justify-between border-b border-noc-border px-4 py-3">
        <div className="flex items-center gap-2">
          <ShieldAlert size={16} className="text-noc-critical" />
          <h2 className="text-sm font-semibold text-gray-100">Live Alerts</h2>
        </div>
        <span
          className={`h-2 w-2 rounded-full ${
            connected ? "bg-noc-online" : "bg-noc-critical"
          }`}
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {alerts.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-gray-600">
            <ShieldAlert size={24} />
            <p className="text-xs">Awaiting watchlist matches...</p>
          </div>
        ) : (
          <ul className="divide-y divide-noc-border">
            {alerts.map((alert) => {
              const badge = flagBadgeStyle(alert.flag_type);
              return (
                <li key={alert.alert_id}>
                  <button
                    type="button"
                    onClick={() => onSelectPlate?.(alert.plate_text)}
                    className="flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors hover:bg-white/5"
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badge.bg} ${badge.text}`}
                      >
                        {badge.icon}
                        {alert.flag_type}
                      </span>
                      <span className="text-[10px] text-gray-500">
                        {formatRelativeTime(alert.alerted_at)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="plate-mono rounded bg-black/40 px-2 py-0.5 text-sm font-semibold text-gray-100">
                        {alert.plate_text}
                      </span>
                      <span className="flex items-center gap-1 text-xs text-gray-400">
                        <Search size={11} />
                        {(alert.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs text-gray-500">
                      <span className="truncate">{alert.camera_name}</span>
                      <span className="plate-mono text-[10px] text-gray-600">
                        TRK-{alert.track_id}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
