// Local MCP WebSocket endpoint. Derived from main/boards/otto-robot/
// websocket_control_server.cc of xiaozhi-esp32 (MIT License, Copyright (c) 2025 Shenzhen
// Xinzhi Future Technology Co., Ltd. and Project Contributors, see firmware/LICENSE).
#include "local_mcp_server.h"

#include <sdkconfig.h>

// Compiled only when the option is on; the board does not reference it otherwise.
#if CONFIG_JARVIS_LOCAL_MCP_WS

#if !CONFIG_HTTPD_WS_SUPPORT
#error \
    "CONFIG_JARVIS_LOCAL_MCP_WS needs CONFIG_HTTPD_WS_SUPPORT=y (boards/jarvis-helmet/config.json)"
#endif
#if !CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT
#error \
    "CONFIG_JARVIS_LOCAL_MCP_WS needs CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT=y (Kconfig.jarvis)"
#endif

#include "helmet_common.h"
#include "jsecrets.h"
#include "local_mcp_policy.h"
#include "mcp_server.h"

#include <esp_log.h>
#include <cJSON.h>
#include <unistd.h>

#include <cstring>
#include <new>
#include <utility>

#define TAG "LocalMcp"

namespace {

// tools/call for self.sequence.run carries up to 4000 bytes of escaped JSON.
constexpr size_t kMaxMessageBytes = 16 * 1024;
// The Wi-Fi configuration portal runs its own httpd on the default control port.
constexpr uint16_t kControlPort = ESP_HTTPD_DEF_CTRL_PORT + 1;
// NVS jsecrets/secrets key of the optional access token (contract D27).
constexpr char kTokenKey[] = "mcp_token";

constexpr char kParseError[] =
    "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32700,\"message\":\"Parse error\"}}";

struct SendJob {
    httpd_handle_t server;
    int fd;
    uint32_t session;
    std::string payload;
};

std::string ErrorReply(const cJSON* request, int code, const char* message) {
    const cJSON* id = cJSON_GetObjectItem(request, "id");
    std::string reply = "{\"jsonrpc\":\"2.0\",\"id\":";
    reply += cJSON_IsNumber(id) ? std::to_string(id->valueint) : std::string("null");
    reply += ",\"error\":{\"code\":" + std::to_string(code) + ",\"message\":\"";
    reply += message;
    reply += "\"}}";
    return reply;
}

}  // namespace

LocalMcpServer& LocalMcpServer::GetInstance() {
    static LocalMcpServer instance;
    return instance;
}

void LocalMcpServer::LoadToken() {
    std::string token;
    const jsecrets::Status status =
        jsecrets::ReadString(kTokenKey, local_mcp::kMaxTokenLength, token);
    TokenState state = TokenState::kNone;
    if (status == jsecrets::Status::kFound && local_mcp::IsValidToken(token)) {
        state = TokenState::kRequired;
    } else if (status != jsecrets::Status::kAbsent) {
        // Present but unusable: refuse everyone rather than silently open the endpoint.
        // Only the length is logged, the value is a secret.
        state = TokenState::kInvalid;
        if (status == jsecrets::Status::kFound) {
            ESP_LOGE(TAG, "%s in NVS is not usable (%u chars; need 1..%u of A-Z a-z 0-9 - . _ ~)",
                     kTokenKey, static_cast<unsigned>(token.size()),
                     static_cast<unsigned>(local_mcp::kMaxTokenLength));
        } else {
            ESP_LOGE(TAG, "%s in NVS cannot be read", kTokenKey);
        }
        token.clear();
    }
    std::lock_guard<std::mutex> lock(mutex_);
    token_state_ = state;
    token_ = std::move(token);
}

