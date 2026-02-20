/**
 * Mordomo HA - Baileys WhatsApp Bridge
 * 
 * Direct WhatsApp Web connection using Baileys (same approach as OpenClaw).
 * No external gateways needed - just scan QR code and go.
 * 
 * Runs as a Node.js subprocess managed by the HA component.
 * Communicates with HA via HTTP webhook callbacks.
 */

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, makeCacheableSignalKeyStore } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const pino = require('pino');
const fs = require('fs');
const path = require('path');
const http = require('http');
const QRCode = require('qrcode');

// ── Configuration ──
const CONFIG = {
  authDir: process.env.MORDOMO_AUTH_DIR || path.join(__dirname, 'auth'),
  webhookUrl: process.env.MORDOMO_WEBHOOK_URL || 'http://localhost:8123/api/webhook/mordomo_ha',
  httpPort: parseInt(process.env.MORDOMO_BRIDGE_PORT || '3781'),
  haToken: process.env.MORDOMO_HA_TOKEN || '',
  logLevel: process.env.MORDOMO_LOG_LEVEL || 'warn',
};

// ── State ──
let sock = null;
let qrCode = null;
let qrBase64 = null;
let connectionStatus = 'disconnected'; // disconnected, qr_ready, connecting, connected
let lastError = null;
let messageCount = { in: 0, out: 0 };

// ── Logger ──
const logger = pino({ level: CONFIG.logLevel });

// ── Ensure auth directory exists ──
if (!fs.existsSync(CONFIG.authDir)) {
  fs.mkdirSync(CONFIG.authDir, { recursive: true });
}

// ── WhatsApp Connection ──
async function startWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(CONFIG.authDir);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    printQRInTerminal: true,
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  // ── Connection Events ──
  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      // New QR code to scan
      qrCode = qr;
      connectionStatus = 'qr_ready';
      lastError = null;

      // Generate base64 QR image
      try {
        qrBase64 = await QRCode.toDataURL(qr, { width: 300, margin: 2 });
      } catch (err) {
        logger.error('QR generation error:', err);
      }

      console.log('\n╔══════════════════════════════════════╗');
      console.log('║   🏠 MORDOMO HA - WhatsApp Setup     ║');
      console.log('║                                      ║');
      console.log('║   Scan the QR code with WhatsApp:    ║');
      console.log('║   Settings > Linked Devices > Link   ║');
      console.log('╚══════════════════════════════════════╝\n');
    }

    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      lastError = lastDisconnect?.error?.message || 'Unknown error';

      if (reason === DisconnectReason.loggedOut) {
        console.log('⛔ WhatsApp logged out. Clear auth and re-scan.');
        connectionStatus = 'disconnected';
        // Clear auth state
        if (fs.existsSync(CONFIG.authDir)) {
          fs.rmSync(CONFIG.authDir, { recursive: true, force: true });
          fs.mkdirSync(CONFIG.authDir, { recursive: true });
        }
        // Restart to show new QR
        setTimeout(startWhatsApp, 3000);
      } else if (reason === DisconnectReason.restartRequired) {
        console.log('🔄 Restart required, reconnecting...');
        startWhatsApp();
      } else {
        console.log(`⚠️ Connection closed (${reason}): ${lastError}. Reconnecting in 5s...`);
        connectionStatus = 'disconnected';
        setTimeout(startWhatsApp, 5000);
      }
    } else if (connection === 'connecting') {
      connectionStatus = 'connecting';
      console.log('🔄 Connecting to WhatsApp...');
    } else if (connection === 'open') {
      connectionStatus = 'connected';
      qrCode = null;
      qrBase64 = null;
      lastError = null;
      console.log('✅ WhatsApp connected successfully!');
    }
  });

  // ── Save credentials on update ──
  sock.ev.on('creds.update', saveCreds);

  // ── Incoming Messages ──
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      // Skip own messages
      if (msg.key.fromMe) continue;

      // Skip status broadcasts
      if (msg.key.remoteJid === 'status@broadcast') continue;

      // Extract text
      const text = msg.message?.conversation
        || msg.message?.extendedTextMessage?.text
        || msg.message?.imageMessage?.caption
        || msg.message?.videoMessage?.caption
        || '';

      if (!text) continue;

      // Extract sender
      const remoteJid = msg.key.remoteJid;
      const sender = remoteJid.replace('@s.whatsapp.net', '').replace('@g.us', '');
      const isGroup = remoteJid.endsWith('@g.us');
      const participant = isGroup ? (msg.key.participant || '').replace('@s.whatsapp.net', '') : sender;

      messageCount.in++;

      console.log(`📩 Message from ${participant}: ${text.substring(0, 80)}`);

      // Send optional read receipt
      try {
        await sock.readMessages([msg.key]);
      } catch (e) { /* ignore */ }

      // Send optional reaction to acknowledge
      try {
        await sock.sendMessage(remoteJid, {
          react: { text: '👀', key: msg.key }
        });
      } catch (e) { /* ignore */ }

      // Forward to Home Assistant webhook
      await forwardToHA({
        from: participant,
        message: text,
        type: 'text',
        isGroup,
        groupId: isGroup ? sender : null,
        remoteJid,
        messageId: msg.key.id,
        timestamp: msg.messageTimestamp,
      });
    }
  });
}

