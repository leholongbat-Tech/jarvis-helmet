#include "jsecrets.h"

#include <esp_log.h>
#include <nvs.h>
#include <nvs_flash.h>

#include <utility>

#define TAG "JSecrets"

namespace jsecrets {
namespace {

constexpr const char* kPartition = "jsecrets";
constexpr const char* kNamespace = "secrets";

// Mounts the partition once. Never erases it: an image that cannot be mounted is reported
// and stays as it is for tools/provision_secrets.py to rewrite.
esp_err_t Mount() {
    static const esp_err_t result = [] {
        esp_err_t err = nvs_flash_init_partition(kPartition);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Partition '%s' not usable: %s", kPartition, esp_err_to_name(err));
        }
        return err;
    }();
    return result;
}

}  // namespace

Status ReadString(const char* key, size_t max_length, std::string& out) {
    esp_err_t err = Mount();
    if (err != ESP_OK) {
        return err == ESP_ERR_NOT_FOUND ? Status::kAbsent : Status::kUnreadable;
    }
    nvs_handle_t handle;
    err = nvs_open_from_partition(kPartition, kNamespace, NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return Status::kAbsent;  // nothing provisioned yet
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Open '%s/%s' failed: %s", kPartition, kNamespace, esp_err_to_name(err));
        return Status::kUnreadable;
    }

    Status status = Status::kUnreadable;
    size_t length = 0;  // includes the terminating NUL
    err = nvs_get_str(handle, key, nullptr, &length);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        status = Status::kAbsent;
    } else if (err != ESP_OK || length == 0) {
        ESP_LOGW(TAG, "Read '%s' failed: %s", key, esp_err_to_name(err));
    } else if (length > max_length + 1) {
        ESP_LOGW(TAG, "Value of '%s' too long (%u chars, max %u)", key,
                 static_cast<unsigned>(length - 1), static_cast<unsigned>(max_length));
    } else {
        std::string value(length, '\0');
        err = nvs_get_str(handle, key, value.data(), &length);
        if (err == ESP_OK) {
            value.resize(length > 0 ? length - 1 : 0);  // drop the terminating NUL
            out = std::move(value);
            status = Status::kFound;
        } else {
            ESP_LOGW(TAG, "Read '%s' failed: %s", key, esp_err_to_name(err));
        }
    }
    nvs_close(handle);
    return status;
}

}  // namespace jsecrets
