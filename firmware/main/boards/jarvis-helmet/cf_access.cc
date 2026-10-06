#include "cf_access.h"

#include "extra_headers.h"
#include "jsecrets.h"
#include "settings.h"

#include <esp_log.h>
#include <sdkconfig.h>

#include <algorithm>
#include <mutex>
#include <string>
#include <vector>

#define TAG "CfAccess"

namespace cf_access {
namespace {

constexpr const char* kIdKey = "cf_id";
constexpr const char* kSecretKey = "cf_secret";
// Client IDs are 39 chars, secrets 54 ("cfast_...") or 64 (legacy hex).
constexpr size_t kMaxValueLength = 128;

constexpr const char* kIdHeader = "CF-Access-Client-Id";
constexpr const char* kSecretHeader = "CF-Access-Client-Secret";

std::mutex g_mutex;
std::string g_id;
std::string g_secret;
std::vector<std::string> g_hosts;  // hosts that currently carry the headers

bool IsPlainToken(const std::string& s) {
    if (s.empty() || s.size() > kMaxValueLength) {
        return false;
    }
    return std::all_of(s.begin(), s.end(), [](unsigned char c) { return c > 0x20 && c < 0x7F; });
}

std::string Trim(std::string s) {
    auto not_space = [](unsigned char c) {
        return c != ' ' && c != '\t' && c != '\r' && c != '\n';
    };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), not_space));
    s.erase(std::find_if(s.rbegin(), s.rend(), not_space).base(), s.end());
    return s;
}

// Reads cf_id / cf_secret from the jsecrets partition (jsecrets.h reports problems). Any
// failure means the Kconfig fallback is used instead.
bool LoadFromNvs(std::string& id, std::string& secret) {
    if (jsecrets::ReadString(kIdKey, kMaxValueLength, id) != jsecrets::Status::kFound ||
        jsecrets::ReadString(kSecretKey, kMaxValueLength, secret) != jsecrets::Status::kFound) {
        return false;
    }
    id = Trim(id);
    secret = Trim(secret);
    return true;
}

// Same rule as Ota::GetCheckVersionUrl().
std::string CurrentOtaUrl() {
    Settings settings("wifi", false);
    std::string url = settings.GetString("ota_url");
    if (url.empty()) {
        url = CONFIG_OTA_URL;
    }
    return url;
}

std::string CurrentWebsocketUrl() {
    Settings settings("websocket", false);
    return settings.GetString("url");
}

bool UsesTls(const std::string& url) {
    return url.rfind("https://", 0) == 0 || url.rfind("wss://", 0) == 0;
}

// Caller holds g_mutex.
void ApplyHostsLocked() {
    std::vector<std::string> wanted;
    for (const std::string& url : {CurrentOtaUrl(), CurrentWebsocketUrl()}) {
        if (!UsesTls(url)) {
            continue;  // never send the secret in clear text
        }
        std::string host = ExtraHeaders::HostFromUrl(url);
        if (!host.empty() && std::find(wanted.begin(), wanted.end(), host) == wanted.end()) {
            wanted.push_back(host);
        }
    }

    auto& registry = ExtraHeaders::GetInstance();
    for (const std::string& host : g_hosts) {
        if (std::find(wanted.begin(), wanted.end(), host) == wanted.end()) {
            registry.ClearHost(host);
            ESP_LOGI(TAG, "Headers removed from %s", host.c_str());
        }
    }
    for (const std::string& host : wanted) {
        if (std::find(g_hosts.begin(), g_hosts.end(), host) == g_hosts.end()) {
            registry.Set(host, kIdHeader, g_id);
            registry.Set(host, kSecretHeader, g_secret);
            ESP_LOGI(TAG, "Headers registered for %s", host.c_str());
        }
    }
    g_hosts = std::move(wanted);
}

}  // namespace

bool Init() {
    std::string id;
    std::string secret;
    const char* source = "NVS";
    if (!LoadFromNvs(id, secret)) {
        id = Trim(CONFIG_JARVIS_CF_ACCESS_ID);
        secret = Trim(CONFIG_JARVIS_CF_ACCESS_SECRET);
        source = "Kconfig";
    }
    if (id.empty() && secret.empty()) {
        ESP_LOGI(TAG, "No service token configured");
        return false;
    }
    if (!IsPlainToken(id) || !IsPlainToken(secret)) {
        ESP_LOGE(TAG, "Invalid service token from %s (id %u chars, secret %u chars), ignored",
                 source, static_cast<unsigned>(id.size()), static_cast<unsigned>(secret.size()));
        return false;
    }

    std::lock_guard<std::mutex> lock(g_mutex);
    g_id = std::move(id);
    g_secret = std::move(secret);
    ESP_LOGI(TAG, "Service token loaded from %s (id %u chars, secret %u chars)", source,
             static_cast<unsigned>(g_id.size()), static_cast<unsigned>(g_secret.size()));
    ApplyHostsLocked();
    return true;
}

void RefreshHosts() {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_id.empty()) {
        return;
    }
    ApplyHostsLocked();
}

bool IsConfigured() {
    std::lock_guard<std::mutex> lock(g_mutex);
    return !g_id.empty();
}

}  // namespace cf_access
