// Cloudflare Access service token for the helmet (contract D13, docs/modules/SV7).
//
// The token comes from NVS partition "jsecrets" (namespace "secrets", keys "cf_id" and
// "cf_secret", written by tools/provision_secrets.py), falling back to Kconfig
// JARVIS_CF_ACCESS_ID / JARVIS_CF_ACCESS_SECRET. The CF-Access-Client-Id/-Secret headers
// are registered in the core ExtraHeaders registry only for the host of the OTA URL and
// of the websocket URL handed out by the OTA server, and only when that URL uses TLS
// (https:// or wss://). The secret is never logged.
#pragma once

namespace cf_access {

// Loads the token and registers the headers for the current hosts. Call once from the
// board constructor (internal-RAM stack). Returns true when a token is configured.
bool Init();

// Re-reads the OTA URL (NVS wifi/ota_url or CONFIG_OTA_URL) and the websocket URL (NVS
// websocket/url) and moves the headers to their hosts. Cheap no-op without a token. Call
// after Wi-Fi connects and before every server connection attempt.
void RefreshHosts();

bool IsConfigured();

}  // namespace cf_access
