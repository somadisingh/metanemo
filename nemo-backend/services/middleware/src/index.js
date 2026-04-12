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
  'NEMOCLAW_PORT'
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
 * Create and configure Express app.
 */
function createApp() {
  const app = express();
  
  app.use(express.json());
  

  // Admin: manually trigger proactive alert to all connected clients
  app.post('/admin/alert', async (req, res) => {
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
  wss = new WebSocketServer({ 
    server,
    path: '/ws'
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
