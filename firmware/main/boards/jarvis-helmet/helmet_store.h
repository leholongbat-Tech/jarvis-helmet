// Persistent helmet data on the dedicated "helmet" NVS partition.
//
// The upstream Settings wrapper only knows i32/bool/string, aborts on NVS errors and
// shares the 16 KB "nvs" partition with Wi-Fi and the core, so helmet data lives on its
// own partition (see partitions_jarvis_16m.csv) and every failure is returned instead.
//
// NVS writes must not run on a task whose stack is in PSRAM.
#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>

// Keys of namespace "helmet" (docs/interfaces.md §4). NVS keys are at most 15 chars.
namespace helmet_keys {
constexpr const char* kS0Closed = "s0_closed";    // u8, servo 0 (left) closed angle
constexpr const char* kS0Open = "s0_open";        // u8, servo 0 open angle
constexpr const char* kS1Closed = "s1_closed";    // u8, servo 1 (right) closed angle
constexpr const char* kS1Open = "s1_open";        // u8, servo 1 open angle
constexpr const char* kMaskSpeed = "mask_speed";  // u8, default speed 10..100
constexpr const char* kEyesBright = "eyes_bright";  // u8, default brightness 0..100
constexpr const char* kMaskPos = "mask_pos";      // u8, last mask position 0..100
constexpr const char* kS0Pos = "s0_pos";          // u8, servo 0 resting angle 0..180
constexpr const char* kS1Pos = "s1_pos";          // u8, servo 1 resting angle 0..180
constexpr const char* kSeqPrefix = "seq_";        // blob, "seq_<name>" saved sequence JSON
}  // namespace helmet_keys

class HelmetStore {
public:
    static constexpr const char* kPartition = "helmet";
    static constexpr const char* kNamespace = "helmet";
    static constexpr size_t kMaxBlobSize = 4000;

    static HelmetStore& GetInstance();

    // Mounts the partition; erases and re-initializes it when its layout is stale.
    // Safe to call more than once. Returns false if the partition is unusable.
    bool Init();
    bool IsReady() const { return initialized_; }

    bool GetU8(const char* key, uint8_t& value);
    bool SetU8(const char* key, uint8_t value);
    bool GetBlob(const char* key, std::string& value);
    bool SetBlob(const char* key, const std::string& value);  // fails if > kMaxBlobSize
    bool Erase(const char* key);
    bool Exists(const char* key);

    HelmetStore(const HelmetStore&) = delete;
    HelmetStore& operator=(const HelmetStore&) = delete;

private:
    HelmetStore() = default;

    std::mutex mutex_;
    bool initialized_ = false;
};
