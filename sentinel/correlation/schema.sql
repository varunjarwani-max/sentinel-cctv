-- =============================================================================
-- sentinel/correlation/schema.sql
--
-- Complete PostgreSQL DDL for the Sentinel correlation & alerting backbone.
-- Run this against a fresh database, e.g.:
--
--   psql "$DB_URL" -f sentinel/correlation/schema.sql
--
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- cameras
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    hls_url     TEXT,
    rtsp_url    TEXT,
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- watchlist
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type  TEXT NOT NULL,
    plate_text   TEXT,
    name         TEXT,
    flag_type    TEXT NOT NULL,
    description  TEXT,
    added_by     TEXT NOT NULL,
    added_at     TIMESTAMPTZ DEFAULT NOW(),
    active       BOOLEAN DEFAULT TRUE
);

-- Functional partial index accelerating exact-match plate lookups against
-- only the active vehicle-type watchlist rows (the hot path for real-time
-- correlation).
CREATE INDEX IF NOT EXISTS idx_watchlist_plate
    ON watchlist(plate_text)
    WHERE entity_type = 'VEHICLE' AND active = TRUE;

-- ---------------------------------------------------------------------------
-- detections
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS detections (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id    TEXT REFERENCES cameras(id),
    track_id     INT NOT NULL,
    class_name   TEXT NOT NULL,
    confidence   FLOAT NOT NULL,
    bbox_x1      INT,
    bbox_y1      INT,
    bbox_x2      INT,
    bbox_y2      INT,
    plate_text   TEXT,
    pts_ms       FLOAT NOT NULL,
    detected_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_detections_camera_id ON detections(camera_id);
CREATE INDEX IF NOT EXISTS idx_detections_plate_text ON detections(plate_text)
    WHERE plate_text IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_detections_detected_at ON detections(detected_at);

-- ---------------------------------------------------------------------------
-- alerts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detection_id   UUID REFERENCES detections(id),
    watchlist_id   UUID REFERENCES watchlist(id),
    camera_id      TEXT REFERENCES cameras(id),
    track_id       INT,
    plate_text     TEXT,
    flag_type      TEXT NOT NULL,
    confidence     FLOAT NOT NULL,
    alerted_at     TIMESTAMPTZ DEFAULT NOW(),
    acknowledged   BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_alerts_alerted_at ON alerts(alerted_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_camera_id ON alerts(camera_id);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged)
    WHERE acknowledged = FALSE;

-- ---------------------------------------------------------------------------
-- vehicle_tracks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicle_tracks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id        INT NOT NULL,
    camera_id       TEXT REFERENCES cameras(id),
    plate_text      TEXT NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    frame_count     INT DEFAULT 1,
    CONSTRAINT uq_track_cam_plate UNIQUE (track_id, camera_id, plate_text)
);

CREATE INDEX IF NOT EXISTS idx_vehicle_tracks_plate_text ON vehicle_tracks(plate_text);
CREATE INDEX IF NOT EXISTS idx_vehicle_tracks_last_seen_at ON vehicle_tracks(last_seen_at DESC);

-- =============================================================================
-- Seed data: watchlist entries for Gujarat Police demo scenarios.
-- =============================================================================

-- STOLEN vehicles
INSERT INTO watchlist (entity_type, plate_text, name, flag_type, description, added_by)
VALUES
    ('VEHICLE', 'GJ01AB1234', NULL, 'STOLEN', 'Reported stolen from Navrangpura, Ahmedabad on 12-Aug-2026. Silver Maruti Swift.', 'SI R. Chauhan'),
    ('VEHICLE', 'GJ05CD5678', NULL, 'STOLEN', 'Stolen two-wheeler, Honda Activa, reported at Vadodara City Police Station.', 'PSI M. Patel'),
    ('VEHICLE', 'GJ27AA9999', NULL, 'STOLEN', 'Hijacked delivery truck last seen near Surat GIDC industrial area.', 'Inspector K. Desai'),
    ('VEHICLE', 'GJ18XY4321', NULL, 'STOLEN', 'Stolen white Innova, complaint filed at Rajkot Sector-1 police station.', 'SI J. Rana'),
    ('VEHICLE', 'GJ06EF2345', NULL, 'STOLEN', 'Motorcycle theft reported near Bhavnagar bus depot on 03-Jul-2026.', 'PSI A. Solanki')
