// server.js — WhatsMesh Relay / Gateway Sunucusu (Render'a deploy edilir)
//
// Görevi: Mesaj İÇERİĞİNİ yorumlamaz (uçtan uca şifreli kabul edilir), sadece
// MeshPacket'leri receiverId / groupId'ye göre bağlı Socket.IO istemcilerine iletir.
// Hedef anlık çevrimiçi değilse `pending_delivery` tablosuna kuyruklar ve
// kullanıcı tekrar bağlandığında flush eder.
//
// Aynı zamanda "İnternet Köprüsü (Proxy Gateway)" akışının sunucu tarafı ayağıdır:
// internetli bir mobil istemci, Bluetooth mesh'ten aldığı ve kendisine ait olmayan
// bir paketi bu sunucuya `bridge:forward` event'i ile gönderir; sunucu paketi
// normal akışa sokup gerçek hedefe iletir.

const path = require('path');
const http = require('http');
const express = require('express');
const cors = require('cors');
const { Server } = require('socket.io');
const { v4: uuidv4 } = require('uuid');
const db = require('./db');

const app = express();
app.use(cors());
app.use(express.json({ limit: '2mb' })); // chunk payload'ları için makul limit

const server = http.createServer(app);
const io = new Server(server, { cors: { origin: '*' } });

// userId -> Set<socket.id>  (aynı kullanıcı birden fazla cihazdan bağlanabilir)
const onlineSockets = new Map();

function registerSocket(userId, socketId) {
  if (!onlineSockets.has(userId)) onlineSockets.set(userId, new Set());
  onlineSockets.get(userId).add(socketId);
}

function unregisterSocket(userId, socketId) {
  const set = onlineSockets.get(userId);
  if (!set) return;
  set.delete(socketId);
  if (set.size === 0) onlineSockets.delete(userId);
}

function isOnline(userId) {
  return onlineSockets.has(userId);
}

function emitToUser(userId, event, payload) {
  const set = onlineSockets.get(userId);
  if (!set) return false;
  for (const socketId of set) io.to(socketId).emit(event, payload);
  return true;
}

// ---------------- REST: Kullanıcı / Arkadaşlık / Grup yönetimi ----------------

app.post('/api/users', (req, res) => {
  const { id, username, publicKey, displayName, avatarUrl } = req.body;
  if (!id || !username || !publicKey) return res.status(400).json({ error: 'id, username, publicKey zorunlu' });
  if (db.getUserByUsername(username) && db.getUserByUsername(username).id !== id) {
    return res.status(409).json({ error: 'username kullanımda' });
  }
  const user = db.createOrUpdateUser({ id, username, publicKey, displayName, avatarUrl });
  res.json(user);
});

app.get('/api/users/search', (req, res) => {
  const q = String(req.query.q || '');
  if (!q) return res.json([]);
  res.json(db.searchUsers(q));
});

app.get('/api/users/:username', (req, res) => {
  const user = db.getUserByUsername(req.params.username);
  if (!user) return res.status(404).json({ error: 'bulunamadı' });
  res.json(user);
});

app.post('/api/friend-requests', (req, res) => {
  const { fromUserId, toUsername, origin } = req.body;
  const toUser = db.getUserByUsername(toUsername);
  if (!toUser) return res.status(404).json({ error: 'kullanıcı bulunamadı' });
  const request = db.createFriendRequest({ id: uuidv4(), fromUserId, toUserId: toUser.id, origin });
  emitToUser(toUser.id, 'friend:request', request);
  res.json(request);
});

app.post('/api/friend-requests/:id/accept', (req, res) => {
  const request = db.acceptFriendRequest(req.params.id);
  emitToUser(request.from_user_id, 'friend:accepted', { requestId: request.id, byUserId: request.to_user_id });
  res.json({ ok: true });
});

app.post('/api/friend-requests/:id/reject', (req, res) => {
  db.rejectFriendRequest(req.params.id);
  res.json({ ok: true });
});

