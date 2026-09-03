# Sentinel — Technical Architecture Document
### Gujarat Police Hackathon 2026
**Version:** 1.0 | **Classification:** Internal — Hackathon Submission | **Author:** Chief Enterprise Architect

---

## 1. Executive Summary

### 1.1 Problem Statement
Gujarat's municipal and highway CCTV estate is fragmented across dozens of Video Management Systems (VMS) procured independently by different police ranges, GSRTC depots, and municipal corporations. There is no unified, real-time mechanism to:
- Detect and track vehicles or persons of interest across camera boundaries.
- Match live sightings against watchlists (stolen vehicles, FIR-linked number plates, missing persons) within operationally useful time windows.
- Provide command-room personnel a single map-based view of sightings across jurisdictions.

The result is that actionable intelligence — a stolen vehicle passing a toll camera, a wanted individual's vehicle crossing a checkpoint — is either never surfaced, or surfaces hours later during manual footage review.

### 1.2 High-Level Approach
**Sentinel** is a camera-agnostic, real-time video analytics and watchlist-matching platform that sits alongside existing VMS infrastructure rather than replacing it. It:
1. Ingests live RTSP streams from existing IP cameras/NVRs (no camera replacement required).
2. Runs edge/regional inference (vehicle detection, ANPR, person re-identification) on a decimated frame rate.
3. Matches every detection against a hot in-memory watchlist index.
4. Dispatches alerts to a real-time GIS command dashboard and mobile units within a sub-50ms processing budget (detection-to-alert-broadcast, excluding network/last-mile).
5. Persists **zero raw video** — only structured metadata, thumbnails, and cryptographically hashed evidence pointers — for privacy and storage-cost reasons.

### 1.3 Target Performance (Hackathon Prototype, 30 Cameras)
| Metric | Target |
|---|---|
| End-to-end detection-to-alert latency (p50) | < 50 ms (compute path only) |
| End-to-end detection-to-alert latency (p99, incl. network) | < 400 ms |
| ANPR read accuracy (clear plate, day) | ≥ 96% |
| ANPR read accuracy (night/IR) | ≥ 88% |
| Sustained throughput | 30 streams × 25 fps ingest, 8.33 fps effective inference/stream |
| Watchlist match query | < 5 ms (Redis-backed hot index) |
| System uptime target | 99.5% (prototype), 99.95% (statewide, see HA section) |

---

## 2. System Topology

Sentinel is organized into five logical planes. Data flows left to right, with a feedback loop from Alert Dispatch back into the Web UI for operator acknowledgment.

```
┌──────────────┐     ┌───────────────┐     ┌────────────────┐     ┌────────────────┐     ┌─────────────┐
│  INGESTION   │────▶│   ANALYTICS   │────▶│ MATCHING ENGINE│────▶│ ALERT DISPATCH │────▶│   WEB UI     │
│  (RTSP pull, │     │ (decode, det, │     │ (watchlist hot │     │ (pub/sub, push │     │ (GIS map,    │
│  frame queue)│     │  track, ANPR) │     │  index lookup) │     │  notif, siren) │     │  ops console)│
└──────────────┘     └───────────────┘     └────────────────┘     └────────────────┘     └─────────────┘
       │                     │                      │                      │                    ▲
       ▼                     ▼                      ▼                      ▼                    │
   [RTSP Relay]        [Metadata Store]      [Redis Hot Index]      [WebSocket/MQTT]      [Operator ACK]
       │                     │                      │                      │                    │
       └─────────────────────┴──────────────────────┴──────────────────────┴────────────────────┘
                                        Central Event Bus (Redis Streams → Kafka at scale)
```

**Plane responsibilities:**

1. **Ingestion Plane** — Maintains persistent RTSP sessions to each camera/NVR, normalizes codecs (H.264/H.265), and pushes decoded frames into a bounded, backpressure-aware queue per stream. Handles reconnect/backoff (see §10).
2. **Analytics Plane** — GPU-backed inference workers consume frames, run object detection (vehicles, persons), multi-object tracking (ByteTrack), and license-plate recognition (detection + segmentation + OCR). Emits structured `Detection` and `Track` events.
3. **Matching Engine** — Subscribes to the detection event stream, performs O(1) hash/exact-match lookups against a Redis-resident watchlist index (plate numbers, person embeddings via approximate nearest-neighbor for face/gait if enabled), and on a hit emits an `Alert` event.
4. **Alert Dispatch** — Fans the `Alert` event out to WebSocket-connected dashboards, MQTT-connected mobile units, and (optionally) SMS/siren integrations. Writes an immutable audit record.
5. **Web UI** — React-based GIS command console rendering live camera pins, sighting trails, and alert cards; also serves the CDN-backed HLS preview for human verification of a hit (see §4).