// ── Forward message to HA ──
async function forwardToHA(data) {
  try {
    const payload = JSON.stringify(data);
    const url = new URL(CONFIG.webhookUrl);

    const options = {
      hostname: url.hostname,
      port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    };

    if (CONFIG.haToken) {
      options.headers['Authorization'] = `Bearer ${CONFIG.haToken}`;
    }

    return new Promise((resolve, reject) => {
      const proto = url.protocol === 'https:' ? require('https') : http;
      const req = proto.request(options, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => resolve(body));
      });
      req.on('error', (err) => {
        logger.error('Webhook forward error:', err.message);
        reject(err);
      });
      req.write(payload);
      req.end();
    });
  } catch (err) {
    logger.error('Forward to HA failed:', err);
  }
}

// ── Send message via WhatsApp ──
async function sendMessage(to, text) {
  if (!sock || connectionStatus !== 'connected') {
    throw new Error('WhatsApp not connected');
  }

  // Ensure JID format
  let jid = to;
  if (!jid.includes('@')) {
    jid = `${to.replace('+', '')}@s.whatsapp.net`;
  }

  // Split long messages
  const MAX_LEN = 4000;
  const parts = [];
  for (let i = 0; i < text.length; i += MAX_LEN) {
    parts.push(text.substring(i, i + MAX_LEN));
  }

  for (const part of parts) {
    await sock.sendMessage(jid, { text: part });
  }

  messageCount.out++;
  console.log(`📤 Sent to ${to}: ${text.substring(0, 80)}`);
}

// ── Send image via WhatsApp ──
async function sendImage(to, imageUrl, caption = '') {
  if (!sock || connectionStatus !== 'connected') {
    throw new Error('WhatsApp not connected');
  }

  let jid = to;
  if (!jid.includes('@')) {
    jid = `${to.replace('+', '')}@s.whatsapp.net`;
  }

  await sock.sendMessage(jid, {
    image: { url: imageUrl },
    caption: caption,
  });
}

// ── HTTP API Server (for HA to communicate with the bridge) ──
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${CONFIG.httpPort}`);
  
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  
  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  const json = (data, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(data));
  };

  try {
    // ── GET /status ──
    if (req.method === 'GET' && url.pathname === '/status') {
      json({
        status: connectionStatus,
        error: lastError,
        messages: messageCount,
        uptime: process.uptime(),
      });
    }

    // ── GET /qr ──
    else if (req.method === 'GET' && url.pathname === '/qr') {
      if (connectionStatus === 'connected') {
        json({ status: 'connected', qr: null, qr_base64: null });
      } else if (qrBase64) {
        json({ status: 'qr_ready', qr: qrCode, qr_base64: qrBase64 });
      } else {
        json({ status: connectionStatus, qr: null, qr_base64: null });
      }
    }

    // ── POST /send ──
    else if (req.method === 'POST' && url.pathname === '/send') {
      let body = '';
      req.on('data', (chunk) => body += chunk);
      req.on('end', async () => {
        try {
          const data = JSON.parse(body);
          await sendMessage(data.to, data.message);
          json({ success: true });
        } catch (err) {
          json({ error: err.message }, 500);
        }
      });
      return; // Don't end yet, waiting for body
    }

    // ── POST /send-image ──
    else if (req.method === 'POST' && url.pathname === '/send-image') {
      let body = '';
      req.on('data', (chunk) => body += chunk);
      req.on('end', async () => {
        try {
          const data = JSON.parse(body);
          await sendImage(data.to, data.image_url, data.caption || '');
          json({ success: true });
        } catch (err) {
          json({ error: err.message }, 500);
        }
      });
      return;
    }

    // ── POST /logout ──
    else if (req.method === 'POST' && url.pathname === '/logout') {
      try {
        if (sock) {
          await sock.logout();
        }
        // Clear auth
        if (fs.existsSync(CONFIG.authDir)) {
          fs.rmSync(CONFIG.authDir, { recursive: true, force: true });
          fs.mkdirSync(CONFIG.authDir, { recursive: true });
        }
        connectionStatus = 'disconnected';
        qrCode = null;
        qrBase64 = null;
        json({ success: true, message: 'Logged out. Restart bridge to re-pair.' });
        // Restart connection to show new QR
        setTimeout(startWhatsApp, 2000);
      } catch (err) {
        json({ error: err.message }, 500);
      }
    }

    // ── GET /health ──
    else if (req.method === 'GET' && url.pathname === '/health') {
      json({ ok: true, pid: process.pid });
    }

    // ── 404 ──
    else {
      json({ error: 'Not found' }, 404);
    }
  } catch (err) {
    json({ error: err.message }, 500);
  }
});

// ── Start ──
server.listen(CONFIG.httpPort, '0.0.0.0', () => {
  console.log(`\n🏠 Mordomo HA - Baileys Bridge`);
  console.log(`   HTTP API: http://0.0.0.0:${CONFIG.httpPort}`);
  console.log(`   Webhook:  ${CONFIG.webhookUrl}`);
  console.log(`   Auth dir: ${CONFIG.authDir}\n`);
  
  startWhatsApp();
});

// ── Graceful shutdown ──
process.on('SIGINT', () => {
  console.log('\n👋 Shutting down Mordomo bridge...');
  if (sock) sock.end();
  server.close();
  process.exit(0);
});

process.on('SIGTERM', () => {
  if (sock) sock.end();
  server.close();
  process.exit(0);
});
