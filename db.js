// db.js — WhatsMesh Relay Sunucusu veri erişim katmanı.
// SQLite (better-sqlite3) kullanır; Postgres'e geçiş için sadece bu dosyanın
// değişmesi yeterlidir (query arayüzü aynı kalacak şekilde tasarlandı).

const path = require('path');
const fs = require('fs');
const Database = require('better-sqlite3');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, '..', 'data', 'whatsmesh.db');
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

const schema = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
db.exec(schema);

function now() {
  return Date.now();
}

// ---------- Kullanıcılar ----------
const upsertUser = db.prepare(`
  INSERT INTO users (id, username, public_key, display_name, avatar_url, last_seen, created_at)
  VALUES (@id, @username, @publicKey, @displayName, @avatarUrl, @lastSeen, @createdAt)
  ON CONFLICT(id) DO UPDATE SET
    display_name = excluded.display_name,
    avatar_url = excluded.avatar_url,
    last_seen = excluded.last_seen
`);

function createOrUpdateUser(user) {
  upsertUser.run({
    id: user.id,
    username: user.username,
    publicKey: user.publicKey,
    displayName: user.displayName || null,
    avatarUrl: user.avatarUrl || null,
    lastSeen: now(),
    createdAt: user.createdAt || now(),
  });
  return getUserById(user.id);
}

function getUserById(id) {
  return db.prepare('SELECT * FROM users WHERE id = ?').get(id);
}

function getUserByUsername(username) {
  return db.prepare('SELECT * FROM users WHERE username = ?').get(username.replace(/^@/, ''));
}

function searchUsers(query) {
  const like = `%${query.replace(/^@/, '')}%`;
  return db.prepare('SELECT id, username, display_name, avatar_url FROM users WHERE username LIKE ? LIMIT 20').all(like);
}

function touchLastSeen(userId) {
  db.prepare('UPDATE users SET last_seen = ? WHERE id = ?').run(now(), userId);
}

// ---------- Arkadaşlık ----------
const insertFriendRequest = db.prepare(`
  INSERT INTO friend_requests (id, from_user_id, to_user_id, status, origin, created_at)
  VALUES (@id, @fromUserId, @toUserId, 'pending', @origin, @createdAt)
  ON CONFLICT(from_user_id, to_user_id) DO UPDATE SET status = 'pending', created_at = excluded.created_at
`);

function createFriendRequest({ id, fromUserId, toUserId, origin }) {
  insertFriendRequest.run({ id, fromUserId, toUserId, origin: origin || 'internet', createdAt: now() });
  return db.prepare('SELECT * FROM friend_requests WHERE id = ?').get(id);
}

const acceptFriendTx = db.transaction((requestId) => {
  const reqRow = db.prepare('SELECT * FROM friend_requests WHERE id = ?').get(requestId);
  if (!reqRow) throw new Error('Friend request not found');
  db.prepare('UPDATE friend_requests SET status = ?, resolved_at = ? WHERE id = ?')
    .run('accepted', now(), requestId);
  const insertFriendship = db.prepare(`
    INSERT OR IGNORE INTO friendships (user_id, friend_id, created_at) VALUES (?, ?, ?)
  `);
  insertFriendship.run(reqRow.from_user_id, reqRow.to_user_id, now());
  insertFriendship.run(reqRow.to_user_id, reqRow.from_user_id, now());
  return reqRow;
});

function acceptFriendRequest(requestId) {
  return acceptFriendTx(requestId);
}

function rejectFriendRequest(requestId) {
  db.prepare('UPDATE friend_requests SET status = ?, resolved_at = ? WHERE id = ?')
    .run('rejected', now(), requestId);
}

function listFriends(userId) {
  return db.prepare(`
    SELECT u.id, u.username, u.display_name, u.avatar_url, u.last_seen
    FROM friendships f JOIN users u ON u.id = f.friend_id
    WHERE f.user_id = ?
  `).all(userId);
}

