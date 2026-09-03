# Sentinel — Hackathon Presentation Deck
### Gujarat Police Hackathon 2026 | 13-Slide Pitch Outline
**Total runtime target:** ~9–10 minutes presentation + Q&A

---

## Slide 1 — Title & Executive Summary

**Visual Design & Diagram Specs:**
Full-bleed dark navy background (#0B1220), Gujarat Police emblem top-left (small, respectful placement), "SENTINEL" in large bold white sans-serif (e.g., Inter/Poppins Bold, 72pt) centered, tagline below in amber accent (#F5A623, 24pt). Bottom-right: team name, hackathon name, date. A subtle animated (or static, if PDF) radar-sweep graphic in the background at 8% opacity — a single thin rotating line inside a circle — to visually cue "detection/surveillance" without being heavy-handed.

**Bulleted Text:**
- **Sentinel**: Real-Time, Statewide Vehicle & Person-of-Interest Detection Platform
- Built for Gujarat Police — deployable across existing camera infrastructure
- Sub-50ms detection-to-alert compute pipeline
- Zero raw video storage — privacy-first by architecture, not by policy alone

**Verbatim Presenter Script:**
"Good [morning/afternoon], panel. My name is [Presenter Name], and over the next few minutes I'm going to show you Sentinel — a real-time vehicle and person-of-interest detection platform we built specifically for Gujarat Police's existing camera infrastructure. Sentinel doesn't ask you to replace a single camera. It sits alongside your current VMS, watches every stream in real time, and the moment it sees a vehicle or person on your watchlist, it puts that alert on an officer's screen in under fifty milliseconds of compute time. And it does all of this without ever writing a single frame of raw video to disk. Let's walk through how."

---

## Slide 2 — The Fragmentation Challenge in Municipal Surveillance

**Visual Design & Diagram Specs:**
Split-screen visual: left half shows a chaotic diagram of 6-8 disconnected camera icons, each connected to a different small "VMS silo" box (labeled with generic vendor-style names like "VMS A", "VMS B", "NVR C"), with red "X" marks between the silos indicating no interconnection. Right half is left mostly empty/greyed, foreshadowing Slide 3's solution. Color palette: muted greys and reds for the "problem" side.

**Bulleted Text:**
- Gujarat's camera estate spans multiple independently-procured VMS platforms
- No cross-camera, cross-jurisdiction real-time correlation today
- A stolen vehicle sighting on one camera network is invisible to another
- Manual footage review means actionable intelligence arrives hours too late

**Verbatim Presenter Script:**
"Here's the problem we set out to solve. Gujarat's CCTV infrastructure — across police ranges, RTO checkpoints, municipal intersections — was procured over many years, from many vendors, as many separate systems. Each one works fine in isolation for its own operator. But none of them talk to each other. So if a stolen vehicle passes a camera in one jurisdiction, and then crosses into another jurisdiction ten minutes later, there is today no automated way to connect those two sightings. An officer has to know to look, has to manually pull footage, has to manually cross-reference a plate number. By the time that happens, the vehicle is gone. We're not proposing to rip out and replace this infrastructure. We're proposing to make it talk to itself, in real time."

---

## Slide 3 — Sentinel Solution Overview

**Visual Design & Diagram Specs:**
Single clean horizontal flow diagram: Camera icons (multiple, small) → arrow → "Sentinel" box (highlighted in amber, center-stage, larger than other elements) → arrow → "Command Dashboard" icon with a map pin. Below the flow, three small icon+label callouts: "Camera-Agnostic," "Real-Time," "Privacy-First." Clean white background, minimal text.

**Bulleted Text:**
- One platform layered on top of existing, heterogeneous camera infrastructure
- Real-time detection, tracking, and watchlist matching — not after-the-fact review
- A single unified GIS command view across every connected jurisdiction
- Deployable incrementally: 30 cameras today, 80,000 cameras statewide tomorrow

**Verbatim Presenter Script:**
"Sentinel is the connective layer. It pulls a live feed from every camera — regardless of which VMS vendor it came from — runs real-time AI analytics on that feed, and the moment it detects something on a watchlist, it raises an alert on a single, unified command dashboard that any authorized officer, in any connected jurisdiction, can see. We designed it to start small — the prototype you'll see today runs on thirty cameras on a single server — and to scale, without architectural rewrites, all the way to a statewide deployment of eighty thousand cameras. Let me show you what that looks like in practice."

---

## Slide 4 — Real-Time Detection & Sighting Tracking (Demo Preview)

**Visual Design & Diagram Specs:**
Screenshot/mockup of the live demo UI: a video frame with a bounding box drawn around a vehicle, a green "MATCH" tag with the plate number overlaid, and a small side panel showing "Track ID," "First Seen," "Confidence: 94%." Below the mockup, a horizontal mini-timeline showing 3-4 timestamped thumbnail crops of the same vehicle across different cameras, connected by dotted lines, illustrating cross-camera tracking.

**Bulleted Text:**
- Live bounding-box detection and license plate recognition
- Persistent tracking of the same vehicle across multiple camera views
- Instant visual confirmation the moment a watchlist match occurs
- This is what you're about to see running live, not a canned video

**Verbatim Presenter Script:**
"This is our live demo screen. What you're looking at is real inference, running right now, on real camera streams. Every vehicle gets a bounding box the instant it's detected. The license plate gets read and overlaid on screen. And when that plate matches something on our watchlist — a stolen vehicle, an FIR-linked number — you'll see it flagged green, instantly, with the officer's confidence score right there. And critically, watch what happens as the vehicle moves from one camera's view to the next — Sentinel keeps following it. That's not four separate detections. That's one tracked journey across your camera network. We'll run that demo for you in just a moment."

---

## Slide 5 — End-to-End System Architecture

**Visual Design & Diagram Specs:**
Full-width horizontal pipeline diagram with five boxes left-to-right: "Ingestion" → "Analytics" → "Matching Engine" → "Alert Dispatch" → "Web UI," connected by arrows, with a horizontal band underneath labeled "Central Event Bus" connecting to all five boxes vertically (matches the topology diagram in architecture.md §2). Each box has a 1-line sub-label (e.g., Ingestion: "RTSP pull, frame queue"). Use consistent icon style (line icons, single accent color).

**Bulleted Text:**
- Five-stage pipeline: Ingestion → Analytics → Matching → Dispatch → Web UI
- Blended integration model: distributed edge inference + direct playback for human verification
- TCP RTSP for zero-loss machine inference; CDN HLS for scalable human review
- Every stage independently scalable, independently fault-tolerant

**Verbatim Presenter Script:**
"Under the hood, Sentinel is five stages. Ingestion pulls the live stream from every camera over RTSP — using TCP, not UDP, because we cannot afford to drop a frame that might contain the one plate we needed to read. Analytics runs our AI models on that stream. The Matching Engine checks every detection against our watchlist in real time. Alert Dispatch pushes that match out to every connected dashboard and mobile device. And the Web UI is where your officers actually see and act on it. Each of these stages is independently scalable — which matters enormously once you go from thirty cameras to eighty thousand, and I'll show you exactly how that scaling works in a few slides."

---

## Slide 6 — Multi-Stage AI Analytics Pipeline

**Visual Design & Diagram Specs:**
Vertical pipeline diagram inside the "Analytics" stage, zoomed in: "Frame Skip (every 3rd frame)" → "Object Detection (TensorRT INT8)" → "ByteTrack Multi-Object Tracking" → "Plate Region Detection" → "Adaptive Threshold Segmentation" → "OCR." Small state-machine inset diagram (simplified from architecture.md §5.2) showing Tentative → Confirmed → Lost → Deleted track states.

**Bulleted Text:**
- Frame-skip-3 policy: 8.33 effective fps per stream, tuned for real-world vehicle speeds
- ByteTrack: recovers partially-occluded and temporarily-lost tracks via two-stage matching
- Adaptive (locally-computed) thresholding for plate segmentation across lighting conditions
- Purpose-built for Indian plate formats and day/night/IR camera conditions

**Verbatim Presenter Script:**
"Our analytics pipeline is tuned specifically for real-world traffic conditions in Gujarat. We don't process every single frame — that would waste compute — we process every third frame, which still comfortably keeps pace with a vehicle moving at highway speed. We use an algorithm called ByteTrack for tracking, which is specifically good at handling occlusion — when one vehicle briefly blocks another from view, we don't lose the track, we recover it. And for plate reading, we use adaptive thresholding, which recalculates the right image processing settings for every single plate crop — because a plate in bright sun and a plate under IR night vision need completely different handling, and a one-size-fits-all approach fails constantly in the field."

---

## Slide 7 — Sub-50ms Watchlist Matching Engine

**Visual Design & Diagram Specs:**
Horizontal latency waterfall/stacked bar chart (matches architecture.md §7 table), 8 segments each labeled with stage name and millisecond value, stacked left to right, total bar length representing ~47ms, with a vertical dashed red line at the 50ms mark labeled "Budget Ceiling." Use a green-to-amber gradient across the bar to visually communicate "on budget."

**Bulleted Text:**
- Full detection-to-alert compute pipeline budgeted and measured stage-by-stage
- Redis-backed hot watchlist index: sub-5ms exact-match lookup
- Total compute path: ~47ms — within the 50ms target with margin
- Alert delivery to dashboard/mobile tracked as a separate, monitored SLA

**Verbatim Presenter Script:**
"This is the number we're proudest of. From the moment a frame is captured to the moment an alert is published internally, our entire pipeline — detection, tracking, plate reading, and the watchlist lookup itself — completes in under fifty milliseconds. We've budgeted every single stage, and we measure it continuously, not just once in a lab. The watchlist check itself, against our full active list, takes under five milliseconds, because we keep it hot in memory rather than querying a disk-based database on every single detection. Fifty milliseconds means the alert exists before the vehicle has even fully cleared the camera's field of view."

---

## Slide 8 — Real-Time GIS Command Interface

**Visual Design & Diagram Specs:**
Full-screen mockup of a dark-themed map dashboard (Google-Maps-style or OSM-style tiles), with camera pin icons scattered across a Gujarat district outline, one pin pulsing red with an alert card popup showing plate number, vehicle thumbnail, timestamp, and an "Acknowledge" button. A left sidebar lists live alerts in a scrollable feed. A top bar shows connected camera count and system health indicators (green dots).

**Bulleted Text:**
- Single map view spanning every connected camera and jurisdiction
- Live alert feed with one-tap acknowledgment and unit dispatch
- Jurisdiction-scoped visibility enforced by role-based access control
- Designed for control-room use — legible at a glance, actionable in one click

**Verbatim Presenter Script:**
"This is what your control room sees. Every connected camera is a pin on this map. The moment there's a watchlist match, that pin lights up, an alert card appears with the plate, a thumbnail, and a timestamp, and the officer on duty can acknowledge it and dispatch a unit in a single tap. Importantly, what an officer sees is scoped to their jurisdiction and role — a district-level analyst sees their district, a range supervisor sees across their range, and only state-level administrators see everything statewide. This isn't a generic map. It's built for the control room, for the person who has three seconds to make a decision, not three minutes to explore a dashboard."

---

## Slide 9 — Privacy by Design & Zero-Footprint Ingestion

**Visual Design & Diagram Specs:**
Simple two-column comparison: left column "Traditional CCTV Storage" with a stacked-disk icon growing tall labeled "Raw video, months of retention"; right column "Sentinel" with a small thumbnail icon and a lock icon labeled "Metadata + cropped thumbnail only, hash-chained audit trail." A small padlock/shield icon row at the bottom listing TLS 1.3, JWT RBAC, Read-Only Client Access, Tamper-Evident Audit Log.

**Bulleted Text:**
- Zero raw video ever written to disk by Sentinel — frames live in memory only
- Only a small cropped thumbnail of an actual match is persisted, hash-referenced
- TLS 1.3 everywhere; JWT-based, jurisdiction-scoped role access control
- Hash-chained, append-only audit log — tampering is structurally detectable

**Verbatim Presenter Script:**
"We know that a platform like this only earns public and institutional trust if privacy is built into the architecture, not bolted on as a policy document. So here's our commitment, and it's enforced in code, not just in writing: Sentinel never writes a single frame of raw video to disk. Ever. Frames exist in memory only, for the fraction of a second it takes to process them, and then they're gone. The only thing we ever store is a small cropped image of an actual watchlist match — nothing more — and every single action taken in this system, from adding a watchlist entry to acknowledging an alert, is written to a tamper-evident audit log where any retroactive edit is mathematically detectable. This is accountability by design."

---

## Slide 10 — Scaling Strategy (30 to 80,000 Streams)

**Visual Design & Diagram Specs:**
Three-stage horizontal roadmap bar: "Phase 1: 30 Streams" → "Phase 2: 1,000 Streams" → "Phase 3: 80,000 Streams," each stage as a progressively larger box, with small icon labels underneath each showing the key infrastructure shift (Redis single-node → Redis Cluster → Kafka; single PostgreSQL → PG + read replicas → TimescaleDB). A small bandwidth figure under each stage (120 Mbps / 4 Gbps / 320 Gbps aggregate).

**Bulleted Text:**
- Phase 1 (today): single server, 30 cameras — what you're seeing demoed live
- Phase 2: Redis Streams cluster, dedicated GPU pool, DB read replicas — 1,000 cameras
- Phase 3: Kafka backbone, regional Kubernetes clusters, TimescaleDB — 80,000 cameras statewide
- No architectural rewrite between phases — only infrastructure substitution behind stable interfaces

**Verbatim Presenter Script:**
"What you're seeing running today, on the table in front of you, is thirty cameras on a single server. But we didn't design this as a toy that gets thrown away once it works — we designed it as the first rung of a specific, engineered ladder. At a thousand cameras, we scale out the message broker, add a dedicated pool of inference GPUs, and add database read replicas — all without touching the core application logic. At eighty thousand cameras — full statewide coverage — the message broker becomes Kafka, the database becomes TimescaleDB, and inference runs across regional Kubernetes clusters, one per police range, so that video traffic never has to cross the entire state — only lightweight metadata does. Every number on this slide is in our architecture documentation, and we're happy to go through the bandwidth math in detail."

---

## Slide 11 — Deployment & Infrastructure Cost-Benefit Analysis

**Visual Design & Diagram Specs:**
Three-column cost table matching the sizing matrices in architecture.md §9, with a simplified visual bar-chart above showing relative infrastructure cost scaling (30 / 1,000 / 80,000 cameras) versus a dotted "cost per camera" line trending downward — illustrating economies of scale. Keep numbers exact and traceable to the architecture document; avoid invented currency totals not backed by the sizing matrix.

**Bulleted Text:**
- Prototype tier: single RTX 4090 server — proof of concept, near-zero incremental infra cost
- Pilot tier: 12-GPU regional pool serving ~1,000 cameras at ~85 streams/GPU (TensorRT INT8)
- Statewide tier: ~950 GPUs distributed regionally — no single camera requires new hardware per unit
- Cost driver is GPU inference density, not per-camera onboarding — INT8 quantization gives ~3.6–4.2× throughput over FP32, directly reducing GPU count needed

**Verbatim Presenter Script:**
"I want to be transparent about cost, because a prototype that can't be honestly costed to scale isn't a real proposal. The single biggest cost driver in a system like this is GPU inference capacity, and the single biggest lever we have on that cost is model optimization. By quantizing our models to INT8 using TensorRT, we get somewhere between three-point-six and four-point-two times the throughput of a naive full-precision deployment — which is the difference between needing roughly a thousand GPUs statewide versus needing three or four thousand. That's not a marginal optimization, that's the difference between a fundable statewide deployment and one that isn't. Every figure on this slide is derived directly from our sizing matrices, and we can walk through the full math with your technical team."

---

## Slide 12 — Phased Implementation Roadmap

**Visual Design & Diagram Specs:**
Horizontal Gantt-style timeline spanning ~32 months, five swimlanes labeled "Police," "GSRTC," "Urban Local Bodies," "Panchayats," "Health," each shown as a horizontal bar starting at its rollout month and overlapping slightly with the next (matches scale_strategy.md §3 timing). Month markers at 0, 8, 12, 20, 28, 32. Use a consistent government-appropriate color palette (deep blue, amber accent).

**Bulleted Text:**
- Months 1–8: Gujarat Police — pilot districts to full police camera estate
- Months 7–12: GSRTC — highway corridor and depot camera integration
- Months 12–20: Urban Local Bodies — municipal corporation traffic networks
- Months 18–28: Panchayats, Months 24–32: Health Department — conservative, governance-reviewed extensions

**Verbatim Presenter Script:**
"We've sequenced the statewide rollout deliberately, starting with the agency that commissions this system — Gujarat Police — over the first eight months, moving from a handful of pilot stations to full departmental coverage. GSRTC follows naturally, because highway corridor cameras and police traffic cameras already share a use case: a vehicle fleeing on a state highway. From there we extend to municipal corporations, which is our largest single jump in camera volume, and finally to Panchayats and the Health Department, each with its own governance review appropriate to that context — we are especially careful that the Health Department integration is scoped narrowly, to aggregate, non-identifying use cases, because that context demands a different privacy posture than law enforcement."

---

## Slide 13 — Team & Conclusion

**Visual Design & Diagram Specs:**
Clean closing slide: team member names/roles in a simple horizontal row with small circular headshot placeholders (or initials-in-circle if no photos), Sentinel logo centered above, contact/repo/demo-link footer. Background returns to the same dark navy as Slide 1 for visual bookending. A single closing line of text large and centered below the team row.

**Bulleted Text:**
- [Team Member 1] — [Role, e.g., ML/Computer Vision]
- [Team Member 2] — [Role, e.g., Backend/Infrastructure]
- [Team Member 3] — [Role, e.g., Frontend/GIS Dashboard]
- [Team Member 4] — [Role, e.g., Systems/DevOps]
- Sentinel: built to scale from 30 cameras to a statewide platform, without compromising privacy

**Verbatim Presenter Script:**
"That's Sentinel, and that's our team — [introduce each member and role briefly]. We didn't build a demo that only works on a stage. We built a platform with a real, numbered path from thirty cameras to eighty thousand, with privacy and auditability engineered in from the first line of code, not added afterward. We believe this is exactly the kind of force multiplier Gujarat Police needs — not more cameras, but making the cameras you already have finally work together, in real time. Thank you — we'd be glad to take your questions, and to run the live demo right now if you'd like to see it in action."

---
*End of presentation_deck.md*