app.get('/api/users/:userId/friends', (req, res) => {
  res.json(db.listFriends(req.params.userId));
});

app.get('/api/users/:userId/friend-requests', (req, res) => {
  res.json(db.listIncomingRequests(req.params.userId));
});

app.post('/api/groups', (req, res) => {
  const { name, ownerId, memberIds, avatarUrl } = req.body;
  const group = db.createGroup({ id: uuidv4(), name, ownerId, memberIds, avatarUrl });
  for (const memberId of group.members.map(m => m.id)) {
    if (memberId === ownerId) continue;
    emitToUser(memberId, 'group:added', group);
  }
  res.json(group);
});

app.get('/api/groups/:groupId', (req, res) => {
  const group = db.getGroup(req.params.groupId);
  if (!group) return res.status(404).json({ error: 'bulunamadı' });
  res.json(group);
});

app.get('/api/users/:userId/groups', (req, res) => {
  res.json(db.listUserGroups(req.params.userId));
});

app.get('/healthz', (_req, res) => res.send('ok'));

// ---------------- Socket.IO: gerçek zamanlı mesaj relay ----------------

io.on('connection', (socket) => {
  let currentUserId = null;

  socket.on('auth', ({ userId }) => {
    currentUserId = userId;
    registerSocket(userId, socket.id);
    db.touchLastSeen(userId);
    // Bağlanır bağlanmaz bekleyen (offline iken kuyruklanmış) paketleri boşalt
    const pending = db.popPendingDeliveries(userId);
    for (const row of pending) {
      socket.emit('packet', JSON.parse(row.payload));
    }
    socket.emit('auth:ok', { pendingCount: pending.length });
  });

  // Ana relay girişi: hem 1'e1 hem grup paketleri, hem de metin/medya-chunk/sticker
  // hepsi tek bir MeshPacket zarfı olarak buraya gelir.
  socket.on('packet', (packet) => {
    handleIncomingPacket(packet, socket);
  });

  // Bluetooth mesh'ten toplanmış ve internetli bu cihaz tarafından köprülenen paket
  socket.on('bridge:forward', (packet) => {
    handleIncomingPacket(packet, socket, { viaGateway: true });
  });

  socket.on('disconnect', () => {
    if (currentUserId) unregisterSocket(currentUserId, socket.id);
  });
});

function handleIncomingPacket(packet, senderSocket, opts = {}) {
  if (!packet || !packet.messageId || !packet.senderId) return;

  db.recordMessage({
    id: packet.messageId,
    senderId: packet.senderId,
    receiverId: packet.isGroup ? null : packet.receiverId,
    groupId: packet.isGroup ? packet.receiverId : null,
    packetType: packet.packetType,
    totalChunks: packet.totalChunks,
    originChannel: opts.viaGateway ? 'bluetooth' : 'internet',
    delivered: false,
  });

  if (packet.isGroup) {
    const memberIds = db.getGroupMemberIds(packet.receiverId);
    for (const memberId of memberIds) {
      if (memberId === packet.senderId) continue;
      deliverOrQueue(memberId, packet);
    }
  } else {
    deliverOrQueue(packet.receiverId, packet);
  }

  // Gönderene teslim onayı (ACK) — kendi UI'ında "sunucuya ulaştı" tiki için
  senderSocket.emit('packet:ack', { messageId: packet.messageId, chunkIndex: packet.chunkIndex });
}

function deliverOrQueue(targetUserId, packet) {
  const delivered = isOnline(targetUserId) && emitToUser(targetUserId, 'packet', packet);
  if (!delivered) {
    db.queuePendingDelivery({
      id: uuidv4(),
      targetUserId,
      messageId: packet.messageId,
      payload: JSON.stringify(packet),
    });
  }
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`WhatsMesh relay sunucusu ${PORT} portunda çalışıyor`));
