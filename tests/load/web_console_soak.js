// k6 4-hour soak test for NiceGUI dashboard
// Focus: memory leaks, WS stability, long-lived sessions

import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep, fail } from 'k6';

export const options = {
  stages: [
    { duration: '10m', target: 50 },
    { duration: '3h30m', target: 50 },
    { duration: '20m', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    ws_connecting: ['p(95)<100'],
  },
};

const jar = http.cookieJar();

export default function () {
  const baseUrl = __ENV.BASE_URL;
  const wsUrl = __ENV.WS_URL;
  const username = __ENV.LOAD_TEST_USERNAME || 'loadtest';
  const password = __ENV.LOAD_TEST_PASSWORD || '';

  if (!baseUrl || !wsUrl) {
    fail('BASE_URL and WS_URL must be set');
  }

  const loginRes = http.post(
    `${baseUrl}/auth/login`,
    JSON.stringify({ username, password }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(loginRes, { 'login success': (r) => r.status === 200 });

  const cookies = jar.cookiesForURL(`${baseUrl}/`);
  const sessionCookieValue = (cookies['nicegui-session'] || cookies['session'])?.[0]?.value;
  if (!sessionCookieValue) {
    console.error('FATAL: Session cookie not found - aborting iteration');
    fail('Session cookie required for authenticated WebSocket testing');
  }

  const dashRes = http.get(`${baseUrl}/`);
  check(dashRes, { 'dashboard loads': (r) => r.status === 200 });

  const wsParams = {
    headers: {
      Cookie: `nicegui-session=${sessionCookieValue}`,
    },
  };

  const res = ws.connect(`${wsUrl}/_nicegui_ws`, wsParams, (socket) => {
    socket.on('open', () => console.log('WS connected'));
    socket.on('message', (data) => console.log('WS message'));
    // Keep connection alive longer for soak
    sleep(300);
    socket.close();
  });
  check(res, { 'ws connected': (r) => r && r.status === 101 });

  sleep(5);
}
