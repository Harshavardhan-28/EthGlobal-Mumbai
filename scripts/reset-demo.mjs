// Resets the dashboard demo to Verify. Each reset uses a fresh on-chain facility key.
const api = process.env.API_URL ?? "http://localhost:8000";
const response = await fetch(`${api}/api/demo/retailco-net30/reset`, { method: "POST" });
if (!response.ok) throw new Error(`Reset failed: HTTP ${response.status}`);
const state = await response.json();
console.log(`Demo reset · lifecycle ${state.lifecycle} · ${state.modes.arcLabel} · Hedera ${state.modes.hedera}`);
