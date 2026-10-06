#include "extra_headers.h"

#include <esp_log.h>

#include <strings.h>
#include <algorithm>
#include <cctype>

#define TAG "ExtraHeaders"

namespace {

std::string ToLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

bool HasLineBreak(const std::string& s) { return s.find_first_of("\r\n") != std::string::npos; }

}  // namespace

ExtraHeaders& ExtraHeaders::GetInstance() {
    static ExtraHeaders instance;
    return instance;
}

std::string ExtraHeaders::HostFromUrl(const std::string& url) {
    size_t start = url.find("://");
    start = (start == std::string::npos) ? 0 : start + 3;
    size_t end = url.find_first_of("/?#", start);
    std::string authority =
        url.substr(start, end == std::string::npos ? std::string::npos : end - start);
    size_t at = authority.rfind('@');
    if (at != std::string::npos) {
        authority.erase(0, at + 1);
    }
    if (!authority.empty() && authority.front() == '[') {
        // IPv6 literal: "[::1]:8080"
        size_t close = authority.find(']');
        authority = (close == std::string::npos) ? std::string() : authority.substr(0, close + 1);
    } else {
        size_t colon = authority.find(':');
        if (colon != std::string::npos) {
            authority.erase(colon);
        }
    }
    return ToLower(authority);
}

bool ExtraHeaders::IsTlsUrl(const std::string& url) {
    return strncasecmp(url.c_str(), "https://", 8) == 0 || strncasecmp(url.c_str(), "wss://", 6) == 0;
}

bool ExtraHeaders::Set(const std::string& host, const std::string& name, const std::string& value) {
    std::string key = ToLower(host);
    if (key.empty() || name.empty() || HasLineBreak(host) || HasLineBreak(name) ||
        HasLineBreak(value)) {
        // Never log the value: it may be a secret.
        ESP_LOGW(TAG, "Rejected header %s for host %s", name.c_str(), key.c_str());
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    for (auto& header : headers_) {
        if (header.host == key && strcasecmp(header.name.c_str(), name.c_str()) == 0) {
            header.value = value;
            return true;
        }
    }
    headers_.push_back(Header{key, name, value});
    return true;
}

void ExtraHeaders::ClearHost(const std::string& host) {
    std::string key = ToLower(host);
    std::lock_guard<std::mutex> lock(mutex_);
    headers_.erase(std::remove_if(headers_.begin(), headers_.end(),
                                  [&key](const Header& h) { return h.host == key; }),
                   headers_.end());
}

bool ExtraHeaders::HasHost(const std::string& host) const {
    std::string key = ToLower(host);
    std::lock_guard<std::mutex> lock(mutex_);
    return std::any_of(headers_.begin(), headers_.end(),
                       [&key](const Header& h) { return h.host == key; });
}

void ExtraHeaders::ForEach(const std::string& url, const Visitor& visitor) const {
    if (!visitor || !IsTlsUrl(url)) {
        return;  // never send registered (possibly secret) headers in clear text
    }
    std::string host = HostFromUrl(url);
    if (host.empty()) {
        return;
    }
    // Copy under the lock and call the visitor without it.
    std::vector<Header> matches;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        for (const auto& header : headers_) {
            if (header.host == host) {
                matches.push_back(header);
            }
        }
    }
    for (const auto& header : matches) {
        visitor(header.name, header.value);
    }
}
