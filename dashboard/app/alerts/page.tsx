"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Circle } from "lucide-react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AlertRow {
  alert_id: string;
  detection_id: string | null;
  watchlist_id: string | null;
  camera_id: string;
  camera_name: string | null;
  track_id: number | null;
  plate_text: string | null;
  flag_type: string;
  confidence: number;
  alerted_at: string;
  acknowledged: boolean;
}

function flagColor(flagType: string): string {
  const normalized = flagType.toUpperCase();
  if (normalized === "STOLEN" || normalized === "WANTED") {
    return "text-noc-critical";
  }
  if (normalized === "SUSPECT" || normalized === "MISSING") {
    return "text-noc-warning";
  }
  if (normalized === "BLACKLISTED") {
    return "text-noc-accent";
  }
  return "text-gray-300";
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_URL}/alerts`);
        if (!res.ok) throw new Error(`Failed to fetch alerts: ${res.status}`);
        const data: AlertRow[] = await res.json();
        if (!cancelled) setAlerts(data);
      } catch (err) {
        console.error("AlertsPage: fetch failed", err);
        if (!cancelled) setError("Unable to load alerts.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const toggleAcknowledged = async (alertId: string) => {
    setAlerts((prev) =>
      prev.map((a) =>
        a.alert_id === alertId ? { ...a, acknowledged: !a.acknowledged } : a
      )
    );

    // Best-effort sync with the backend. The core API contract for this
    // hackathon build does not guarantee an acknowledgement endpoint, so
    // failures here are swallowed and the UI stays optimistic.
    try {
      await fetch(`${API_URL}/alerts/${alertId}/ack`, { method: "PATCH" });
    } catch {
      /* backend acknowledgement endpoint may be unavailable; ignore */
    }
  };

  return (
    <div className="flex h-full w-full flex-col p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-100">Alert History</h1>
        <span className="text-xs text-gray-500">
          {alerts.length} record{alerts.length === 1 ? "" : "s"}
        </span>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading alerts...</p>}
      {error && <p className="text-sm text-noc-critical">{error}</p>}

      {!loading && !error && (
        <div className="overflow-hidden rounded-lg border border-noc-border">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-noc-border bg-noc-panel text-xs uppercase tracking-wide text-gray-500">
                <th className="px-4 py-3 font-medium">Plate</th>
                <th className="px-4 py-3 font-medium">Class</th>
                <th className="px-4 py-3 font-medium">Timestamp</th>
                <th className="px-4 py-3 font-medium">Camera</th>
                <th className="px-4 py-3 font-medium">Confidence</th>
                <th className="px-4 py-3 font-medium">Acknowledged</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <tr
                  key={alert.alert_id}
                  className="border-b border-noc-border bg-noc-bg/40 last:border-b-0 hover:bg-white/5"
                >
                  <td className="plate-mono px-4 py-3 text-gray-100">
                    {alert.plate_text ?? "UNKNOWN"}
                  </td>
                  <td className={`px-4 py-3 text-xs font-semibold uppercase ${flagColor(alert.flag_type)}`}>
                    {alert.flag_type}
                  </td>
                  <td className="plate-mono px-4 py-3 text-gray-400">
                    {new Date(alert.alerted_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-gray-300">
                    {alert.camera_name ?? alert.camera_id}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {(alert.confidence * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => toggleAcknowledged(alert.alert_id)}
                      className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                        alert.acknowledged
                          ? "bg-noc-online/10 text-noc-online"
                          : "bg-gray-500/10 text-gray-400 hover:bg-gray-500/20"
                      }`}
                    >
                      {alert.acknowledged ? (
                        <CheckCircle2 size={13} />
                      ) : (
                        <Circle size={13} />
                      )}
                      {alert.acknowledged ? "Acknowledged" : "Pending"}
                    </button>
                  </td>
                </tr>
              ))}
              {alerts.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-6 text-center text-xs text-gray-600"
                  >
                    No alerts recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
