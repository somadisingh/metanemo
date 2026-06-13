/**
 * pseudoMetaGlass Middleware Server
 * 
 * WebSocket server for AR glasses communication.
 * Binds to Tailscale interface for secure VPN-only access.
 * 
 * Property 1: Concurrent WebSocket Connections
 * Property 33: Missing Environment Variable Causes Exit
 */

import 'dotenv/config';
import express from 'express';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';

import logger from './logger.js';
import { handleConnection, wsConnections, clientState } from './wsHandler.js';
import { getSituationReport } from './nemoClawClient.js';
import { isSuppressed, record } from './cooldownCache.js';
import { healthCheck as nemoClawHealthCheck } from './nemoClawClient.js';

// Required environment variables (Property 33)
const REQUIRED_ENV = [
  'TAILSCALE_GN100_IP',
  'TAILSCALE_GN100_PORT',
  'NEMOCLAW_HOST',
  'NEMOCLAW_PORT',
  'WS_AUTH_TOKEN'
];

/**
 * Validate required environment variables.
 * Exits with non-zero code if any are missing (Property 33).
 */
function validateEnvironment() {
  const missing = REQUIRED_ENV.filter(v => !process.env[v]);
  
  if (missing.length > 0) {
    logger.fatal({
      msg: 'Missing required environment variables',
      missing
    });
    process.exit(1);
  }
}

/**
 * Authenticate admin requests.
 * Requires ADMIN_API_KEY env var to be set and a matching
 * Authorization: Bearer <key> header on the request.
 */
function requireAdminAuth(req, res, next) {
  const adminKey = process.env.ADMIN_API_KEY;

  // If no admin key is configured, the admin endpoint is disabled entirely
  if (!adminKey) {
    return res.status(403).json({ error: 'Admin endpoint is disabled (ADMIN_API_KEY not configured)' });
  }

  const authHeader = req.headers['authorization'];
  if (!authHeader) {
    return res.status(401).json({ error: 'Missing Authorization header' });
  }

  const parts = authHeader.split(' ');
  if (parts.length !== 2 || parts[0] !== 'Bearer') {
    return res.status(401).json({ error: 'Invalid Authorization header format. Expected: Bearer <token>' });
  }

  const token = parts[1];

  // Constant-time comparison to prevent timing attacks
  if (token.length !== adminKey.length) {
    return res.status(401).json({ error: 'Invalid admin API key' });
  }

  let mismatch = 0;
  for (let i = 0; i < token.length; i++) {
    mismatch |= token.charCodeAt(i) ^ adminKey.charCodeAt(i);
  }

  if (mismatch !== 0) {
    return res.status(401).json({ error: 'Invalid admin API key' });
  }

  next();
}

/**
 * Create and configure Express app.
 */
function createApp() {
  const app = express();
  
  app.use(express.json());
  

  // Admin: manually trigger proactive alert to all connected clients
  app.post('/admin/alert', requireAdminAuth, async (req, res) => {
    const pushed = [];
    for (const [clientId, ws] of wsConnections.entries()) {
      if (ws.readyState !== ws.OPEN) continue;
      const state = clientState.get(clientId);
      if (!state) continue;
      try {
        const report = await getSituationReport(state.latitude, state.longitude);
        if (!report) continue;
        const bullets = [];
        for (const h of (report.top_hazards || []).slice(0, 6)) {
          const id = h.id || (h.type + '_' + (h.description || '').slice(0, 40));
          if (isSuppressed(id)) continue;
          bullets.push((h.description || h.type || 'Alert').slice(0, 80));
          record(id);
        }
        for (const a of (report.alerts || []).slice(0, 3)) {
          if (a.severity === 'high' || a.severity === 'medium') {
            bullets.push((a.message || a.type || '').slice(0, 80));
          }
        }
        const collisions = report.summary?.collision_hotspots || 0;
        if (collisions > 0) {
          bullets.push(collisions + ' pedestrian collision hotspot(s) nearby');
        }
        if (bullets.length === 0) {
          pushed.push({ clientId, status: 'no_new_alerts' });
          continue;
        }
        ws.send(JSON.stringify({
          serverContent: {
            proactiveAlert: {
              bullets,
              safetyScore: report.safety_score,
              safetyLevel: report.safety_level,
              nearbyTransit: report.nearby_transit?.routes || []
            }
          }
        }));
        pushed.push({ clientId, bulletCount: bullets.length, safetyScore: report.safety_score });
        logger.info({ msg: 'Admin alert pushed', clientId, bulletCount: bullets.length });
      } catch (err) {
        logger.error({ msg: 'Admin alert error', clientId, error: err.message });
        pushed.push({ clientId, status: 'error', error: err.message });
      }
    }
    res.json({ pushed, totalClients: wsConnections.size });
  });

  // Health check endpoint
  app.get('/health', async (req, res) => {
    const nemoClawOk = await nemoClawHealthCheck();
    
    res.json({
      status: nemoClawOk ? 'healthy' : 'degraded',
      service: 'middleware',
      timestamp: new Date().toISOString(),
      dependencies: {
        nemoclaw: nemoClawOk ? 'healthy' : 'unhealthy'
      }
    });
  });
  
  // Metrics endpoint (for hackathon dashboard)
  app.get('/metrics', (req, res) => {
    res.json({
      connections: wss ? wss.clients.size : 0,
      uptime: process.uptime()
    });
  });
  
  return app;
}