---

## 3. Integration Framework: Blended VMS Model 4 + Model 2

Municipal CCTV deployments in India generally fall into four VMS integration patterns. Sentinel deliberately blends two of them to balance latency, cost, and non-disruption of legacy systems.

### 3.1 Model 4 — Central AI + Distributed Stream Processing (primary pattern)
- Inference workers are deployed **regionally** (one GPU pool per police range / district data center), not centrally in a single data center and not on-camera.
- Each regional worker pool pulls RTSP from cameras within its geographic/network radius (to bound RTT), runs the analytics pipeline, and publishes only **metadata** (bounding boxes, plate strings, embeddings, timestamps, camera ID) to the central event bus.
- This avoids backhauling raw video to a central site (bandwidth-prohibitive at 80,000-camera scale) while keeping model management, watchlist sync, and alerting logic centralized.

### 3.2 Model 2 — Direct Edge Playback (secondary pattern, for human verification only)
- When an operator needs to **visually verify** a hit (e.g., confirm plate OCR before dispatching a unit), the Web UI opens a direct low-latency playback path to the camera's existing VMS/NVR — not through the inference pipeline.
- This is a read-only, on-demand HLS pull (see §4.2), used exclusively for human-in-the-loop verification, never for the automated detection path.

### 3.3 Why Blended
Pure Model 4 alone would still require raw frames to transit to the Web UI for verification, adding load to the inference network. Pure Model 2 alone cannot support real-time automated matching, since VMS playback protocols are optimized for human viewing, not machine consumption. The blend keeps the **machine path** (Model 4) and **human path** (Model 2) architecturally and network-path separate, so a burst of verification traffic never competes with the inference path's bandwidth budget.

| Path | Protocol | Consumer | Persisted? |
|---|---|---|---|
| Machine detection path | RTSP (TCP) | Analytics Plane | No (frames discarded after inference) |
| Human verification path | HLS (via CDN) | Web UI operator | No (CDN edge cache only, TTL 60s) |

---

## 4. Video Transmission Strategy

### 4.1 TCP RTSP for Edge Inference
- All camera-to-inference-worker links use **RTSP over TCP** (not UDP), trading a small amount of latency for zero packet loss.
- Rationale: ANPR and tracking degrade sharply with dropped frames (a single lost I-frame can break a ByteTrack track and corrupt an OCR read mid-sequence). UDP's jitter/loss tolerance is unacceptable when a missed frame means a missed plate, whereas TCP's retransmission cost (~1-3ms on LAN, ~10-20ms on regional MPLS/fiber backhaul) is negligible against the 50ms compute budget.
- Connection pooling: each regional worker pool maintains a persistent TCP RTSP session per camera; sessions use RTP interleaved over the RTSP TCP channel (RFC 2326 §10.12) to avoid separate UDP ports traversing firewalls/NAT, which is critical for government network environments with restrictive perimeter policies.

### 4.2 CDN HLS for Web Playback
- The human verification path (§3.2) transcodes the live RTSP feed to HLS (6-second segments, 2-segment sliding window) at the regional edge, and serves it through a CDN (e.g., CloudFront-equivalent / NIC-hosted CDN for government deployments).
- HLS is chosen over RTSP/WebRTC for the Web UI because: (a) it works through standard HTTPS on port 443 with no special firewall rules, (b) it scales horizontally via CDN edge caching when many operators view the same camera during a multi-jurisdiction incident, (c) 6-12s of glass-to-glass latency is acceptable for verification (not for automated detection, which never uses this path).

| Path | Transport | Typical Latency | Use Case |
|---|---|---|---|
| RTSP/TCP | Camera → Regional Worker | 40–120 ms (LAN/MPLS) | Machine inference |
| HLS/CDN | Regional Edge → Browser | 6,000–12,000 ms | Human visual verification |

---

## 5. Machine Learning & Computer Vision Specifications

