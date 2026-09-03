# Sentinel — Scale Strategy & Statewide Migration Plan
### Gujarat Police Hackathon 2026
**Version:** 1.0 | **Author:** Chief Enterprise Architect

---

## 1. Migration Phases

Sentinel is designed to scale through three discrete, independently-deployable phases. Each phase is a superset of the previous phase's data model and API contract — no phase requires a client-facing breaking change, only infrastructure substitution behind stable interfaces (the event bus interface and the DB read interface are the two abstraction seams that absorb all scale-driven architecture changes).

### 1.1 Phase 1 — 30 Streams (Hackathon Prototype / Single Server)
**Goal:** Prove the detection→match→alert pipeline end-to-end within hackathon constraints.

| Component | Implementation |
|---|---|
| Ingestion | Single-process RTSP client pool (30 concurrent TCP sessions) |
| Analytics | 1 GPU (RTX 4090), TensorRT INT8 engine, frame-skip-3 policy |
| Event bus | Redis Streams (single node) — sufficient at this volume, and forward-compatible API with Phase 2 |
| Database | Single PostgreSQL 16 instance, daily-partitioned tables |
| Matching | In-process Redis hash lookup |
| Deployment | Docker Compose, single VM/bare-metal server |
| Failure tolerance | None (single point of failure; acceptable for a judged prototype demo) |

**Exit criteria to Phase 2:** sustained demo-quality performance at 30 streams; validated ANPR accuracy ≥ 90% on representative Gujarat plate formats (including bilingual Gujarati/English plates) and lighting conditions.

### 1.2 Phase 2 — 1,000 Streams (Multi-District Pilot)
**Goal:** Validate the architecture under real multi-district load, prove horizontal scaling of the inference tier and DB read path.

| Component | Change from Phase 1 |
|---|---|
| Ingestion | Distributed across regional edge nodes (one ingestion cluster per pilot district) |
| Analytics | GPU pool (12× L40S) with a scheduler assigning camera streams to workers based on load; **Redis Streams broker becomes a 3-node cluster** with consumer groups for horizontal event fan-out |
| Database | 1 primary + 2 async read replicas; PgBouncer connection pooling; Web UI reads exclusively from replicas |
| Matching | Redis Streams consumer-group-based matching workers (horizontally scaled, each owns a partition of camera IDs) |
| Deployment | Kubernetes (single regional cluster), Helm charts, autoscaling on GPU worker pool (HPA keyed on queue depth) |
| Failure tolerance | N+1 GPU workers, Patroni-managed PG failover (RTO < 30s) |

**Key scaling decisions introduced at this phase:**
- **Redis Streams broker**: replaces the single-node Redis pub/sub of Phase 1 with a clustered Streams deployment supporting consumer groups, so multiple matching workers can share the detection event load without duplicate processing (each event is claimed by exactly one consumer in the group).
- **Dedicated GPU inference pool**: inference is decoupled from the ingestion/API tier onto purpose-built GPU nodes, scheduled by a lightweight bin-packing scheduler (streams assigned to the least-loaded GPU, rebalanced on worker join/leave).
- **DB read replicas**: the GIS dashboard's read-heavy query pattern (map pans, historical trail lookups) is fully offloaded from the write-serving primary.

**Exit criteria to Phase 3:** sustained 1,000-stream load for 30 consecutive days with < 0.5% frame-drop rate and alert delivery p99 < 3s; multi-district watchlist sync validated (an entry added in District A is enforced in District B within 5s).

### 1.3 Phase 3 — 80,000 Streams (Statewide Deployment)
**Goal:** Full Gujarat statewide coverage across all police ranges, GSRTC, and (per the rollout roadmap, §3) other government bodies.