function listIncomingRequests(userId) {
  return db.prepare(`
    SELECT fr.*, u.username as from_username FROM friend_requests fr
    JOIN users u ON u.id = fr.from_user_id
    WHERE fr.to_user_id = ? AND fr.status = 'pending'
  `).all(userId);
}

// ---------- Gruplar ----------
const insertGroup = db.prepare(`
  INSERT INTO groups (id, name, avatar_url, owner_id, created_at) VALUES (@id, @name, @avatarUrl, @ownerId, @createdAt)
`);
const insertMember = db.prepare(`
  INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_at) VALUES (@groupId, @userId, @role, @joinedAt)
`);

const createGroupTx = db.transaction((group) => {
  insertGroup.run({
    id: group.id, name: group.name, avatarUrl: group.avatarUrl || null,
    ownerId: group.ownerId, createdAt: now(),
  });
  insertMember.run({ groupId: group.id, userId: group.ownerId, role: 'owner', joinedAt: now() });
  for (const memberId of group.memberIds || []) {
    if (memberId === group.ownerId) continue;
    insertMember.run({ groupId: group.id, userId: memberId, role: 'member', joinedAt: now() });
  }
  return group.id;
});

function createGroup(group) {
  const groupId = createGroupTx(group);
  return getGroup(groupId);
}

function getGroup(groupId) {
  const group = db.prepare('SELECT * FROM groups WHERE id = ?').get(groupId);
  if (!group) return null;
  group.members = db.prepare(`
    SELECT u.id, u.username, gm.role FROM group_members gm
    JOIN users u ON u.id = gm.user_id WHERE gm.group_id = ?
  `).all(groupId);
  return group;
}

function addGroupMember(groupId, userId, role = 'member') {
  insertMember.run({ groupId, userId, role, joinedAt: now() });
}

function removeGroupMember(groupId, userId) {
  db.prepare('DELETE FROM group_members WHERE group_id = ? AND user_id = ?').run(groupId, userId);
}

function listUserGroups(userId) {
  return db.prepare(`
    SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.user_id = ?
  `).all(userId);
}

function getGroupMemberIds(groupId) {
  return db.prepare('SELECT user_id FROM group_members WHERE group_id = ?').all(groupId).map(r => r.user_id);
}

// ---------- Mesajlar / Bekleyen teslimatlar ----------
const insertMessage = db.prepare(`
  INSERT OR IGNORE INTO messages (id, sender_id, receiver_id, group_id, packet_type, total_chunks, origin_channel, created_at, delivered)
  VALUES (@id, @senderId, @receiverId, @groupId, @packetType, @totalChunks, @originChannel, @createdAt, @delivered)
`);

function recordMessage(msg) {
  insertMessage.run({
    id: msg.id,
    senderId: msg.senderId,
    receiverId: msg.receiverId || null,
    groupId: msg.groupId || null,
    packetType: msg.packetType,
    totalChunks: msg.totalChunks || 1,
    originChannel: msg.originChannel || 'internet',
    createdAt: now(),
    delivered: msg.delivered ? 1 : 0,
  });
}

function queuePendingDelivery({ id, targetUserId, messageId, payload }) {
  db.prepare(`
    INSERT INTO pending_delivery (id, target_user_id, message_id, payload, created_at)
    VALUES (?, ?, ?, ?, ?)
  `).run(id, targetUserId, messageId, payload, now());
}

function popPendingDeliveries(targetUserId) {
  const rows = db.prepare('SELECT * FROM pending_delivery WHERE target_user_id = ? ORDER BY created_at ASC').all(targetUserId);
  db.prepare('DELETE FROM pending_delivery WHERE target_user_id = ?').run(targetUserId);
  return rows;
}

module.exports = {
  createOrUpdateUser, getUserById, getUserByUsername, searchUsers, touchLastSeen,
  createFriendRequest, acceptFriendRequest, rejectFriendRequest, listFriends, listIncomingRequests,
  createGroup, getGroup, addGroupMember, removeGroupMember, listUserGroups, getGroupMemberIds,
  recordMessage, queuePendingDelivery, popPendingDeliveries,
};
