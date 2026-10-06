// Sequence runner (FW4): runs validated step lists on the "seq_runner" task.
//
// Callers (MCP callbacks on the main task, button callbacks on the esp_timer task) only
// validate, hand a job over and wake the task; they never wait for motion. There is a
// single pending slot: a new job replaces a job that has not started yet and cancels
// the running one (newest wins). The runner checks the cancel flag at least every 20 ms.
#include "sequence_runner.h"

#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <new>
#include <utility>

#include "helmet_common.h"
#include "helmet_store.h"
#include "mask_controller.h"
#include "output_controller.h"
#include "sequence_validator.h"

namespace {

constexpr char TAG[] = "SeqRunner";

// Internal RAM (xTaskCreate allocates stacks with MALLOC_CAP_INTERNAL on ESP-IDF).
constexpr uint32_t kTaskStackBytes = 6 * 1024;
// Above Opus (2) and AFE (3) work only while sleeping, below the servo task and audio input.
constexpr UBaseType_t kTaskPriority = 3;

constexpr int kCancelTickMs = 20;  // upper bound between two cancel checks
constexpr int kPollMs = 10;        // one FreeRTOS tick at CONFIG_FREERTOS_HZ=100
// Time a controller task may take to pick up a queued command before the runner stops
// waiting for it to start (it then only waits for idle).
constexpr int kStartGraceMs = 200;
// Longest legitimate mask motion is a full travel at speed 10 (8 s); servo moves <= 5 s.
constexpr int kMotionTimeoutMs = 12000;
// Slack on top of an eye effect's duration before the runner gives up waiting.
constexpr int kEffectSlackMs = 2000;

// Built-in fallbacks: same steps as open_mask / close_mask in server/tasks/tasks.yaml
// (docs/interfaces.md §5). The server may replace them with self.sequence.save.
// firmware/tests/host reads both literals back from this file (by constant name, raw
// string delimiter "json") and checks them against interfaces.md.
constexpr char kOpenMaskJson[] =
    R"json([{"eyes":{"effect":"fade_out","duration_ms":300}},{"mask":{"action":"open","speed":80}}])json";
constexpr char kCloseMaskJson[] =
    R"json([{"mask":{"action":"close"}},{"delay":300},{"eyes":{"effect":"power_up","duration_ms":1200}}])json";

constexpr char kInvalidNameError[] = "invalid sequence name (use [a-z0-9_]{1,11})";
constexpr char kTooLongError[] = "steps too long (max 4000 bytes)";

int64_t NowMs() { return esp_timer_get_time() / 1000; }

std::string StoreKey(const std::string& name) {
    return std::string(helmet_keys::kSeqPrefix) + name;
}

}  // namespace

struct SequenceRunner::Impl {
    struct Job {
        seq::Sequence sequence;
        std::string name;         // empty for anonymous sequences
        bool load_saved = false;  // resolve NVS "seq_<name>" / built-in on the runner task
    };

    Impl(MaskController& mask_controller, OutputController& output_controller, ShowInfoFn show,
         PlaySoundFn play)
        : mask(mask_controller),
          output(output_controller),
          show_info(std::move(show)),
          play_sound(std::move(play)) {}

    MaskController& mask;
    OutputController& output;
    ShowInfoFn show_info;
    PlaySoundFn play_sound;

    // Guards task, pending, current_name and running.
    mutable std::mutex mutex;
    TaskHandle_t task = nullptr;
    std::unique_ptr<Job> pending;
    std::string current_name;
    bool running = false;

    std::atomic<bool> cancel{false};
    std::atomic<bool> quit{false};
    std::atomic<bool> exited{false};

    static void TaskEntry(void* arg) { static_cast<Impl*>(arg)->Loop(); }