// WebSocket server reference
let wss = null;

/**
 * Start the middleware server.
 */
async function start() {
  logger.info('=' .repeat(60));
  logger.info('pseudoMetaGlass Middleware Starting');
  logger.info('=' .repeat(60));
  
  // Validate environment
  validateEnvironment();
  
  const HOST = process.env.TAILSCALE_GN100_IP;
  const PORT = parseInt(process.env.TAILSCALE_GN100_PORT || '8080');
  
  // Create Express app and HTTP server
  const app = createApp();
  const server = createServer(app);
  
  // Create WebSocket server (Property 1: handles concurrent connections)
  // verifyClient enforces authentication on WebSocket upgrade requests (CWE-306/CWE-862)
  wss = new WebSocketServer({ 
    server,
    path: '/ws',
    verifyClient: (info, callback) => {
      const authToken = process.env.WS_AUTH_TOKEN;

      // Fail-closed: if no auth token is configured, reject all connections
      if (!authToken) {
        logger.warn({ msg: 'WS connection rejected — WS_AUTH_TOKEN not configured' });
        callback(false, 401, 'Unauthorized');
        return;
      }

      // Extract token from Authorization header or query parameter
      const url = new URL(info.req.url, `http://${info.req.headers.host || 'localhost'}`);
      const queryToken = url.searchParams.get('token');
      const authHeader = info.req.headers['authorization'];
      let providedToken = null;

      if (authHeader && authHeader.startsWith('Bearer ')) {
        providedToken = authHeader.slice(7);
      } else if (queryToken) {
        providedToken = queryToken;
      }

      if (!providedToken || providedToken !== authToken) {
        logger.warn({
          msg: 'WS connection rejected — invalid or missing auth token',
          ip: info.req.socket.remoteAddress
        });
        callback(false, 401, 'Unauthorized');
        return;
      }

      callback(true);
    }
  });
  
  wss.on('connection', handleConnection);
  
  wss.on('error', (error) => {
    logger.error({ msg: 'WebSocket server error', error: error.message });
  });
  
  // Handle server errors
  server.on('error', (error) => {
    if (error.code === 'EADDRINUSE') {
      logger.fatal({
        msg: 'Port already in use',
        port: PORT,
        host: HOST
      });
      process.exit(1);
    }
    throw error;
  });
  
  // Start listening
  server.listen(PORT, HOST, () => {
    logger.info({
      msg: 'Middleware server started',
      host: HOST,
      port: PORT,
      wsPath: '/ws'
    });
    logger.info(`WebSocket: ws://${HOST}:${PORT}/ws`);
    logger.info(`Health: http://${HOST}:${PORT}/health`);
  });
  
  // Graceful shutdown
  const shutdown = () => {
    logger.info('Shutting down...');
    
    wss.clients.forEach(client => {
      client.close(1001, 'Server shutting down');
    });
    
    server.close(() => {
      logger.info('Server closed');
      process.exit(0);
    });
    
    // Force exit after 10 seconds
    setTimeout(() => {
      logger.warn('Forcing exit');
      process.exit(1);
    }, 10000);
  };
  
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

// Handle uncaught errors
process.on('uncaughtException', (error) => {
  logger.fatal({
    msg: 'Uncaught exception',
    error: error.message,
    stack: error.stack
  });
  process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
  logger.error({
    msg: 'Unhandled rejection',
    reason: reason?.message || reason
  });
});

// Start server
start().catch(error => {
  logger.fatal({ msg: 'Failed to start server', error: error.message });
  process.exit(1);
});