| Component | Change from Phase 2 |
|---|---|
| Ingestion | One Kubernetes cluster per police range (~15–20 regional clusters), each handling ingestion for cameras within its geographic jurisdiction |
| Analytics | ~950 GPUs distributed across regional clusters (no cross-region video transit) |
| Event bus | **Migrates from Redis Streams to Apache Kafka** — Kafka's log-structured, disk-backed durability and much higher partition-count ceiling (Redis Streams practically tops out in the low thousands of consumer-group members; Kafka scales to tens of thousands of partitions across a broker cluster) is required at this event volume (~80,000 streams × 8.33 fps ≈ 666,000 detection-candidate events/sec at peak, of which a filtered subset become actual `Detection` events after confidence thresholding) |
| Database | **Migrates from partitioned PostgreSQL to TimescaleDB hypertables** — native compression (columnar compression on closed chunks, typically 10-20x on detection metadata), continuous aggregates for dashboard rollups (e.g., "detections per district per hour" materialized incrementally rather than recomputed), and better multi-node scaling than vanilla PG partitioning |
| Matching | Kafka Streams (or ksqlDB) based matching topology, statefully partitioned by camera region, with the `watchlist_entries` hot set replicated to every region's local Redis cache (write-through from the central watchlist service) so matching latency stays local and does not depend on cross-region network hops |
| Deployment | Multi-region Kubernetes federation (one cluster per range + 1 central control-plane cluster for watchlist management, state admin, and cross-region rollups) |
| Failure tolerance | Full regional isolation — a Kafka/DB outage in one range does not affect matching or alerting in any other range; only cross-range dashboard rollups degrade gracefully to "last known good" cached aggregates |