### 5.1 Frame Skip Logic
- Cameras stream at 25 fps (standard Indian broadcast-derived CCTV rate). Running full inference on every frame is computationally wasteful for slow-moving traffic-scene content.
- **Policy:** process every **3rd frame** (effective inference rate: 8.33 fps per stream), using a modulo counter per stream: `if frame_index % 3 == 0: enqueue_for_inference(frame)`.
- Skipped frames are still passed to the tracker's motion-prediction step (Kalman filter state update using timestamp delta) so track continuity is not lost, but no detector/OCR inference runs on them.
- At 8.33 fps, a vehicle moving at 60 km/h (16.7 m/s) travels ~2.0m between inference frames — well within the tracker's association gate (configured at 3.5m for vehicle class), so track continuity is preserved.

### 5.2 ByteTrack Tracking States
Sentinel uses **ByteTrack** (association via IoU + high/low confidence two-stage matching) for multi-object tracking, with the following state machine per track:

```
       new detection, no match
              │
              ▼
        ┌───────────┐   matched next frame    ┌───────────┐
        │  TENTATIVE│ ───────────────────────▶ │ CONFIRMED │
        └───────────┘                          └───────────┘
              │ no match within                      │ no match within
              │ 1 frame (3 real frames)               │ 30 frames (~3.6s @ 8.3fps)
              ▼                                       ▼
        ┌───────────┐                          ┌───────────┐
        │  DELETED  │ ◀────── no match ──────  │   LOST    │
        └───────────┘        within 30 frames  └───────────┘
                              of LOST state            │
                                                 matched again
                                                 (re-ID via
                                                  appearance embed)
                                                        │
                                                        ▼
                                                  back to CONFIRMED
```

- **TENTATIVE**: newly detected object, not yet confirmed as a stable track (avoids alerting on single-frame false positives).
- **CONFIRMED**: matched in ≥2 consecutive inference frames; eligible for watchlist matching.
- **LOST**: no association found for up to 30 inference frames (~3.6s at 8.33 fps); track is kept alive in memory with predicted (Kalman-extrapolated) position, allowing re-identification if the object re-emerges (e.g., briefly occluded by another vehicle).
- **DELETED**: exceeded the LOST threshold; track is finalized, its full trajectory (`vehicle_tracks` row) is closed out and written to PostgreSQL.
- High-confidence detections (conf ≥ 0.6) are matched first by IoU; low-confidence detections (0.1 ≤ conf < 0.6) are matched second against remaining unmatched tracks — this is the core ByteTrack innovation that recovers partially-occluded objects that would otherwise be dropped by naive high-threshold-only pipelines.

### 5.3 Adaptive Thresholding for Plate Segmentation
License plate character segmentation (post plate-region detection, pre-OCR) uses adaptive (local) thresholding rather than a single global binarization threshold, since Indian plates are photographed under wildly variable illumination (direct sun glare, IR night illumination, shadowed underpasses):

- **Method:** Gaussian-weighted adaptive threshold (OpenCV `ADAPTIVE_THRESH_GAUSSIAN_C` equivalent), block size 15–25px (scaled to plate crop resolution), constant C = 4, recomputed per plate crop rather than per frame.
- **Pre-processing chain:** plate crop → CLAHE contrast normalization (clip limit 2.0, tile grid 8×8) → adaptive threshold → morphological open (3×3 kernel) to remove salt-noise → connected-component character segmentation → per-character resize to 32×32 → CNN OCR classifier.
- **Night/IR mode:** when a camera's IR-cut filter state metadata (if exposed by the NVR's ONVIF profile) indicates IR-illuminated capture, the pipeline switches CLAHE clip limit to 3.5 and block size down to 11px to compensate for the lower dynamic range typical of IR sensors.
- **Fallback:** if character segmentation produces fewer than 6 or more than 11 connected components (Indian plates are 9-10 characters typically, formatted `SS-DD-LL-NNNN`), the crop is flagged `low_confidence` and re-queued for a second read attempt on the next tracked frame of the same vehicle, rather than emitting a low-quality OCR result.

---

## 6. PostgreSQL Schema & Query Design

### 6.1 ER Diagram (ASCII)

