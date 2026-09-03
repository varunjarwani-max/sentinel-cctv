"use client";

import "./globals.css";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Map as MapIcon,
  Camera as CameraIcon,
  Bell,
  ShieldCheck,
} from "lucide-react";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/map", label: "GIS Map", icon: MapIcon },
  { href: "/cameras", label: "Cameras", icon: CameraIcon },
  { href: "/alerts", label: "Alerts", icon: Bell },
];

function useConnectionStatus(): boolean {
  const [connected, setConnected] = useState<boolean>(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;

    const connect = () => {
      if (!mountedRef.current) return;

      let socket: WebSocket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        setConnected(false);
        reconnectTimerRef.current = setTimeout(connect, 3000);
        return;
      }

      wsRef.current = socket;

      socket.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };

      socket.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket.close();
      };
    };

    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }
    };
  }, []);

  return connected;
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const connected = useConnectionStatus();

  return (
    <html lang="en">
      <head>
        <title>Sentinel Command Console</title>
        <meta
          name="description"
          content="Sentinel real-time CCTV edge analytics command dashboard"
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="bg-noc-bg text-gray-200 font-sans">
        <div className="flex h-screen w-screen overflow-hidden">
          <aside className="flex h-full w-[240px] shrink-0 flex-col border-r border-noc-border bg-noc-panel">
            <div className="flex items-center gap-2 border-b border-noc-border px-5 py-5">
              <ShieldCheck size={24} className="text-noc-accent" />
              <div>
                <p className="text-sm font-bold tracking-wide text-gray-100">
                  SENTINEL
                </p>
                <p className="text-[10px] uppercase tracking-widest text-gray-500">
                  Gujarat Police
                </p>
              </div>
            </div>

            <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname?.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                      isActive
                        ? "bg-noc-accent/10 text-noc-accent"
                        : "text-gray-400 hover:bg-white/5 hover:text-gray-200"
                    }`}
                  >
                    <Icon size={17} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>

            <div className="flex items-center gap-2 border-t border-noc-border px-5 py-4">
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                  connected
                    ? "bg-noc-online shadow-[0_0_8px_2px_rgba(16,185,129,0.6)]"
                    : "bg-noc-critical shadow-[0_0_8px_2px_rgba(239,68,68,0.6)]"
                }`}
              />
              <span className="text-xs text-gray-400">
                {connected ? "Live feed connected" : "Reconnecting..."}
              </span>
            </div>
          </aside>

          <main className="flex-1 overflow-y-auto bg-noc-bg">{children}</main>
        </div>
      </body>
    </html>
  );
}