**Why Kafka over scaling Redis Streams further:** Redis Streams is an excellent broker up to the low-thousands-of-camera range because of its simplicity and sub-millisecond latency, but it is fundamentally a single-node-primary structure per stream key with limited native multi-broker partitioning compared to Kafka's purpose-built distributed log architecture. At 80,000-camera scale, Kafka's partition-per-region model, built-in replication (factor 3), and mature consumer-group rebalancing tooling reduce operational risk more than the marginal latency cost (Kafka's typical broker-side latency is single-digit milliseconds higher than Redis Streams, which is immaterial against the 50ms compute budget dominated by GPU inference, not broker transit).

**Why TimescaleDB over further PostgreSQL partitioning:** vanilla declarative partitioning (Phase 1/2) requires manual partition lifecycle management and does not compress historical chunks. At statewide data volumes (~80,000 cameras × 8.33 fps × ~15% object-presence rate ≈ ~100,000 detection rows/sec sustained, ~8.6 billion rows/day before compression), TimescaleDB's automatic chunk compression and continuous aggregates are necessary to keep both storage cost and dashboard query latency bounded.

---

## 2. Bandwidth & Processing Math

### 2.1 Per-Stream Bandwidth (1080p @ 25fps, H.264)
Using a representative H.264 bitrate for 1080p surveillance content (moderate motion, standard compression profile):

```
Per-stream bitrate           ≈ 4 Mbps  (typical H.264 1080p25 CCTV encode, 3–6 Mbps range
                                          depending on scene complexity and encoder profile)
Per-stream bytes/sec         = 4,000,000 bits ÷ 8 = 500,000 bytes/sec ≈ 500 KB/s
Per-stream bytes/day         = 500 KB/s × 86,400 s ≈ 43.2 GB/day (if it were persisted — it is not; see §8.1 of architecture.md)
```

### 2.2 Aggregate Ingress Bandwidth by Phase
```
Phase 1 (30 streams):      30 × 4 Mbps  = 120 Mbps
Phase 2 (1,000 streams):   1,000 × 4 Mbps = 4,000 Mbps = 4 Gbps
Phase 3 (80,000 streams):  80,000 × 4 Mbps = 320,000 Mbps = 320 Gbps (aggregate, statewide)
```
**Critical design point:** the 320 Gbps Phase 3 figure is an *aggregate* across ~15–20 regional clusters, not a single link. Per-region ingress is bounded by that region's camera count — e.g., a range with 5,000 cameras sees ~20 Gbps of regional ingress, which is well within standard 10GbE-bonded or 40GbE regional data-center uplinks. This regional decomposition is the entire rationale for the Model 4 architecture (§3.1 of architecture.md): **video never needs to leave its region**, only metadata does, and metadata volume is orders of magnitude smaller (a `Detection` event is ~1-2 KB vs. a video frame at ~150-300 KB).

### 2.3 Metadata (Event Bus) Bandwidth — the Actual Cross-Region Load
```
Effective inference rate per stream        = 8.33 fps (frame-skip-3 policy)
Assume ~15% of inference frames contain
  at least one trackable object of interest
Detection-candidate events/sec/stream      ≈ 8.33 × 0.15 ≈ 1.25 events/sec
Avg event payload (JSON, bbox+plate+meta)  ≈ 1.5 KB

Phase 1 (30 streams):    30 × 1.25 × 1.5 KB ≈ 56 KB/s   ≈ 0.45 Mbps
Phase 2 (1,000 streams): 1,000 × 1.25 × 1.5 KB ≈ 1.9 MB/s ≈ 15 Mbps
Phase 3 (80,000 streams):80,000 × 1.25 × 1.5 KB ≈ 150 MB/s ≈ 1.2 Gbps (statewide event-bus aggregate)
```
This confirms the architectural thesis: raw video bandwidth (320 Gbps at Phase 3) stays entirely regional, while the bandwidth that actually needs to traverse the statewide backbone to the central watchlist/audit/rollup services (1.2 Gbps) is nearly **three orders of magnitude smaller** — easily carried on redundant 10GbE links between the central control-plane cluster and each regional cluster.

### 2.4 TensorRT INT8 Optimization Gains
Sentinel's detection and OCR models are exported from FP32 training checkpoints to TensorRT engines with INT8 post-training quantization (calibrated on a representative Gujarat traffic-scene calibration dataset of ~2,000 images to preserve accuracy):

| Precision | Relative Inference Throughput | Relative VRAM Footprint | Typical Accuracy Delta |
|---|---|---|---|
| FP32 (baseline) | 1.0× | 1.0× | — (baseline) |
| FP16 | ~2.1× | ~0.5× | Negligible (<0.2% mAP drop) |
| INT8 (calibrated) | ~3.6–4.2× | ~0.25× | Small (~0.5–1.5% mAP drop, acceptable given calibration set quality) |

**Practical impact:** the INT8 throughput gain is what makes the sizing matrices in `architecture.md` §9 tenable — e.g., at Phase 3's ~85 streams/GPU figure, an FP32 deployment would require roughly 3.6–4.2× more GPUs (~3,400–4,000 GPUs instead of ~950), which is the difference between a fundable statewide deployment and an impractical one. INT8 calibration is re-run whenever the underlying detection model is retrained (target: quarterly retraining cadence incorporating newly-labeled edge cases from field feedback).

### 2.5 Frames-Per-Second Requirements Summary
```
Camera native capture rate:        25 fps (all phases, standard India CCTV spec)
Effective inference rate:          8.33 fps  (frame-skip-3, all phases)
Aggregate inference FPS required:
  Phase 1:  30 × 8.33   ≈ 250 fps aggregate
  Phase 2:  1,000 × 8.33 ≈ 8,330 fps aggregate
  Phase 3:  80,000 × 8.33 ≈ 666,400 fps aggregate

GPU throughput (single L40S, INT8, batched, detection+plate-region+OCR pipeline): ~700-750 fps sustained
  → streams/GPU ≈ 700 ÷ 8.33 ≈ 84–85 streams/GPU (matches sizing matrix in architecture.md §9.2/9.3)
```

---

## 3. Statewide Rollout Roadmap

Sentinel's rollout is sequenced to build institutional trust and technical maturity progressively, starting with the commissioning agency (Police) and expanding to adjacent government bodies whose camera estates and use cases naturally extend the same platform.

### Stage 1 — Gujarat Police (Months 1–8)
- **Months 1–2:** Hackathon prototype hardened into a pilot-ready build; onboard 3 pilot police stations (~30–50 cameras) in a single district (e.g., Ahmedabad).
- **Months 3–5:** Expand to Phase 2 scale (~1,000 cameras) across 5–7 districts; validate cross-district watchlist sync and RBAC jurisdiction scoping under real operational load.
- **Months 6–8:** District-by-district rollout to full police camera estate; establish the Watchlist Governance Committee (state admin role holders) and finalize retention/audit policy per Gujarat Police procedural requirements.

### Stage 2 — GSRTC (Gujarat State Road Transport Corporation) (Months 7–12, overlapping Stage 1 tail)
- GSRTC depot and highway-corridor cameras (toll plazas, major bus stand entries) are natural extensions: shared vehicle-of-interest use case (stolen buses, hit-and-run vehicles fleeing via highway corridors), and GSRTC's camera estate substantially overlaps geographically with state highway routes already partially covered by police traffic cameras.
- Integration is primarily an **onboarding exercise** (adding GSRTC's VMS endpoints to the Ingestion Plane), not new architecture — this validates the platform's camera-agnostic integration claim (§3 of `architecture.md`) with a second, independently-procured VMS vendor.

### Stage 3 — Urban Local Bodies / Municipal Corporations (Months 12–20)
- Ahmedabad Municipal Corporation, Surat Municipal Corporation, Vadodara, Rajkot, and other municipal corporations' traffic and public-safety camera networks are onboarded, expanding coverage into urban intersections, markets, and public spaces beyond police-owned infrastructure.
- This stage introduces **new RBAC roles and jurisdiction boundaries** (municipal traffic police vs. state police vs. municipal corporation civic staff each need different scoped access), extending but not restructuring the RBAC model in §8.3 of `architecture.md`.
- This is the largest single volume increase (municipal traffic camera estates are typically the densest per-capita), and is the stage that pushes the platform from Phase 2 toward Phase 3 scale.

### Stage 4 — Panchayats (Rural Local Government) (Months 18–28, overlapping Stage 3 tail)
- Extension to rural and semi-urban Panchayat-managed camera infrastructure (typically sparser, lower-bandwidth-link deployments — village entry points, rural police chowky cameras).
- Given lower-bandwidth rural connectivity, this stage validates the Ingestion Plane's degraded-link handling (exponential backoff, §10.3 of `architecture.md`) and may introduce an **edge-caching/store-and-forward mode** for metadata (not raw video) at sites with intermittent connectivity, buffering `Detection` events locally and syncing when connectivity resumes.

### Stage 5 — Health Department (Months 24–32)
- Final planned stage: integration with Health Department infrastructure, primarily for non-surveillance use cases distinct from the watchlist-matching core — e.g., ambulance/emergency-vehicle priority-corridor detection at intersections (a vehicle-class detection use case, not a watchlist-match use case), and potential public-health-emergency crowd-density analytics at facility entrances (aggregate counting, explicitly **not** individual tracking or identification, to respect the materially different privacy posture required for health-context deployments).
- This stage is scoped conservatively and requires a distinct data-governance review given the different regulatory and public-trust context of health infrastructure versus law-enforcement infrastructure.

### Rollout Sequencing Rationale
```
Police  →  GSRTC  →  Urban Local Bodies  →  Panchayats  →  Health
  │           │              │                    │            │
  │           │              │                    │            └─ Distinct use case,
  │           │              │                    │               conservative scope,
  │           │              │                    │               separate governance review
  │           │              │                    └─ Validates degraded-connectivity
  │           │              │                       handling, largest geographic spread
  │           │              └─ Largest volume increase, new RBAC boundaries,
  │           │                 pushes platform to Phase 3 scale
  │           └─ Second VMS vendor integration, validates camera-agnostic claim,
  │              natural highway-corridor use-case overlap
  └─ Commissioning agency, establishes governance model, hardens core platform
```
Each stage is gated on the previous stage's exit criteria (from §1) being met at the relevant scale tier, ensuring the platform is never pushed into a new operational domain faster than its demonstrated technical maturity supports.

---
*End of scale_strategy.md*
