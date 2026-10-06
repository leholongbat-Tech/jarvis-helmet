// HelmetStore: raw NVS access on the dedicated "helmet" partition (contract D6).
//
// Every call opens and closes its own handle under one mutex, so the store can be used
// from the main task, the controller tasks and the sequence runner. Nothing here aborts:
// failures are logged and returned to the caller.
#include "helmet_store.h"

#include <esp_log.h>
#include <nvs.h>
#include <nvs_flash.h>

#include <cstring>

#define TAG "HelmetStore"

namespace {

// Opens the helmet namespace; read-only handles fail with ESP_ERR_NVS_NOT_FOUND until
// the first write created the namespace.
esp_err_t OpenNamespace(nvs_open_mode_t mode, nvs_handle_t* handle) {
    return nvs_open_from_partition(HelmetStore::kPartition, HelmetStore::kNamespace, mode, handle);
}

bool ValidKey(const char* key) {
    return key != nullptr && key[0] != '\0' && std::strlen(key) < NVS_KEY_NAME_MAX_SIZE;
}

}  // namespace

HelmetStore& HelmetStore::GetInstance() {
    static HelmetStore instance;
    return instance;
}

bool HelmetStore::Init() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (initialized_) {
        return true;
    }
    esp_err_t err = nvs_flash_init_partition(kPartition);
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        // Stale or full layout: the data cannot be read anyway, start from scratch.
        ESP_LOGW(TAG, "Partition '%s' unusable (%s), erasing", kPartition, esp_err_to_name(err));
        err = nvs_flash_erase_partition(kPartition);
        if (err == ESP_OK) {
            err = nvs_flash_init_partition(kPartition);
        }
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot mount partition '%s': %s (flash the jarvis partition table?)",
                 kPartition, esp_err_to_name(err));
        return false;
    }
    initialized_ = true;
    ESP_LOGI(TAG, "Partition '%s' mounted", kPartition);
    return true;
}

bool HelmetStore::GetU8(const char* key, uint8_t& value) {
    if (!ValidKey(key)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    esp_err_t err = OpenNamespace(NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return false;  // namespace not created yet: nothing stored
    }
    uint8_t stored = 0;
    err = nvs_get_u8(handle, key, &stored);
    nvs_close(handle);
    if (err != ESP_OK) {
        if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "Read %s failed: %s", key, esp_err_to_name(err));
        }
        return false;
    }
    value = stored;
    return true;
}

bool HelmetStore::SetU8(const char* key, uint8_t value) {
    if (!ValidKey(key)) {
        ESP_LOGE(TAG, "Invalid key");
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    esp_err_t err = OpenNamespace(NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Open for write failed: %s", esp_err_to_name(err));
        return false;
    }
    uint8_t current = 0;
    if (nvs_get_u8(handle, key, &current) == ESP_OK && current == value) {
        nvs_close(handle);  // unchanged: spare the flash (mask_pos is written after every move)
        return true;
    }
    err = nvs_set_u8(handle, key, value);
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Write %s failed: %s", key, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool HelmetStore::GetBlob(const char* key, std::string& value) {
    if (!ValidKey(key)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    esp_err_t err = OpenNamespace(NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return false;
    }
    size_t length = 0;
    err = nvs_get_blob(handle, key, nullptr, &length);
    if (err != ESP_OK || length > kMaxBlobSize) {
        nvs_close(handle);
        if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "Read %s failed: %s", key, esp_err_to_name(err));
        } else if (err == ESP_OK) {
            ESP_LOGW(TAG, "Blob %s too large (%u bytes)", key, static_cast<unsigned>(length));
        }
        return false;
    }
    std::string data(length, '\0');
    if (length > 0) {
        err = nvs_get_blob(handle, key, data.data(), &length);
    }
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Read %s failed: %s", key, esp_err_to_name(err));
        return false;
    }
    data.resize(length);
    value = std::move(data);
    return true;
}

bool HelmetStore::SetBlob(const char* key, const std::string& value) {
    if (!ValidKey(key)) {
        ESP_LOGE(TAG, "Invalid key");
        return false;
    }
    if (value.size() > kMaxBlobSize) {
        ESP_LOGE(TAG, "Blob %s too large (%u > %u bytes)", key, static_cast<unsigned>(value.size()),
                 static_cast<unsigned>(kMaxBlobSize));
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    esp_err_t err = OpenNamespace(NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Open for write failed: %s", esp_err_to_name(err));
        return false;
    }
    err = nvs_set_blob(handle, key, value.data(), value.size());
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Write %s failed: %s", key, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool HelmetStore::Erase(const char* key) {
    if (!ValidKey(key)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    esp_err_t err = OpenNamespace(NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Open for erase failed: %s", esp_err_to_name(err));
        return false;
    }
    err = nvs_erase_key(handle, key);
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    } else if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;  // already gone
    }
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Erase %s failed: %s", key, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool HelmetStore::Exists(const char* key) {
    if (!ValidKey(key)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!initialized_) {
        return false;
    }
    nvs_handle_t handle;
    if (OpenNamespace(NVS_READONLY, &handle) != ESP_OK) {
        return false;
    }
    esp_err_t err = nvs_find_key(handle, key, nullptr);
    nvs_close(handle);
    return err == ESP_OK;
}
