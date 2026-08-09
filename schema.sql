-- WhatsMesh Relay Sunucusu — Veritabanı Şeması (SQLite / Postgres uyumlu)

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,        -- UUID
    username        TEXT UNIQUE NOT NULL,    -- "@kullanici_adi"
    public_key      TEXT NOT NULL,           -- E2E şifreleme public key (base64)
    display_name    TEXT,
    avatar_url      TEXT,
    last_seen       INTEGER,                 -- epoch ms
    created_at      INTEGER NOT NULL
);

-- Arkadaşlık istekleri (pending / accepted / rejected)
CREATE TABLE IF NOT EXISTS friend_requests (
    id              TEXT PRIMARY KEY,
    from_user_id    TEXT NOT NULL REFERENCES users(id),
    to_user_id      TEXT NOT NULL REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | rejected
    origin          TEXT NOT NULL DEFAULT 'internet', -- internet | bluetooth
    created_at      INTEGER NOT NULL,
    resolved_at     INTEGER,
    UNIQUE(from_user_id, to_user_id)
);

-- Onaylanmış arkadaşlıklar (çift yönlü kayıt kolaylığı için ayrı tablo)
CREATE TABLE IF NOT EXISTS friendships (
    user_id         TEXT NOT NULL REFERENCES users(id),
    friend_id       TEXT NOT NULL REFERENCES users(id),
    created_at      INTEGER NOT NULL,
    PRIMARY KEY (user_id, friend_id)
);

CREATE TABLE IF NOT EXISTS groups (
    id              TEXT PRIMARY KEY,        -- Group_ID (UUID)
    name            TEXT NOT NULL,
    avatar_url      TEXT,
    owner_id        TEXT NOT NULL REFERENCES users(id),
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id        TEXT NOT NULL REFERENCES groups(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    role            TEXT NOT NULL DEFAULT 'member', -- owner | admin | member
    joined_at       INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

-- Mesaj meta verisi (içerik uçtan uca şifreli tutulacağından payload opak blob'dur)
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,        -- messageId (client üretir, idempotency için)
    sender_id       TEXT NOT NULL REFERENCES users(id),
    receiver_id     TEXT,                    -- 1'e1 sohbette hedef user id
    group_id        TEXT,                    -- grup sohbetinde grup id
    packet_type     TEXT NOT NULL,           -- TEXT | MEDIA_CHUNK | STICKER | FRIEND_REQUEST ...
    total_chunks    INTEGER DEFAULT 1,
    origin_channel  TEXT NOT NULL DEFAULT 'internet', -- internet | bluetooth
    created_at      INTEGER NOT NULL,
    delivered       INTEGER NOT NULL DEFAULT 0
);

-- Offline kullanıcılar bağlandığında teslim edilecek bekleyen paketler
CREATE TABLE IF NOT EXISTS pending_delivery (
    id              TEXT PRIMARY KEY,
    target_user_id  TEXT NOT NULL REFERENCES users(id),
    message_id      TEXT NOT NULL REFERENCES messages(id),
    payload         TEXT NOT NULL,           -- serileştirilmiş MeshPacket (JSON/Base64)
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_target ON pending_delivery(target_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id);
CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id);