```
┌────────────────────┐        ┌──────────────────────┐        ┌────────────────────┐
│      cameras        │        │    vehicle_tracks     │        │     detections      │
├────────────────────┤        ├──────────────────────┤        ├────────────────────┤
│ camera_id      PK   │◀──┐    │ track_id        PK    │◀──┐    │ detection_id   PK    │
│ vms_source_id       │   │    │ camera_id       FK ───┼───┘    │ track_id       FK ───┼──┐
│ district_id    FK ──┼─┐ │    │ first_seen_at         │        │ camera_id      FK ───┼──┤
│ lat, lon             │ │ └───┼ last_seen_at          │        │ ts                   │  │
│ rtsp_url (encrypted) │ │     │ plate_text            │        │ bbox_x,y,w,h         │  │
│ status               │ │     │ plate_confidence       │        │ class_label          │  │
│ vms_model            │ │     │ vehicle_class          │        │ plate_text            │  │
└────────────────────┘ │     │ track_state            │        │ plate_confidence       │  │
                        │     │ trajectory_geom (PostGIS)│      │ frame_hash            │  │
┌────────────────────┐ │     └──────────────────────┘        │ embedding (vector)     │  │
│     districts        │◀┘                                     └────────────────────┘  │
├────────────────────┤                                                                    │
│ district_id     PK   │        ┌──────────────────────┐                                  │
│ name                  │        │  watchlist_entries     │        ┌────────────────────┐ │
│ region_id      FK     │        ├──────────────────────┤        │      alerts          │ │
└────────────────────┘        │ entry_id         PK    │◀──┐    ├────────────────────┤ │
                                │ entry_type (plate/     │   │    │ alert_id        PK    │ │
                                │   person/vehicle_class) │   └────┼ watchlist_entry_id FK  │ │
                                │ value (plate/embedding) │        │ detection_id   FK ───┼─┘
                                │ priority                │        │ triggered_at           │
                                │ case_ref (FIR number)   │        │ acknowledged_by FK(user)│
                                │ added_by         FK     │        │ acknowledged_at        │
                                │ active_from/to           │        └────────────────────┘
                                └──────────────────────┘

┌────────────────────┐        ┌──────────────────────┐
│       users          │        │     audit_log          │
├────────────────────┤        ├──────────────────────┤
│ user_id         PK   │◀──────┼ actor_user_id  FK      │
│ badge_number          │        │ action_type            │
│ role (RBAC)            │        │ resource_type/id       │
│ jurisdiction_scope     │        │ ts, ip_addr             │
└────────────────────┘        │ hash_chain_prev (SHA256) │
                                │ hash_chain_self (SHA256) │
                                └──────────────────────┘
```

