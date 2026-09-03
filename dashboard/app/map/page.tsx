"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { LatLngExpression, DivIcon as DivIconType } from "leaflet";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

const GUJARAT_CENTER: [number, number] = [22.3, 72.6];
const GUJARAT_ZOOM = 7;
const PULSE_LIFETIME_MS = 10000;

interface Camera {
  id: string;
  name: string;
  department: string;
  lat: number;
  lng: number;
  hls_url: string | null;
}

interface PulseMarker {
  id: string;
  lat: number;
  lng: number;
  plate_text: string;
  flag_type: string;
  createdAt: number;
}

interface IncomingAlert {
  alert_id: string;
  camera_id: string;
  camera_name: string;
  plate_text: string;
  flag_type: string;
  lat: number;
  lng: number;
}

function GISMapInner() {
  const [Leaflet, setLeaflet] = useState<typeof import("react-leaflet") | null>(
    null
  );
  const [pulseIcon, setPulseIcon] = useState<DivIconType | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [pulses, setPulses] = useState<PulseMarker[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const leafletCore = await import("leaflet");
      const RL = await import("react-leaflet");

      // @ts-expect-error - _getIconUrl is a private Leaflet internal.
      delete leafletCore.Icon.Default.prototype._getIconUrl;
      leafletCore.Icon.Default.mergeOptions({
        iconRetinaUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      const icon = leafletCore.divIcon({
        className: "sentinel-pulse-marker",
        html: "",
        iconSize: [20, 20],
      });

      if (!cancelled) {
        setLeaflet(RL);
        setPulseIcon(icon);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(`${API_URL}/cameras`);
        if (!res.ok) throw new Error(`Failed to fetch cameras: ${res.status}`);
        const data: Camera[] = await res.json();
        if (!cancelled) setCameras(data);
      } catch (err) {
        console.error("GISMap: failed to fetch cameras", err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    const connect = () => {
      if (!mountedRef.current) return;

      let socket: WebSocket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        reconnectTimerRef.current = setTimeout(connect, 3000);
        return;
      }

      wsRef.current = socket;

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data) as Partial<IncomingAlert>;
          if (
            typeof data.lat !== "number" ||
            typeof data.lng !== "number" ||
            !data.alert_id
          ) {
            return;
          }

          const marker: PulseMarker = {
            id: data.alert_id,
            lat: data.lat,
            lng: data.lng,
            plate_text: data.plate_text ?? "UNKNOWN",
            flag_type: data.flag_type ?? "UNKNOWN",
            createdAt: Date.now(),
          };

          setPulses((prev) => [...prev, marker]);

          setTimeout(() => {
            setPulses((prev) => prev.filter((p) => p.id !== marker.id));
          }, PULSE_LIFETIME_MS);
        } catch (err) {
          console.error("GISMap: failed to parse websocket message", err);
        }
      };

      socket.onclose = () => {
        if (!mountedRef.current) return;
        reconnectTimerRef.current = setTimeout(connect, 3000);
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

  if (!Leaflet || !pulseIcon) {
    return (
      <div className="flex h-full w-full items-center justify-center text-sm text-gray-500">
        Loading tactical map...
      </div>
    );
  }

  const { MapContainer, TileLayer, Marker, Popup } = Leaflet;
  const center: LatLngExpression = GUJARAT_CENTER;

  return (
    <MapContainer
      center={center}
      zoom={GUJARAT_ZOOM}
      style={{ height: "100%", width: "100%" }}
    >
      <TileLayer
        url="https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://carto.com/attributions">CARTO</a> contributors'
      />

      {cameras.map((camera) => (
        <Marker key={camera.id} position={[camera.lat, camera.lng]}>
          <Popup>
            <div className="text-xs">
              <p className="font-semibold">{camera.name}</p>
              <p className="text-gray-500">{camera.department}</p>
              <p className="mt-1 plate-mono">
                {camera.lat.toFixed(4)}, {camera.lng.toFixed(4)}
              </p>
            </div>
          </Popup>
        </Marker>
      ))}

      {pulses.map((pulse) => (
        <Marker key={pulse.id} position={[pulse.lat, pulse.lng]} icon={pulseIcon}>
          <Popup>
            <div className="text-xs">
              <p className="plate-mono font-semibold">{pulse.plate_text}</p>
              <p className="text-noc-critical">{pulse.flag_type}</p>
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}

const GISMap = dynamic(() => Promise.resolve(GISMapInner), { ssr: false });

export default function MapPage() {
  return (
    <div className="h-full w-full">
      <GISMap />
    </div>
  );
}