    std::expected<void, std::string> Queue(std::unique_ptr<Job> job) {
        TaskHandle_t handle = nullptr;
        bool replaced = false;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (task == nullptr) {
                return std::unexpected(std::string("sequence runner not started"));
            }
            replaced = pending != nullptr;
            pending = std::move(job);
            cancel.store(true);  // the running sequence (if any) stops at its next check
            handle = task;
        }
        xTaskNotifyGive(handle);
        if (replaced) {
            ESP_LOGI(TAG, "Dropped a queued sequence that had not started yet");
        }
        return {};
    }

    std::unique_ptr<Job> TakeJob() {
        std::lock_guard<std::mutex> lock(mutex);
        std::unique_ptr<Job> job = std::move(pending);
        running = job != nullptr;
        current_name = job != nullptr ? job->name : std::string();
        if (job != nullptr) {
            cancel.store(false);
        }
        return job;
    }

    void Loop() {
        while (!quit.load()) {
            std::unique_ptr<Job> job = TakeJob();
            if (job == nullptr) {
                ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
                continue;
            }
            if (job->load_saved && !Resolve(*job)) {
                continue;
            }
            Execute(*job);
        }
        exited.store(true);  // last access to this object
        vTaskDelete(nullptr);
    }

    bool Cancelled() const { return cancel.load() || quit.load(); }

    // Sleeps up to `ms`, returning early when Run()/Stop() notify the task.
    void Pause(int ms) {
        TickType_t ticks = pdMS_TO_TICKS(ms);
        ulTaskNotifyTake(pdTRUE, ticks > 0 ? ticks : 1);
    }

    // NVS copy first, then the built-in sequence. Saved copies were validated before they
    // were stored; an invalid one (older format, corruption) falls back to the built-in.
    bool Resolve(Job& job) {
        std::string json;
        if (HelmetStore::GetInstance().GetBlob(StoreKey(job.name).c_str(), json)) {
            seq::ValidationResult result = seq::Validate(json);
            if (result.ok) {
                job.sequence = std::move(result.sequence);
                return true;
            }
            ESP_LOGE(TAG, "Saved sequence %s is invalid (%s)", job.name.c_str(),
                     result.error.c_str());
        }
        const char* builtin = SequenceRunner::BuiltinSequence(job.name);
        if (builtin != nullptr) {
            seq::ValidationResult result = seq::Validate(builtin);
            if (result.ok) {
                job.sequence = std::move(result.sequence);
                return true;
            }
            ESP_LOGE(TAG, "Built-in sequence %s is invalid (%s)", job.name.c_str(),
                     result.error.c_str());
            return false;
        }
        ESP_LOGE(TAG, "Sequence %s not found", job.name.c_str());
        return false;
    }

    void Execute(const Job& job) {
        const char* label = job.name.empty() ? "(anonymous)" : job.name.c_str();
        const auto& steps = job.sequence.steps;
        ESP_LOGI(TAG, "Start %s: %u steps, ~%d ms", label, static_cast<unsigned>(steps.size()),
                 job.sequence.estimated_ms);
        const int64_t started_ms = NowMs();
        for (size_t i = 0; i < steps.size(); ++i) {
            if (Cancelled() || !RunStep(steps[i])) {
                // Safe state: servos hold where they are, lights keep their brightness.
                mask.Stop();
                output.StopEffect();
                ESP_LOGI(TAG, "Stopped %s at step %u/%u", label, static_cast<unsigned>(i + 1),
                         static_cast<unsigned>(steps.size()));
                return;
            }
        }
        ESP_LOGI(TAG, "Done %s in %d ms", label, static_cast<int>(NowMs() - started_ms));
    }

    // Returns false when the sequence was cancelled during the step.
    bool RunStep(const seq::Step& step) {
        switch (step.type) {
            case seq::StepType::kMask:
                if (step.mask_by_action && step.mask_position == helmet::kPositionMax) {
                    mask.Open(step.speed);
                } else if (step.mask_by_action) {
                    mask.Close(step.speed);
                } else {
                    mask.SetPosition(step.mask_position, step.speed);
                }
                return WaitMask(step.mask_position, kStartGraceMs);
            case seq::StepType::kServo:
                mask.MoveServo(step.servo_id, step.angle, step.duration_ms);
                // An instant move (one controller tick) may never be seen as "moving".
                return WaitMask(-1, step.duration_ms > kCancelTickMs
                                        ? std::min(kStartGraceMs, step.duration_ms)
                                        : 0);
            case seq::StepType::kEyes:
                if (!step.eyes_effect) {
                    output.SetEyes(step.eyes_on, step.brightness);
                    return true;
                }
                if (!output.EyesEffect(step.effect, step.duration_ms)) {
                    ESP_LOGW(TAG, "Eye effect %s rejected", step.effect.c_str());
                    return true;
                }
                // breathe runs until the next eyes command and counts as idle.
                return step.effect == "breathe" ? true : WaitEffect(step.effect, step.duration_ms);
            case seq::StepType::kOutput:
                output.SetOutput(step.output_on);
                return true;
            case seq::StepType::kDelay:
                return Sleep(step.delay_ms);
            case seq::StepType::kDisplay:
                // Display methods lock internally, so the HUD is called from this task
                // directly (Application::Schedule can stall while a channel opens).
                if (show_info) {
                    show_info(step.title, step.text, step.seconds);
                } else {
                    ESP_LOGW(TAG, "No display, skipping display step");
                }
                return true;
            case seq::StepType::kSound:
                if (!play_sound) {
                    ESP_LOGW(TAG, "No sound player, skipping sound %s", step.sound.c_str());
                } else if (!play_sound(step.sound)) {
                    ESP_LOGW(TAG, "Sound %s not available", step.sound.c_str());
                }
                return true;
        }
        return true;
    }

    // Waits for the mask motion just commanded. The controller task may pick the command
    // up a tick later, so first wait (at most grace_ms) until it reports the motion or the
    // mask already sits at `target` (-1 = no target, servo step), then until it is idle.
    bool WaitMask(int target, int grace_ms) {
        const int64_t start_ms = NowMs();
        while (grace_ms > 0) {
            if (Cancelled()) {
                return false;
            }
            MaskState state = mask.GetState();
            if (state.kind == MaskState::Kind::kMoving) {
                break;
            }
            if (target >= 0 && state.position == target) {
                return true;  // nothing to move
            }
            if (NowMs() - start_ms >= grace_ms) {
                break;
            }
            Pause(kPollMs);
        }
        while (!mask.WaitIdle(kCancelTickMs)) {
            if (Cancelled()) {
                return false;
            }
            if (NowMs() - start_ms > kMotionTimeoutMs) {
                ESP_LOGW(TAG, "Mask still moving after %d ms, continuing", kMotionTimeoutMs);
                return true;
            }
        }
        return true;
    }

    // Same two phases for a finite eye effect.
    bool WaitEffect(const std::string& name, int duration_ms) {
        const int64_t start_ms = NowMs();
        const int grace_ms = std::min(kStartGraceMs, duration_ms);
        while (true) {
            if (Cancelled()) {
                return false;
            }
            if (output.GetState().eyes_effect == name || NowMs() - start_ms >= grace_ms) {
                break;
            }
            Pause(kPollMs);
        }
        const int64_t limit_ms = static_cast<int64_t>(duration_ms) + kEffectSlackMs;
        while (!output.WaitIdle(kCancelTickMs)) {
            if (Cancelled()) {
                return false;
            }
            if (NowMs() - start_ms > limit_ms) {
                ESP_LOGW(TAG, "Eye effect %s still running after %d ms, continuing", name.c_str(),
                         static_cast<int>(limit_ms));
                return true;
            }
        }
        return true;
    }

    // Delay step in chunks of at most 20 ms; Run()/Stop() wake it immediately.
    bool Sleep(int ms) {
        const int64_t deadline_us = esp_timer_get_time() + static_cast<int64_t>(ms) * 1000;
        while (true) {
            if (Cancelled()) {
                return false;
            }
            const int64_t remaining_us = deadline_us - esp_timer_get_time();
            if (remaining_us <= 0) {
                return true;
            }
            const int chunk_ms =
                static_cast<int>(std::min<int64_t>((remaining_us + 999) / 1000, kCancelTickMs));
            Pause(chunk_ms);
        }
    }
};