### 6.2 Functional Indexes
```sql
-- Fast plate lookups (case-insensitive, punctuation-stripped)
CREATE INDEX idx_detections_plate_normalized
  ON detections (regexp_replace(upper(plate_text), '[^A-Z0-9]', '', 'g'))
  WHERE plate_text IS NOT NULL;

-- Time-windowed camera queries (used by GIS "last N minutes" view)
CREATE INDEX idx_detections_camera_ts
  ON detections (camera_id, ts DESC);

-- Active watchlist fast-path (partial index — only active entries)
CREATE INDEX idx_watchlist_active_value
  ON watchlist_entries (value)
  WHERE active_from <= now() AND (active_to IS NULL OR active_to > now());

-- Trajectory spatial queries (PostGIS GIST index)
CREATE INDEX idx_vehicle_tracks_geom
  ON vehicle_tracks USING GIST (trajectory_geom);

-- Unacknowledged alert triage queue
CREATE INDEX idx_alerts_unacked
  ON alerts (triggered_at DESC)
  WHERE acknowledged_at IS NULL;

-- Vector similarity search for person re-ID (pgvector, IVFFlat)
CREATE INDEX idx_detections_embedding
  ON detections USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### 6.3 Partitioning Logic
Both `detections` and `vehicle_tracks` are high-write, time-series-dominant tables and are **range-partitioned by day** on `ts` / `first_seen_at` respectively, using native PostgreSQL declarative partitioning (migrating to TimescaleDB hypertables at the 1,000+ stream tier — see `scale_strategy.md`):

```sql
CREATE TABLE detections (
    detection_id   BIGSERIAL,
    track_id       BIGINT,
    camera_id      INT NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    bbox_x         REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    class_label    TEXT,
    plate_text     TEXT,
    plate_confidence REAL,
    frame_hash     TEXT,
    embedding      VECTOR(512),
    PRIMARY KEY (detection_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE detections_2026_09_03 PARTITION OF detections
    FOR VALUES FROM ('2026-09-03') TO ('2026-09-04');
```

- A nightly cron (`pg_partman` extension) pre-creates the next 7 days of partitions and drops/archives partitions older than the retention window (default: 90 days for `detections`, 180 days for `vehicle_tracks`, configurable per district policy).
- Retention enforcement is itself an auditable action (logged to `audit_log`) since it constitutes evidence lifecycle management.
- Partition pruning means a "last hour" GIS query only scans today's partition, not the full historical table — critical once table sizes reach the billions-of-rows range at statewide scale.

---

## 7. End-to-End Latency Profile

Target: **< 50 ms** from a frame containing a watchlist-matching object being captured by the analytics worker, to the alert event being published on the internal event bus (this is the **compute path**; it excludes network transit to the operator's browser, which is accounted separately).

| Stage | Component | Budget (ms) | Notes |
|---|---|---|---|
| 1 | Frame dequeue + preprocessing (resize, normalize) | 3 | GPU-side, pinned memory transfer |
| 2 | Object detection inference (YOLO-class, TensorRT INT8) | 12 | Batched across streams on shared GPU, per-frame amortized cost |
| 3 | ByteTrack association update | 2 | CPU, O(n×m) Hungarian matching, n,m small (<50 objects/frame typical) |
| 4 | Plate region detection (if vehicle class) | 6 | Secondary lightweight detector, TensorRT INT8 |
| 5 | Plate segmentation + OCR | 14 | Adaptive threshold + CNN classifier, dominant cost |
| 6 | Watchlist hash lookup (Redis) | 3 | O(1) exact match; vector kNN path (person re-ID) adds ~8ms when enabled |
| 7 | Alert event construction + publish (Redis Streams) | 4 | Serialize + XADD |
| 8 | Event bus fan-out to subscriber(s) | 3 | Redis Streams consumer group dispatch |
| 9 | Audit log write (async, non-blocking) | (async — not on critical path) | Fire-and-forget with write-ahead buffer |
| **Total compute path** | | **~47 ms** | Within 50ms budget with ~3ms margin |

**Post-compute-path latency (not counted against the 50ms target, tracked separately as "alert delivery latency"):**

| Stage | Typical | p99 |
|---|---|---|
| WebSocket push to connected dashboard | 5–15 ms | 40 ms |
| Mobile push notification (FCM/APNs-equivalent) | 200–800 ms | 2,500 ms |
| SMS gateway (if configured, secondary channel) | 1,000–4,000 ms | 8,000 ms |

**Design implication:** the 50ms target governs the automated detection-to-alert-object-creation pipeline, which is fully within Sentinel's control. Last-mile delivery to a human depends on external factors (mobile network, SMS gateway) and is monitored as a separate SLA (target p99 < 3s for dashboard + mobile push combined).

---

## 8. Security Architecture

### 8.1 Zero Raw Video Disk Persistence
- Inference workers hold decoded frames **in GPU/pinned host memory only**; frames are never written to any filesystem, including temp directories. Frame buffers are ring-buffered and overwritten within one GOP cycle (~2 seconds).
- The only persisted visual artifact is a **cropped, JPEG-compressed thumbnail** of the matched object (plate region + surrounding vehicle bbox, typically <50KB), stored in object storage with the same retention/partition policy as its parent `detections` row, and referenced by a content hash (not by a mutable filename) to prevent silent substitution.
- Full-frame video remains solely on the source VMS/NVR (which retains its own existing, agency-owned retention policy) — Sentinel never duplicates the VMS's video archive.

### 8.2 TLS 1.3 Encryption
- All network hops carrying metadata or credentials (worker→event bus, event bus→dashboard, dashboard→API) are TLS 1.3 only; TLS 1.2 and below are disabled at the load balancer.
- RTSP sessions to cameras use RTSP-over-TLS (RTSPS, port 322) where the camera/NVR supports it; for legacy cameras that only support plaintext RTSP, that hop is confined to the physically-secured regional LAN segment and never traverses a WAN link in plaintext.
- Certificate rotation: automated 90-day rotation via internal ACME-compatible CA, with a 14-day overlap window to avoid handshake failures during rollover.

### 8.3 JWT-Based RBAC
- Every API and WebSocket connection authenticates via short-lived JWTs (15-minute access token, 12-hour refresh token), signed with RS256 (asymmetric — verification keys distributed to services, signing key held only by the auth service).
- Roles are hierarchical and jurisdiction-scoped:

| Role | Scope | Permissions |
|---|---|---|
| `field_officer` | Own district | View alerts, ACK alerts, view live map (own district cameras only) |
| `district_analyst` | Own district | + Add/edit watchlist entries (requires case reference), view historical tracks |
| `range_supervisor` | District group | + Cross-district alert visibility within range, approve watchlist entries |
| `state_admin` | Statewide | + Full watchlist management, camera onboarding, audit log read access |
| `system_service` | N/A (service-to-service) | Internal only, scoped to specific event bus topics, never issued to a human |

- JWT claims embed `jurisdiction_scope` as a claim, and every query is filtered server-side by that scope (never trusted purely from client-side UI hiding) — enforced at the API gateway layer via a policy-as-code check (OPA/Rego-style) before the request reaches application logic.

### 8.4 Read-Only DB Permissions for Client Queries
- The Web UI's query path connects to PostgreSQL through a dedicated `sentinel_readonly` role that has `SELECT`-only grants on a restricted set of views (never raw tables directly), preventing any accidental or malicious write/delete from the presentation layer.
- Writes (new detections, track updates, watchlist changes) happen exclusively through the backend analytics/matching services using a separate `sentinel_writer` role, connected only from within the trusted service mesh (mTLS, not reachable from the Web UI's network segment).
- This separation means a compromised or buggy dashboard client can, at worst, read data within its RBAC scope — it structurally cannot corrupt or delete evidence records.

### 8.5 Tamper-Proof Audit Trails
- Every state-changing action (watchlist add/edit, alert acknowledgment, camera onboarding, data retention purge) is written to `audit_log` as an **append-only, hash-chained** record:
  `hash_chain_self = SHA256(hash_chain_prev || actor_user_id || action_type || resource_id || ts)`
- This produces a Merkle-chain-like structure where any retroactive edit to a historical audit row breaks the hash chain for every subsequent row, making tampering computationally evident on periodic integrity verification (a scheduled job recomputes and compares the chain).
- The audit log table itself has **no UPDATE or DELETE grants** for any application role, including `sentinel_writer` — inserts only, enforced at the database privilege level, not just application logic, so even a fully compromised backend service cannot rewrite history without also compromising the database superuser (a separate, offline-managed credential).

---

## 9. Sizing Matrices

### 9.1 30 Cameras (Hackathon Prototype — Single Server)
| Resource | Spec |
|---|---|
| CPU | 1× 16-core / 32-thread (e.g., AMD Ryzen 9 / Threadripper-class), for RTSP handling, tracking, API |
| GPU | 1× NVIDIA RTX 4090 (24GB VRAM) — sufficient for 30 streams at 8.33 fps effective inference with TensorRT INT8 batching |
| RAM | 64 GB |
| Bandwidth (ingress) | 30 streams × ~4 Mbps (1080p H.264) ≈ 120 Mbps |
| Bandwidth (egress, HLS verification) | Assume ≤5 concurrent viewers × 3 Mbps ≈ 15 Mbps peak |
| Disk | 1 TB NVMe SSD (metadata, thumbnails, DB — no raw video) |
| Deployment | Single Docker Compose stack: PostgreSQL, Redis, 1 inference worker, API, Web UI |

### 9.2 1,000 Cameras (Pilot / Multi-District)
| Resource | Spec |
|---|---|
| CPU (ingestion/API tier) | 8× 16-core nodes (Kubernetes-managed) |
| GPU (inference pool) | 12× NVIDIA L40S (48GB), ~85 streams/GPU at 8.33fps effective with INT8 batching |
| RAM | 8× 128 GB nodes (1 TB aggregate) |
| Bandwidth (ingress) | 1,000 × 4 Mbps ≈ 4 Gbps (regional links aggregated) |
| Bandwidth (egress, HLS) | ~50 concurrent viewers × 3 Mbps ≈ 150 Mbps peak |
| Disk | PostgreSQL primary: 4 TB NVMe (RAID10); 2 read replicas at 4 TB each |
| Message broker | Redis Streams cluster, 3 nodes, 32 GB RAM each |
| Deployment | Kubernetes (regional cluster), Redis Streams, DB read replicas (see `scale_strategy.md`) |

### 9.3 80,000 Cameras (Statewide Deployment)
| Resource | Spec |
|---|---|
| CPU (ingestion/API tier) | ~400 nodes across regional Kubernetes clusters (one cluster per police range, ~15-20 ranges) |
| GPU (inference pool) | ~950 GPUs (NVIDIA L40S-class or successor) distributed regionally, ~85 streams/GPU |
| RAM | ~50 TB aggregate across regional clusters |
| Bandwidth (ingress, statewide aggregate) | 80,000 × 4 Mbps ≈ 320 Gbps (distributed — no single link carries this; regional aggregation is the design point, see `scale_strategy.md` §2) |
| Bandwidth (egress, HLS, statewide peak) | ~2,000 concurrent viewers × 3 Mbps ≈ 6 Gbps peak, CDN-absorbed |
| Disk | TimescaleDB multi-node cluster, ~500 TB aggregate (compressed hypertables, 90-day hot + archived cold tier) |
| Message broker | Kafka cluster, 12+ brokers, replication factor 3 |
| Deployment | Multi-region Kubernetes federation, Kafka backbone, TimescaleDB, central watchlist sync service |

---

## 10. High Availability & Disaster Recovery

### 10.1 Database Replication Topology
- **Prototype (30 cameras):** single PostgreSQL instance with continuous WAL archiving to object storage (point-in-time recovery, RPO ≈ 5 minutes).
- **Pilot (1,000 cameras):** 1 primary + 2 streaming read replicas (async, `hot_standby` mode) in the same region; the Web UI's read-only role connects to replicas via a connection-pooled load balancer (PgBouncer), isolating read load from write path. Automatic failover via Patroni (etcd-based leader election), RTO target < 30s.
- **Statewide (80,000 cameras):** TimescaleDB multi-node with regional primaries and cross-region logical replication for the `watchlist_entries` table specifically (must be globally consistent within seconds, since a watchlist addition in one district must be immediately enforceable statewide), while `detections`/`vehicle_tracks` remain regionally sharded (no cross-region replication needed — a Vadodara camera's detections do not need to be replicated to the Rajkot cluster).

### 10.2 Inference Failover
- Each regional GPU pool runs inference workers in an N+1 configuration (one spare worker per ~10 active workers).
- Worker health is monitored via heartbeat (2s interval) to a lightweight coordinator (etcd or Kubernetes-native `Lease` objects); a worker missing 3 consecutive heartbeats (6s) is marked unhealthy and its assigned camera streams are rebalanced to the next-least-loaded healthy worker.
- Rebalancing target: < 10 seconds from failure detection to a camera's frames being processed again by a new worker — during this window, frames are still buffered by the Ingestion Plane (bounded queue, ~15s capacity) so no frames are silently dropped for short outages.

### 10.3 Worker Reconnection Backoff Strategy
For both camera-to-ingestion RTSP sessions and inference-worker-to-event-bus connections, Sentinel uses **exponential backoff with jitter** to avoid thundering-herd reconnection storms after a network blip or broker restart:

```
retry_delay = min(base_delay * (2 ^ attempt), max_delay) + random_jitter(0, jitter_window)

base_delay    = 500 ms
max_delay     = 30,000 ms (30s)
jitter_window = 0–1000 ms
max_attempts  = unbounded (camera links retry indefinitely; alert raised to ops after 5 min down)
```

- Attempt 1: ~0.5–1.5s, Attempt 2: ~1–2s, Attempt 3: ~2–3s, ... Attempt 6+: capped at 30s ± jitter.
- A camera link down for > 5 minutes triggers a `camera_offline` operational alert (distinct from a watchlist `alert`) to the district's operations dashboard, so field maintenance can be dispatched — offline cameras are a coverage gap, not just a technical metric.
- Event-bus (Redis Streams / Kafka) consumer reconnects resume from the last acknowledged offset (consumer group offset tracking), guaranteeing no alert is silently lost due to a broker reconnect — at-least-once delivery semantics, with alert deduplication at the Web UI layer keyed on `alert_id` (idempotent rendering).

---
*End of architecture.md*