bool LocalMcpServer::Start(int port) {
    TokenState state;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (server_ != nullptr) {
            return true;
        }
        state = token_state_;
    }
    if (state == TokenState::kNotLoaded) {
        LoadToken();
        std::lock_guard<std::mutex> lock(mutex_);
        state = token_state_;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = static_cast<uint16_t>(port);
    config.ctrl_port = kControlPort;
    config.max_open_sockets = 2;     // the calibration CLI plus one spare
    config.lru_purge_enable = true;  // a dead client never locks others out
    config.max_uri_handlers = 1;
    config.stack_size = 6144;  // McpServer::ParseMessage builds tools/list here
    config.global_user_ctx = this;
    config.global_user_ctx_free_fn = [](void*) {};  // singleton, httpd must not free() it
    config.close_fn = &LocalMcpServer::HandleClose;

    httpd_handle_t server = nullptr;
    esp_err_t err = httpd_start(&server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return false;
    }

    httpd_uri_t ws_uri = {};
    ws_uri.uri = "/ws";
    ws_uri.method = HTTP_GET;
    ws_uri.handler = &LocalMcpServer::HandleRequest;
    ws_uri.user_ctx = this;
    ws_uri.is_websocket = true;
    ws_uri.ws_pre_handshake_cb = &LocalMcpServer::HandlePreHandshake;
    err = httpd_register_uri_handler(server, &ws_uri);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Register /ws failed: %s", esp_err_to_name(err));
        httpd_stop(server);
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        server_ = server;
    }
    switch (state) {
        case TokenState::kRequired:
            ESP_LOGI(TAG, "MCP WebSocket server on ws://<ip>:%d/ws (access token required)", port);
            break;
        case TokenState::kNone:
            ESP_LOGW(TAG,
                     "MCP WebSocket server on ws://<ip>:%d/ws WITHOUT authentication: no %s in "
                     "NVS, anyone on this network can move the mask and change the calibration",
                     port, kTokenKey);
            break;
        default:
            ESP_LOGE(TAG,
                     "MCP WebSocket server on ws://<ip>:%d/ws refuses every client until %s is "
                     "provisioned again (tools/provision_secrets.py --mcp-token)",
                     port, kTokenKey);
            break;
    }
    return true;
}

void LocalMcpServer::Stop() {
    httpd_handle_t server;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        server = server_;
        server_ = nullptr;
        sessions_.clear();
    }
    // Outside the lock: httpd_stop() runs HandleClose() for every open session.
    if (server != nullptr) {
        httpd_stop(server);
        ESP_LOGI(TAG, "Stopped");
    }
}

bool LocalMcpServer::IsRunning() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return server_ != nullptr;
}

esp_err_t LocalMcpServer::HandlePreHandshake(httpd_req_t* req) {
    auto* self = static_cast<LocalMcpServer*>(req->user_ctx);
    if (self->Admit(req)) {
        return ESP_OK;
    }
    httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Missing or wrong access token (?token=)");
    return ESP_FAIL;  // httpd closes the socket without upgrading it
}

// httpd task, before the 101 response: checks the access token and starts a session for an
// admitted client. If the upgrade then fails, the close callback ends the session.
bool LocalMcpServer::Admit(httpd_req_t* req) {
    const int fd = httpd_req_to_sockfd(req);
    const char* refusal = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        switch (token_state_) {
            case TokenState::kNone:
                break;
            case TokenState::kRequired:
                switch (local_mcp::CheckToken(req->uri, token_)) {
                    case local_mcp::TokenCheck::kAccepted:
                        break;
                    case local_mcp::TokenCheck::kMissing:
                        refusal = "missing token";
                        break;
                    case local_mcp::TokenCheck::kRejected:
                        refusal = "wrong token";
                        break;
                }
                break;
            case TokenState::kInvalid:
            case TokenState::kNotLoaded:
                refusal = "no usable token on the device";
                break;
        }
    }
    if (refusal != nullptr) {
        // Neither the token nor the request URI (which carries it) is logged.
        ESP_LOGW(TAG, "Client refused (fd %d): %s", fd, refusal);
        return false;
    }
    OnOpen(fd);
    return true;
}

esp_err_t LocalMcpServer::HandleRequest(httpd_req_t* req) {
    auto* self = static_cast<LocalMcpServer*>(req->user_ctx);
    int fd = httpd_req_to_sockfd(req);
    if (req->method == HTTP_GET) {
        // IDF 6.0 calls the handler once the upgrade is done (IDF 6.1 does not; WebSocket
        // frames come with method 0): the session already started in Admit(). Any other
        // GET is a plain HTTP request and never reaches McpServer.
        if (httpd_ws_get_fd_info(req->handle, fd) == HTTPD_WS_CLIENT_WEBSOCKET) {
            return ESP_OK;
        }
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "WebSocket endpoint");
        return ESP_FAIL;
    }

    httpd_ws_frame_t frame = {};
    frame.type = HTTPD_WS_TYPE_TEXT;
    esp_err_t err = httpd_ws_recv_frame(req, &frame, 0);  // length only
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Read frame header failed: %s", esp_err_to_name(err));
        return err;
    }
    if (frame.len > kMaxMessageBytes) {
        ESP_LOGW(TAG, "Frame too large (%u bytes), closing", static_cast<unsigned>(frame.len));
        return ESP_FAIL;  // the payload stays unread, so the session must go
    }
    if (frame.len == 0) {
        return ESP_OK;
    }
    std::string message(frame.len, '\0');
    frame.payload = reinterpret_cast<uint8_t*>(message.data());
    err = httpd_ws_recv_frame(req, &frame, frame.len);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Read frame failed: %s", esp_err_to_name(err));
        return err;
    }
    if (frame.type != HTTPD_WS_TYPE_TEXT) {
        ESP_LOGW(TAG, "Ignoring frame type %d", static_cast<int>(frame.type));
        return ESP_OK;
    }
    self->OnText(fd, std::move(message));
    return ESP_OK;
}