ON CONFLICT DO NOTHING;

-- WANTED vehicles / associated with wanted individuals
INSERT INTO watchlist (entity_type, plate_text, name, flag_type, description, added_by)
VALUES
    ('VEHICLE', 'GJ01ZZ0007', NULL, 'WANTED', 'Vehicle linked to absconding accused in FIR 214/2026, Ahmedabad City.', 'Inspector V. Joshi'),
    ('VEHICLE', 'GJ19AB6789', NULL, 'WANTED', 'Getaway vehicle in chain-snatching case, Surat Athwalines.', 'SI P. Vaghela'),
    ('VEHICLE', 'GJ27BB1111', NULL, 'WANTED', 'Flagged in connection with narcotics transport, Surat rural.', 'PSI D. Thakor'),
    ('VEHICLE', 'GJ23CD8888', NULL, 'WANTED', 'Registered to bail-jumping accused, non-bailable warrant issued.', 'Inspector S. Barot'),
    ('VEHICLE', 'GJ05EE3333', NULL, 'WANTED', 'Vehicle seen fleeing scene of assault, Vadodara Manjalpur.', 'SI N. Gohil')
ON CONFLICT DO NOTHING;

-- SUSPECT vehicles/individuals under active surveillance
INSERT INTO watchlist (entity_type, plate_text, name, flag_type, description, added_by)
VALUES
    ('VEHICLE', 'GJ01FG4444', NULL, 'SUSPECT', 'Under surveillance for suspected involvement in vehicle theft ring.', 'SI H. Makwana'),
    ('VEHICLE', 'GJ18HH5555', NULL, 'SUSPECT', 'Repeated presence near sensitive installations, Rajkot.', 'PSI T. Zala'),
    ('PERSON', NULL, 'Ramesh Bhai Parmar', 'SUSPECT', 'Person of interest in ongoing extortion investigation, Ahmedabad.', 'Inspector R. Chudasama'),
    ('PERSON', NULL, 'Salim Sheikh', 'SUSPECT', 'Flagged for suspected involvement in counterfeit currency case.', 'SI K. Baraiya'),
    ('VEHICLE', 'GJ06II6666', NULL, 'SUSPECT', 'Vehicle associated with suspected smuggling network, Bhavnagar coast.', 'PSI M. Chauhan')
ON CONFLICT DO NOTHING;

-- BLACKLISTED vehicles/individuals
INSERT INTO watchlist (entity_type, plate_text, name, flag_type, description, added_by)
VALUES
    ('VEHICLE', 'GJ27JJ7777', NULL, 'BLACKLISTED', 'Permit revoked; commercial vehicle banned from operating within city limits.', 'RTO Liaison Officer'),
    ('VEHICLE', 'GJ19KK8888', NULL, 'BLACKLISTED', 'Repeat traffic offender vehicle, multiple unpaid challans.', 'Traffic PI A. Rathod'),
    ('PERSON', NULL, 'Dinesh Kumar Yadav', 'BLACKLISTED', 'Barred from entering event venues following prior security breach.', 'DCP Control Room'),
    ('VEHICLE', 'GJ01LL9999', NULL, 'BLACKLISTED', 'Vehicle involved in past communal disturbance, flagged for monitoring.', 'Inspector B. Mori'),
    ('VEHICLE', 'GJ23MM0001', NULL, 'BLACKLISTED', 'Fake registration plate previously seized, watch for reappearance.', 'SI L. Vora')
ON CONFLICT DO NOTHING;
