// Board-level MCP tools (FW4): self.sequence.*, self.display.show_info and
// self.system.get_status (contract §3.5).
//
// Callbacks run on the main task (McpServer::DoToolCall -> Application::Schedule): they
// validate, hand work to the sequence runner and return. None of them waits for motion.
// Error strings go through helmet::SanitizeMessage because McpServer::ReplyError pastes
// them into JSON without escaping.
#include "helmet_tools.h"

#include <esp_log.h>
#include <esp_timer.h>
#include <cJSON.h>
#include <wifi_manager.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

#include "cjson_utils.h"
#include "helmet_common.h"
#include "mask_controller.h"
#include "mcp_server.h"
#include "output_controller.h"
#include "sequence_runner.h"
#include "system_info.h"
#include "vbus_monitor.h"

namespace helmet_tools {
namespace {

constexpr char TAG[] = "HelmetTools";

// Byte budgets of the show_info string properties (a UTF-8 code point takes at most 4
// bytes); the code-point limits themselves are checked in the callback.
constexpr size_t kInfoTitleMaxBytes = helmet::kInfoTitleMaxChars * 4;  // 96
constexpr size_t kInfoTextMaxBytes = helmet::kInfoTextMaxChars * 4;    // 800

ToolResult Fail(const std::string& message) {
    return std::unexpected(helmet::SanitizeMessage(message));
}

// Same rules as the sequence "display" step: title 0..24 code points, text 0..200.
std::string CheckInfoText(const std::string& title, const std::string& text) {
    if (helmet::Utf8Length(title) > static_cast<size_t>(helmet::kInfoTitleMaxChars) ||
        title.size() > kInfoTitleMaxBytes) {
        return "title too long (max " + std::to_string(helmet::kInfoTitleMaxChars) + " chars)";
    }
    if (helmet::Utf8Length(text) > static_cast<size_t>(helmet::kInfoTextMaxChars) ||
        text.size() > kInfoTextMaxBytes) {
        return "text too long (max " + std::to_string(helmet::kInfoTextMaxChars) + " chars)";
    }
    return {};
}

}  // namespace

std::string BuildStatusJson(MaskController& mask, OutputController& output, SequenceRunner& runner,
                            VbusMonitor& vbus) {
    CJsonUniquePtr root(cJSON_CreateObject());
    if (root == nullptr) {
        return "{}";
    }

    const MaskState mask_state = mask.GetState();
    cJSON* mask_json = cJSON_AddObjectToObject(root.get(), "mask");
    cJSON_AddStringToObject(mask_json, "state", MaskStateKindName(mask_state.kind));
    cJSON_AddNumberToObject(mask_json, "position", mask_state.position);

    const OutputState output_state = output.GetState();
    cJSON* eyes_json = cJSON_AddObjectToObject(root.get(), "eyes");
    cJSON_AddBoolToObject(eyes_json, "on", output_state.eyes_on);
    cJSON_AddNumberToObject(eyes_json, "brightness", output_state.eyes_brightness);
    cJSON_AddStringToObject(eyes_json, "effect", output_state.eyes_effect.c_str());
    cJSON* output_json = cJSON_AddObjectToObject(root.get(), "output");
    cJSON_AddBoolToObject(output_json, "on", output_state.output_on);

    cJSON* sequence_json = cJSON_AddObjectToObject(root.get(), "sequence");
    cJSON_AddBoolToObject(sequence_json, "running", runner.IsRunning());
    cJSON_AddStringToObject(sequence_json, "name", runner.CurrentName().c_str());

    cJSON_AddNumberToObject(root.get(), "vbus_mv", vbus.GetMillivolts());
    auto& wifi = WifiManager::GetInstance();
    if (wifi.IsConnected()) {
        cJSON_AddNumberToObject(root.get(), "rssi", wifi.GetRssi());
    } else {
        cJSON_AddNullToObject(root.get(), "rssi");
    }
    cJSON_AddNumberToObject(root.get(), "uptime_s",
                            static_cast<double>(esp_timer_get_time() / 1000000));
    cJSON_AddNumberToObject(root.get(), "free_heap",
                            static_cast<double>(SystemInfo::GetFreeHeapSize()));

    CJsonStringUniquePtr text(cJSON_PrintUnformatted(root.get()));
    return text != nullptr ? std::string(text.get()) : std::string("{}");
}

void Register(SequenceRunner& runner, MaskController& mask, OutputController& output,
              VbusMonitor& vbus, ShowInfoFn show_info) {
    auto& mcp = McpServer::GetInstance();

    mcp.AddTool(
        "self.sequence.run",
        "Chạy chuỗi thao tác / Run a step sequence. steps = JSON array of one-key "
        "objects: mask, servo, eyes, output, delay, display, sound. Returns at once; "
        "a new sequence replaces the running one.",
        PropertyList(
            {Property("steps", kPropertyTypeString).SetMaxLength(helmet::kMaxSequenceJsonBytes)}),
        [&runner](const PropertyList& properties) -> ToolResult {
            auto result = runner.Run(properties["steps"].value<std::string>());
            if (!result) {
                return Fail(result.error());
            }
            return std::string("started");
        });

    mcp.AddTool(
        "self.sequence.run_saved",
        "Chạy chuỗi đã lưu / Run a sequence saved on the helmet by name "
        "(e.g. open_mask, close_mask).",
        PropertyList(
            {Property("name", kPropertyTypeString).SetMaxLength(helmet::kSequenceNameMaxLen)}),
        [&runner](const PropertyList& properties) -> ToolResult {
            auto result = runner.RunSaved(properties["name"].value<std::string>());
            if (!result) {
                return Fail(result.error());
            }
            return std::string("started");
        });

    mcp.AddTool(
        "self.sequence.save",
        "Lưu chuỗi thao tác vào mũ / Save a step sequence on the helmet for offline "
        "use (name: a-z, 0-9, _; max 11 chars).",
        PropertyList(
            {Property("name", kPropertyTypeString).SetMaxLength(helmet::kSequenceNameMaxLen),
             Property("steps", kPropertyTypeString).SetMaxLength(helmet::kMaxSequenceJsonBytes)}),
        [&runner](const PropertyList& properties) -> ToolResult {
            auto result = runner.Save(properties["name"].value<std::string>(),
                                      properties["steps"].value<std::string>());
            if (!result) {
                return Fail(result.error());
            }
            return std::string("saved");
        });

    mcp.AddTool("self.sequence.stop", "Dừng chuỗi đang chạy / Stop the running sequence.",
                PropertyList(), [&runner](const PropertyList&) -> ToolResult {
                    runner.Stop();
                    return std::string("stopped");
                });

    mcp.AddTool(
        "self.display.show_info",
        "Hiện thẻ thông tin trên HUD / Show an info card on the helmet HUD "
        "(title max 24 chars, text max 200 chars).",
        PropertyList({Property("title", kPropertyTypeString).SetMaxLength(kInfoTitleMaxBytes),
                      Property("text", kPropertyTypeString).SetMaxLength(kInfoTextMaxBytes),
                      Property("seconds", kPropertyTypeInteger, helmet::kDefaultInfoSeconds,
                               helmet::kInfoSecondsMin, helmet::kInfoSecondsMax)}),
        [show_info](const PropertyList& properties) -> ToolResult {
            const std::string& title = properties["title"].value<std::string>();
            const std::string& text = properties["text"].value<std::string>();
            const std::string error = CheckInfoText(title, text);
            if (!error.empty()) {
                return Fail(error);
            }
            if (!show_info) {
                return Fail("display unavailable");
            }
            show_info(title, text, properties["seconds"].value<int>());
            return std::string("ok");
        });

    mcp.AddTool("self.system.get_status",
                "Trạng thái mũ / Helmet status: mask, eyes, output, running sequence, 5V supply "
                "(vbus_mv), Wi-Fi RSSI, uptime.",
                PropertyList(),
                [&runner, &mask, &output, &vbus](const PropertyList&) -> ToolResult {
                    return BuildStatusJson(mask, output, runner, vbus);
                });

    ESP_LOGI(TAG, "Registered sequence, display and status tools");
}

}  // namespace helmet_tools