void LocalMcpServer::HandleClose(httpd_handle_t handle, int sockfd) {
    auto* self = static_cast<LocalMcpServer*>(httpd_get_global_user_ctx(handle));
    if (self != nullptr) {
        self->OnClose(sockfd);
    }
    close(sockfd);  // with a custom close_fn httpd leaves closing the socket to us
}

void LocalMcpServer::OnOpen(int fd) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (++next_session_ == 0) {
        ++next_session_;
    }
    sessions_[fd] = next_session_;
    ESP_LOGI(TAG, "Client connected (fd %d, %u open)", fd, static_cast<unsigned>(sessions_.size()));
}

void LocalMcpServer::OnClose(int fd) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (sessions_.erase(fd) > 0) {
        ESP_LOGI(TAG, "Client disconnected (fd %d)", fd);
    }
}

bool LocalMcpServer::IsCurrentSession(int fd, uint32_t session) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sessions_.find(fd);
    return it != sessions_.end() && it->second == session;
}

void LocalMcpServer::OnText(int fd, std::string message) {
    uint32_t session = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = sessions_.find(fd);
        if (it != sessions_.end()) {
            session = it->second;
        }
    }
    if (session == 0) {
        ESP_LOGW(TAG, "Message from unknown socket %d ignored", fd);
        return;
    }

    // cJSON recurses once per '[' / '{' and this runs on the 6 KB httpd stack: a frame of a
    // few hundred brackets from any LAN client would overflow it.
    if (!helmet::JsonNestingWithin(message, local_mcp::kMaxNestingDepth)) {
        ESP_LOGW(TAG, "Frame nested too deep, refused");
        SendText(fd, session, kParseError);
        return;
    }
    cJSON* root = cJSON_ParseWithLength(message.data(), message.size());
    if (root == nullptr) {
        SendText(fd, session, kParseError);
        return;
    }

    // Replies may come later from the main task (tools/call runs there).
    auto sender = [this, fd, session](const std::string& payload) {
        SendText(fd, session, payload);
    };

    // Accept both {"type":"mcp","payload":{...}} and a bare JSON-RPC object.
    const cJSON* request = root;
    cJSON* type = cJSON_GetObjectItem(root, "type");
    if (cJSON_IsString(type) && std::strcmp(type->valuestring, "mcp") == 0) {
        request = cJSON_GetObjectItem(root, "payload");
    }
    if (!cJSON_IsObject(request)) {
        SendText(fd, session,
                 "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32600,"
                 "\"message\":\"Invalid request\"}}");
    } else if (const char* refused = local_mcp::RefusedToolCall(request); refused != nullptr) {
        // With or without a token, this port serves only helmet hardware and status tools.
        ESP_LOGW(TAG, "tools/call %.48s refused (not allowed on the local endpoint)", refused);
        SendText(fd, session,
                 ErrorReply(request, -32601, "tool not allowed on the local endpoint"));
    } else {
        McpServer::GetInstance().ParseMessage(request, sender);
    }
    cJSON_Delete(root);
}

void LocalMcpServer::SendText(int fd, uint32_t session, const std::string& payload) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (server_ == nullptr) {
        return;
    }
    auto* job = new (std::nothrow) SendJob{server_, fd, session, payload};
    if (job == nullptr) {
        ESP_LOGE(TAG, "Out of memory for reply");
        return;
    }
    // Sending must happen on the httpd task.
    esp_err_t err = httpd_queue_work(server_, &LocalMcpServer::SendWork, job);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Queue reply failed: %s", esp_err_to_name(err));
        delete job;
    }
}

void LocalMcpServer::SendWork(void* arg) {
    auto* job = static_cast<SendJob*>(arg);
    // The socket may have closed, or been reused by a new client, since the request.
    if (GetInstance().IsCurrentSession(job->fd, job->session) &&
        httpd_ws_get_fd_info(job->server, job->fd) == HTTPD_WS_CLIENT_WEBSOCKET) {
        httpd_ws_frame_t frame = {};
        frame.type = HTTPD_WS_TYPE_TEXT;
        frame.payload = reinterpret_cast<uint8_t*>(job->payload.data());
        frame.len = job->payload.size();
        esp_err_t err = httpd_ws_send_frame_async(job->server, job->fd, &frame);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Send to fd %d failed: %s", job->fd, esp_err_to_name(err));
        }
    }
    delete job;
}

#endif  // CONFIG_JARVIS_LOCAL_MCP_WS
