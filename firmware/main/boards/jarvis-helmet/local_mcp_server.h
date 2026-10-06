// Local MCP endpoint on the helmet: ws://<helmet-ip>:<port>/ws (contract D7, D27).
//
// Every text frame is a JSON-RPC 2.0 request (or {"type":"mcp","payload":{...}}) handed
// to McpServer::ParseMessage; the reply goes back to the client that sent the request.
// This reaches the user-only tools (tools/list with {"withUserTools": true},
// self.servo.calibrate ...) that the xiaozhi server never lists. Used by
// tools/servo_calibrate.py. tools/call is limited to the helmet hardware and status tools
// of local_mcp_policy.h and over-nested frames are refused before cJSON parses them.
//
// Access: when NVS holds a token (partition jsecrets, namespace secrets, key mcp_token,
// written by tools/provision_secrets.py --mcp-token) the handshake must be
// GET /ws?token=<value>; any other client gets HTTP 401 before the upgrade and never
// reaches McpServer. A token that is present but unusable refuses every client. Without a
// token the endpoint is open to the whole network (a warning is logged at start).
//
// Based on main/boards/otto-robot/websocket_control_server.cc (xiaozhi-esp32, MIT
// license), reworked for thread safety: a session starts when the pre-handshake callback
// admits the client, ends in the httpd close callback, and replies are matched to the
// session that asked.
#pragma once

#include <esp_http_server.h>

#include <cstdint>
#include <map>
#include <mutex>
#include <string>

class LocalMcpServer {
public:
    static LocalMcpServer& GetInstance();

    // Reads the optional access token from NVS. Call once, before Start(), from a task
    // with an internal-RAM stack (the board constructor); Start() loads it otherwise.
    void LoadToken();

    // Starts the server (idempotent). Needs CONFIG_HTTPD_WS_SUPPORT and
    // CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT (both selected by CONFIG_JARVIS_LOCAL_MCP_WS).
    bool Start(int port);
    void Stop();
    bool IsRunning() const;

    LocalMcpServer(const LocalMcpServer&) = delete;
    LocalMcpServer& operator=(const LocalMcpServer&) = delete;

private:
    enum class TokenState : uint8_t { kNotLoaded, kNone, kRequired, kInvalid };

    LocalMcpServer() = default;

    static esp_err_t HandlePreHandshake(httpd_req_t* req);
    static esp_err_t HandleRequest(httpd_req_t* req);
    static void HandleClose(httpd_handle_t handle, int sockfd);
    static void SendWork(void* arg);

    bool Admit(httpd_req_t* req);
    void OnOpen(int fd);
    void OnClose(int fd);
    void OnText(int fd, std::string message);
    void SendText(int fd, uint32_t session, const std::string& payload);
    bool IsCurrentSession(int fd, uint32_t session);

    mutable std::mutex mutex_;
    httpd_handle_t server_ = nullptr;
    std::map<int, uint32_t> sessions_;  // socket fd -> session id (admitted clients only)
    uint32_t next_session_ = 0;
    TokenState token_state_ = TokenState::kNotLoaded;
    std::string token_;  // set only in kRequired; never logged
};