SequenceRunner::SequenceRunner(MaskController& mask, OutputController& output, ShowInfoFn show_info,
                               PlaySoundFn play_sound)
    : impl_(new Impl(mask, output, std::move(show_info), std::move(play_sound))) {}

SequenceRunner::~SequenceRunner() {
    TaskHandle_t handle = nullptr;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        handle = impl_->task;
        impl_->pending.reset();
        impl_->quit.store(true);
        impl_->cancel.store(true);
    }
    if (handle != nullptr) {
        xTaskNotifyGive(handle);
        for (int i = 0; i < 100 && !impl_->exited.load(); ++i) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        if (!impl_->exited.load()) {
            ESP_LOGE(TAG, "Runner task did not exit, leaking its state");
            return;
        }
    }
    delete impl_;
}

void SequenceRunner::Start() {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (impl_->task != nullptr) {
        return;
    }
    if (xTaskCreate(&Impl::TaskEntry, "seq_runner", kTaskStackBytes, impl_, kTaskPriority,
                    &impl_->task) != pdPASS) {
        impl_->task = nullptr;
        ESP_LOGE(TAG, "Failed to create the seq_runner task");
        return;
    }
    ESP_LOGI(TAG, "Sequence runner started");
}

std::expected<void, std::string> SequenceRunner::Run(const std::string& steps_json,
                                                     const std::string& name) {
    if (steps_json.size() > helmet::kMaxSequenceJsonBytes) {
        return std::unexpected(std::string(kTooLongError));
    }
    seq::ValidationResult result = seq::Validate(steps_json);
    if (!result.ok) {
        ESP_LOGW(TAG, "Rejected sequence: %s", result.error.c_str());
        return std::unexpected(std::move(result.error));
    }
    std::unique_ptr<Impl::Job> job(new (std::nothrow) Impl::Job());
    if (job == nullptr) {
        return std::unexpected(std::string("out of memory"));
    }
    job->sequence = std::move(result.sequence);
    job->name = name;
    return impl_->Queue(std::move(job));
}

