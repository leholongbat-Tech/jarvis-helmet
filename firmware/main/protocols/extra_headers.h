#ifndef EXTRA_HEADERS_H
#define EXTRA_HEADERS_H

#include <functional>
#include <mutex>
#include <string>
#include <vector>

// Registry of extra HTTP headers scoped to a host name.
//
// A board registers headers at runtime (for example Cloudflare Access service-token
// headers); the OTA client (Ota::SetupHttp, Ota::Upgrade) and the WebSocket handshake add
// the headers registered for the host they connect to. Headers are only ever added to
// https:// and wss:// URLs, since they may carry secrets. The registry is empty by default,
// so it changes nothing unless a board fills it. Thread-safe.
class ExtraHeaders {
public:
    using Visitor = std::function<void(const std::string& name, const std::string& value)>;

    static ExtraHeaders& GetInstance();

    // Adds or replaces header `name` for `host` (host name only, case-insensitive, no
    // port). Rejects empty names and values containing CR/LF.
    bool Set(const std::string& host, const std::string& name, const std::string& value);
    // Removes every header registered for `host`.
    void ClearHost(const std::string& host);
    bool HasHost(const std::string& host) const;

    // Calls `visitor` for every header registered for the host of `url`, but only when `url`
    // uses TLS (https:// or wss://): a runtime-supplied plain http:// URL to a registered
    // host (for example a firmware URL from an MCP tool) never gets the headers.
    void ForEach(const std::string& url, const Visitor& visitor) const;

    // "wss://user@Jarvis.Example.com:443/xiaozhi/v1/" -> "jarvis.example.com".
    // Returns an empty string when the URL has no host.
    static std::string HostFromUrl(const std::string& url);
    // True for https:// and wss:// URLs (scheme compared case-insensitively).
    static bool IsTlsUrl(const std::string& url);

    ExtraHeaders(const ExtraHeaders&) = delete;
    ExtraHeaders& operator=(const ExtraHeaders&) = delete;

private:
    ExtraHeaders() = default;

    struct Header {
        std::string host;
        std::string name;
        std::string value;
    };

    mutable std::mutex mutex_;
    std::vector<Header> headers_;
};

#endif  // EXTRA_HEADERS_H