std::expected<void, std::string> SequenceRunner::RunSaved(const std::string& name) {
    if (!helmet::IsValidSequenceName(name)) {
        return std::unexpected(std::string(kInvalidNameError));
    }
    // Buttons call this on the esp_timer task: built-ins need no NVS access here, the
    // runner task resolves the saved copy itself.
    if (BuiltinSequence(name) == nullptr &&
        !HelmetStore::GetInstance().Exists(StoreKey(name).c_str())) {
        return std::unexpected("unknown sequence " + name);
    }
    std::unique_ptr<Impl::Job> job(new (std::nothrow) Impl::Job());
    if (job == nullptr) {
        return std::unexpected(std::string("out of memory"));
    }
    job->name = name;
    job->load_saved = true;
    return impl_->Queue(std::move(job));
}

std::expected<void, std::string> SequenceRunner::Save(const std::string& name,
                                                      const std::string& steps_json) {
    if (!helmet::IsValidSequenceName(name)) {
        return std::unexpected(std::string(kInvalidNameError));
    }
    if (steps_json.size() > helmet::kMaxSequenceJsonBytes) {
        return std::unexpected(std::string(kTooLongError));
    }
    seq::ValidationResult result = seq::Validate(steps_json);
    if (!result.ok) {
        ESP_LOGW(TAG, "Not saving %s: %s", name.c_str(), result.error.c_str());
        return std::unexpected(std::move(result.error));
    }
    if (!HelmetStore::GetInstance().SetBlob(StoreKey(name).c_str(), steps_json)) {
        ESP_LOGE(TAG, "Failed to store sequence %s", name.c_str());
        return std::unexpected("failed to save sequence " + name +
                               " (storage full or unavailable)");
    }
    ESP_LOGI(TAG, "Saved sequence %s: %u steps, %u bytes", name.c_str(),
             static_cast<unsigned>(result.sequence.steps.size()),
             static_cast<unsigned>(steps_json.size()));
    return {};
}

void SequenceRunner::Stop() {
    TaskHandle_t handle = nullptr;
    bool had_work = false;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        had_work = impl_->running || impl_->pending != nullptr;
        impl_->pending.reset();
        impl_->cancel.store(true);
        handle = impl_->task;
    }
    if (handle != nullptr) {
        xTaskNotifyGive(handle);
    }
    if (had_work) {
        ESP_LOGI(TAG, "Stop requested");
    }
}

bool SequenceRunner::IsRunning() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->running || impl_->pending != nullptr;
}

std::string SequenceRunner::CurrentName() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (impl_->pending != nullptr) {
        return impl_->pending->name;  // replaces the running one at the next check
    }
    return impl_->running ? impl_->current_name : std::string();
}

const char* SequenceRunner::BuiltinSequence(const std::string& name) {
    if (name == "open_mask") {
        return kOpenMaskJson;
    }
    if (name == "close_mask") {
        return kCloseMaskJson;
    }
    return nullptr;
}
